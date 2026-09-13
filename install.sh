#!/usr/bin/env bash

set -Eeuo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$PROJECT_ROOT/scripts/lib.sh"

require_root
ENV_FILE="${1:-$PROJECT_ROOT/env.conf}"
load_env "$ENV_FILE"
validate_config
env_permissions="$(stat -c '%a' "$ENV_FILE")"
(( (8#$env_permissions & 077) == 0 )) || die "$ENV_FILE must not be readable or writable by group/others (use chmod 600)"

[[ -r /etc/os-release ]] || die "cannot identify this operating system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == ubuntu && ( "${VERSION_ID:-}" == 22.04 || "${VERSION_ID:-}" == 24.04 ) ]] || \
  die "supported systems are Ubuntu 22.04 and 24.04"

for path in "$INSTALL_ROOT" "$CONFIG_ROOT" "$RUNTIME_ROOT" "$NGINX_SITE" "$NGINX_ENABLED" \
  /etc/systemd/system/proxy-panel.service /etc/systemd/system/proxy-panel-xray.service; do
  [[ ! -e "$path" && ! -L "$path" ]] || die "existing installation artifact found: $path"
done
getent passwd proxy-panel >/dev/null && die "system user proxy-panel already exists"
getent passwd proxy-panel-xray >/dev/null && die "system user proxy-panel-xray already exists"
getent group proxy-panel >/dev/null && die "system group proxy-panel already exists"
getent group proxy-panel-xray >/dev/null && die "system group proxy-panel-xray already exists"

if command -v nginx >/dev/null 2>&1 && nginx -T 2>/dev/null | grep -Eq "server_name[[:space:]]+${DOMAIN//./\\.}([[:space:];])"; then
  die "an Nginx server block already owns $DOMAIN"
fi

if [[ "$CHECK_DNS" == true ]]; then
  getent ahosts "$DOMAIN" >/dev/null 2>&1 || die "$DOMAIN does not resolve; fix DNS or set CHECK_DNS=false"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
packages=(ca-certificates curl iproute2 nginx openssl python3 qrencode sudo unzip)
[[ "$TLS_MODE" == certbot ]] && packages+=(certbot)
apt-get install -y "${packages[@]}"

listening_ports="$(ss -H -ltn | awk '{addr=$4; sub(/^.*:/, "", addr); if (addr ~ /^[0-9]+$/) print addr}')"
if awk -v panel="$PANEL_PORT" -v api="$XRAY_API_PORT" -v low="$DEVICE_PORT_MIN" -v high="$DEVICE_PORT_MAX" \
  '$1 == panel || $1 == api || ($1 >= low && $1 <= high) { found=1 } END { exit !found }' <<< "$listening_ports"; then
  die "one or more configured local ports are already in use"
fi

case "$(uname -m)" in
  x86_64|amd64) xray_asset=Xray-linux-64.zip; xray_checksum="$XRAY_SHA256_AMD64" ;;
  aarch64|arm64) xray_asset=Xray-linux-arm64-v8a.zip; xray_checksum="$XRAY_SHA256_ARM64" ;;
  *) die "unsupported CPU architecture: $(uname -m)" ;;
esac

download_dir="$(mktemp -d /tmp/proxy-panel-install.XXXXXX)"
installation_complete=false
cleanup() {
  local status=$?
  if [[ "$installation_complete" != true ]]; then
    set +e
    systemctl disable --now proxy-panel.service proxy-panel-shaping.service proxy-panel-xray.service >/dev/null 2>&1
    rm -f /etc/systemd/system/proxy-panel.service \
      /etc/systemd/system/proxy-panel-shaping.service \
      /etc/systemd/system/proxy-panel-apply.service \
      /etc/systemd/system/proxy-panel-xray.service \
      /etc/sudoers.d/proxy-panel-apply \
      /etc/nginx/sites-enabled/proxy-panel.conf \
      /etc/nginx/sites-available/proxy-panel.conf \
      /etc/nginx/snippets/proxy-panel-devices.conf \
      /etc/letsencrypt/renewal-hooks/deploy/proxy-panel-nginx
    rm -rf /opt/proxy-panel /etc/proxy-panel /var/lib/proxy-panel-runtime /var/lib/proxy-panel-acme
    systemctl daemon-reload >/dev/null 2>&1
    if nginx -t >/dev/null 2>&1; then
      systemctl reload nginx.service >/dev/null 2>&1
    fi
    userdel proxy-panel >/dev/null 2>&1
    userdel proxy-panel-xray >/dev/null 2>&1
    groupdel proxy-panel >/dev/null 2>&1
    groupdel proxy-panel-xray >/dev/null 2>&1
    printf 'Installation failed; Proxy Panel-owned files and accounts were rolled back.\n' >&2
  fi
  rm -rf "$download_dir"
  exit "$status"
}
trap cleanup EXIT

