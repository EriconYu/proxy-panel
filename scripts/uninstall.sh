#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"
require_root

assume_yes=false
skip_backup=false
for argument in "$@"; do
  case "$argument" in
    --yes) assume_yes=true ;;
    --no-backup) skip_backup=true ;;
    *) die "unknown argument: $argument" ;;
  esac
done

[[ -d /opt/proxy-panel || -d /etc/proxy-panel ]] || die "Proxy Panel is not installed"
if [[ "$assume_yes" != true ]]; then
  read -r -p "Remove Proxy Panel while retaining backups and TLS certificates? [y/N] " answer
  [[ "$answer" == y || "$answer" == Y ]] || exit 0
fi

if [[ "$skip_backup" != true && -f /opt/proxy-panel/data/config.json ]]; then
  "$SCRIPT_DIR/backup.sh"
fi

if [[ -f /var/lib/proxy-panel-runtime/tc.json ]]; then
  while read -r preference handle; do
    [[ "$preference" =~ ^[0-9]+$ && "$handle" =~ ^[0-9]+$ ]] || continue
    tc filter del dev lo egress protocol ip pref "$preference" handle "$handle" flower 2>/dev/null || true
    tc filter del dev lo egress protocol ip pref "$((preference + 1))" handle "$handle" flower 2>/dev/null || true
  done < <(python3 -c 'import json; print("\n".join("{} {}".format(x["preference"], x["handle"]) for x in json.load(open("/var/lib/proxy-panel-runtime/tc.json"))))' 2>/dev/null || true)
fi

systemctl disable --now proxy-panel.service proxy-panel-shaping.service proxy-panel-xray.service 2>/dev/null || true
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
systemctl daemon-reload
if nginx -t >/dev/null; then
  systemctl reload nginx.service || true
fi
userdel proxy-panel 2>/dev/null || true
userdel proxy-panel-xray 2>/dev/null || true
groupdel proxy-panel 2>/dev/null || true
groupdel proxy-panel-xray 2>/dev/null || true

printf 'Proxy Panel was removed. Packages, backups in %s, and TLS certificates were retained.\n' "$BACKUP_ROOT"
