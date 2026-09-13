#!/usr/bin/env bash

set -o errexit
set -o nounset
set -o pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC2034
INSTALL_ROOT=/opt/proxy-panel
# shellcheck disable=SC2034
CONFIG_ROOT=/etc/proxy-panel
# shellcheck disable=SC2034
RUNTIME_ROOT=/var/lib/proxy-panel-runtime
# shellcheck disable=SC2034
BACKUP_ROOT=/var/backups/proxy-panel
# shellcheck disable=SC2034
NGINX_SITE=/etc/nginx/sites-available/proxy-panel.conf
# shellcheck disable=SC2034
NGINX_ENABLED=/etc/nginx/sites-enabled/proxy-panel.conf
# shellcheck disable=SC2034
NGINX_SNIPPET=/etc/nginx/snippets/proxy-panel-devices.conf

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "run this command as root"
}

config_key_allowed() {
  case "$1" in
    DOMAIN|ADMIN_USERNAME|ADMIN_PASSWORD|TLS_MODE|LETSENCRYPT_EMAIL|TLS_CERT_PATH|TLS_KEY_PATH|PANEL_PORT|XRAY_API_PORT|DEVICE_PORT_MIN|DEVICE_PORT_MAX|CHECK_DNS|XRAY_VERSION|XRAY_SHA256_AMD64|XRAY_SHA256_ARM64) return 0 ;;
    *) return 1 ;;
  esac
}

load_env() {
  local env_file="${1:-${PROJECT_ROOT}/env.conf}"
  [[ -f "$env_file" ]] || die "missing $env_file (copy env.conf.example to env.conf first)"

  DOMAIN=''; ADMIN_USERNAME='admin'; ADMIN_PASSWORD='111111'; TLS_MODE='certbot'
  LETSENCRYPT_EMAIL=''; TLS_CERT_PATH=''; TLS_KEY_PATH=''; PANEL_PORT='18080'
  XRAY_API_PORT='10085'; DEVICE_PORT_MIN='11000'; DEVICE_PORT_MAX='11999'; CHECK_DNS='true'
  XRAY_VERSION='v26.3.27'
  XRAY_SHA256_AMD64='23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae'
  XRAY_SHA256_ARM64='4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c'

  local raw key value
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    raw="${raw%$'\r'}"
    [[ -z "$raw" || "$raw" =~ ^[[:space:]]*# ]] && continue
    [[ "$raw" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || die "invalid line in $env_file: $raw"
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    config_key_allowed "$key" || die "unknown setting in $env_file: $key"
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
      value="${BASH_REMATCH[1]}"
    fi
    printf -v "$key" '%s' "$value"
  done < "$env_file"
}

is_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( 10#$1 >= 1024 && 10#$1 <= 65535 ))
}

validate_config() {
  [[ "$DOMAIN" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$ ]] || die "DOMAIN is not a valid hostname"
  [[ "$ADMIN_USERNAME" =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || die "ADMIN_USERNAME is invalid"
  (( ${#ADMIN_PASSWORD} >= 6 && ${#ADMIN_PASSWORD} <= 256 )) || die "ADMIN_PASSWORD must contain 6 to 256 characters"
  [[ "$TLS_MODE" == certbot || "$TLS_MODE" == existing ]] || die "TLS_MODE must be certbot or existing"
  [[ "$CHECK_DNS" == true || "$CHECK_DNS" == false ]] || die "CHECK_DNS must be true or false"
  is_port "$PANEL_PORT" || die "PANEL_PORT is invalid"
  is_port "$XRAY_API_PORT" || die "XRAY_API_PORT is invalid"
  is_port "$DEVICE_PORT_MIN" || die "DEVICE_PORT_MIN is invalid"
  is_port "$DEVICE_PORT_MAX" || die "DEVICE_PORT_MAX is invalid"
  (( 10#$DEVICE_PORT_MIN <= 10#$DEVICE_PORT_MAX )) || die "device port range is reversed"
  (( 10#$DEVICE_PORT_MAX - 10#$DEVICE_PORT_MIN <= 999 )) || die "device port range may contain at most 1000 ports"
  (( 10#$PANEL_PORT < 10#$DEVICE_PORT_MIN || 10#$PANEL_PORT > 10#$DEVICE_PORT_MAX )) || die "PANEL_PORT overlaps the device range"
  (( 10#$XRAY_API_PORT < 10#$DEVICE_PORT_MIN || 10#$XRAY_API_PORT > 10#$DEVICE_PORT_MAX )) || die "XRAY_API_PORT overlaps the device range"
  [[ "$PANEL_PORT" != "$XRAY_API_PORT" ]] || die "PANEL_PORT and XRAY_API_PORT must differ"
  [[ "$XRAY_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "XRAY_VERSION is invalid"
  [[ "$XRAY_SHA256_AMD64" =~ ^[a-f0-9]{64}$ && "$XRAY_SHA256_ARM64" =~ ^[a-f0-9]{64}$ ]] || die "Xray checksums are invalid"

  if [[ "$TLS_MODE" == certbot ]]; then
    [[ "$LETSENCRYPT_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || die "LETSENCRYPT_EMAIL is required for certbot"
  else
    [[ "$TLS_CERT_PATH" == /* && "$TLS_KEY_PATH" == /* ]] || die "existing TLS paths must be absolute"
    [[ "$TLS_CERT_PATH" =~ ^/[A-Za-z0-9._/-]+$ && "$TLS_KEY_PATH" =~ ^/[A-Za-z0-9._/-]+$ ]] || die "TLS paths contain unsupported characters"
    [[ -r "$TLS_CERT_PATH" && -r "$TLS_KEY_PATH" ]] || die "existing TLS certificate or key is not readable"
  fi
}

installed_value() {
  local key="$1"
  [[ -f "$CONFIG_ROOT/install.conf" ]] || die "Proxy Panel is not installed"
  awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; found=1 } END { if (!found) exit 1 }' "$CONFIG_ROOT/install.conf"
}