curl -fsSL --connect-timeout 20 --max-time 180 \
  "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${xray_asset}" \
  -o "$download_dir/xray.zip"
printf '%s  %s\n' "$xray_checksum" "$download_dir/xray.zip" | sha256sum -c -
unzip -q "$download_dir/xray.zip" xray -d "$download_dir"

groupadd --system proxy-panel
useradd --system --gid proxy-panel --home-dir "$INSTALL_ROOT" --shell /usr/sbin/nologin proxy-panel
groupadd --system proxy-panel-xray
useradd --system --gid proxy-panel-xray --home-dir /nonexistent --shell /usr/sbin/nologin proxy-panel-xray

install -d -m 755 "$INSTALL_ROOT/app" "$INSTALL_ROOT/bin"
install -d -m 700 -o proxy-panel -g proxy-panel "$INSTALL_ROOT/data"
install -d -m 750 "$CONFIG_ROOT"
install -d -m 700 "$RUNTIME_ROOT" "$BACKUP_ROOT"
install -d -m 755 /var/lib/proxy-panel-acme
install -m 755 "$PROJECT_ROOT/app/app.py" "$INSTALL_ROOT/app/app.py"
install -m 755 "$PROJECT_ROOT/app/apply_config.py" "$INSTALL_ROOT/app/apply_config.py"
install -m 755 "$download_dir/xray" "$INSTALL_ROOT/bin/xray"

panel_base="/net-admin-$(openssl rand -hex 8)/"
printf '%s\n' "$ADMIN_PASSWORD" | runuser -u proxy-panel -- env \
  PROXY_PANEL_CONFIG="$INSTALL_ROOT/data/config.json" \
  /usr/bin/python3 "$INSTALL_ROOT/app/app.py" init --password-stdin \
  --host "$DOMAIN" --username "$ADMIN_USERNAME" --base-path "$panel_base" \
  --xray-api-port "$XRAY_API_PORT" --device-port-min "$DEVICE_PORT_MIN" --device-port-max "$DEVICE_PORT_MAX"

install -m 644 "$PROJECT_ROOT/systemd/proxy-panel.service" /etc/systemd/system/proxy-panel.service
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel-xray.service" /etc/systemd/system/proxy-panel-xray.service
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel-apply.service" /etc/systemd/system/proxy-panel-apply.service
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel-shaping.service" /etc/systemd/system/proxy-panel-shaping.service

install -m 640 -o root -g proxy-panel /dev/null "$CONFIG_ROOT/runtime.env"
cat > "$CONFIG_ROOT/runtime.env" <<RUNTIME
PYTHONUNBUFFERED=1
PROXY_PANEL_CONFIG=$INSTALL_ROOT/data/config.json
PROXY_PANEL_XRAY_BIN=$INSTALL_ROOT/bin/xray
PROXY_PANEL_XRAY_CONFIG=$CONFIG_ROOT/xray.json
PROXY_PANEL_NGINX_SNIPPET=$NGINX_SNIPPET
PROXY_PANEL_TC_STATE=$RUNTIME_ROOT/tc.json
PROXY_PANEL_XRAY_GROUP=proxy-panel-xray
PROXY_PANEL_XRAY_SERVICE=proxy-panel-xray.service
PROXY_PANEL_LISTEN_PORT=$PANEL_PORT
RUNTIME

install -m 600 /dev/null "$CONFIG_ROOT/install.conf"
cat > "$CONFIG_ROOT/install.conf" <<INSTALL_CONFIG
DOMAIN=$DOMAIN
TLS_MODE=$TLS_MODE
TLS_CERT_PATH=$TLS_CERT_PATH
TLS_KEY_PATH=$TLS_KEY_PATH
PANEL_PORT=$PANEL_PORT
XRAY_API_PORT=$XRAY_API_PORT
DEVICE_PORT_MIN=$DEVICE_PORT_MIN
DEVICE_PORT_MAX=$DEVICE_PORT_MAX
XRAY_VERSION=$XRAY_VERSION
XRAY_SHA256_AMD64=$XRAY_SHA256_AMD64
XRAY_SHA256_ARM64=$XRAY_SHA256_ARM64
PANEL_BASE=$panel_base
INSTALL_CONFIG

install -m 440 /dev/null /etc/sudoers.d/proxy-panel-apply
cat > /etc/sudoers.d/proxy-panel-apply <<'SUDOERS'
proxy-panel ALL=(root) NOPASSWD: /usr/bin/systemctl --wait restart proxy-panel-apply.service
SUDOERS
visudo -cf /etc/sudoers.d/proxy-panel-apply >/dev/null

install -m 644 /dev/null "$NGINX_SNIPPET"
ln -s "$NGINX_SITE" "$NGINX_ENABLED"

if [[ "$TLS_MODE" == certbot ]]; then
  sed "s/__DOMAIN__/$DOMAIN/g" "$PROJECT_ROOT/nginx/acme.conf.template" > "$NGINX_SITE"
  nginx -t
  systemctl reload nginx
  certbot certonly --webroot -w /var/lib/proxy-panel-acme -d "$DOMAIN" \
    --email "$LETSENCRYPT_EMAIL" --agree-tos --non-interactive --keep-until-expiring
  TLS_CERT_PATH="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
  TLS_KEY_PATH="/etc/letsencrypt/live/$DOMAIN/privkey.pem"
  sed -i "s|^TLS_CERT_PATH=.*|TLS_CERT_PATH=$TLS_CERT_PATH|; s|^TLS_KEY_PATH=.*|TLS_KEY_PATH=$TLS_KEY_PATH|" "$CONFIG_ROOT/install.conf"
  install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
  install -m 755 /dev/null /etc/letsencrypt/renewal-hooks/deploy/proxy-panel-nginx
  cat > /etc/letsencrypt/renewal-hooks/deploy/proxy-panel-nginx <<'HOOK'
#!/usr/bin/env bash
systemctl reload nginx.service
HOOK
fi

sed -e "s/__DOMAIN__/$DOMAIN/g" \
  -e "s|__TLS_CERT_PATH__|$TLS_CERT_PATH|g" \
  -e "s|__TLS_KEY_PATH__|$TLS_KEY_PATH|g" \
  -e "s|__PANEL_BASE_NO_SLASH__|${panel_base%/}|g" \
  -e "s|__PANEL_BASE__|$panel_base|g" \
  -e "s/__PANEL_PORT__/$PANEL_PORT/g" \
  "$PROJECT_ROOT/nginx/site.conf.template" > "$NGINX_SITE"

nginx -t
systemctl daemon-reload
systemctl enable proxy-panel-xray.service proxy-panel-shaping.service proxy-panel.service
systemctl start proxy-panel-apply.service
systemctl restart proxy-panel-shaping.service proxy-panel.service
systemctl is-active --quiet proxy-panel-xray.service
systemctl is-active --quiet proxy-panel.service
installation_complete=true

printf '\nInstalled successfully.\nPanel URL: https://%s%s\nUsername: %s\nInitial password: %s\n' \
  "$DOMAIN" "$panel_base" "$ADMIN_USERNAME" "$ADMIN_PASSWORD"
printf 'Change the initial password immediately after signing in.\n'
