#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"
require_root

archive="${1:-}"
[[ -n "$archive" && -f "$archive" ]] || die "usage: sudo scripts/restore.sh BACKUP.tar.gz"
[[ -f /opt/proxy-panel/app/app.py ]] || die "install Proxy Panel before restoring a backup"

temporary="$(mktemp -d /tmp/proxy-panel-restore.XXXXXX)"
restore_started=false
restore_complete=false
cleanup() {
  local status="$?"
  if [[ "$restore_started" == true && "$restore_complete" != true ]]; then
    set +e
    install -m 600 -o proxy-panel -g proxy-panel "$temporary/current-config.json" /opt/proxy-panel/data/config.json
    install -m 600 "$temporary/current-install.conf" /etc/proxy-panel/install.conf
    install -m 644 "$temporary/current-nginx.conf" /etc/nginx/sites-available/proxy-panel.conf
    nginx -t >/dev/null 2>&1
    systemctl start proxy-panel-apply.service >/dev/null 2>&1
    systemctl restart proxy-panel-shaping.service proxy-panel.service >/dev/null 2>&1
    printf 'Restore failed; the pre-restore configuration was put back.\n' >&2
  fi
  rm -rf "$temporary"
  exit "$status"
}
trap cleanup EXIT

python3 "$SCRIPT_DIR/restore_archive.py" "$archive" "$temporary"
python3 -m json.tool "$temporary/opt/proxy-panel/data/config.json" >/dev/null
PYTHONPATH=/opt/proxy-panel python3 - "$temporary/opt/proxy-panel/data/config.json" <<'PY'
import json
import sys
from app import apply_config

with open(sys.argv[1], encoding="utf-8") as handle:
    apply_config.validated_devices(json.load(handle))
PY

"$SCRIPT_DIR/backup.sh" >/dev/null
install -m 600 /opt/proxy-panel/data/config.json "$temporary/current-config.json"
install -m 600 /etc/proxy-panel/install.conf "$temporary/current-install.conf"
install -m 600 /etc/nginx/sites-available/proxy-panel.conf "$temporary/current-nginx.conf"
restore_started=true
systemctl stop proxy-panel.service
install -m 600 -o proxy-panel -g proxy-panel "$temporary/opt/proxy-panel/data/config.json" /opt/proxy-panel/data/config.json
install -m 600 "$temporary/etc/proxy-panel/install.conf" /etc/proxy-panel/install.conf
install -m 644 "$temporary/etc/nginx/sites-available/proxy-panel.conf" /etc/nginx/sites-available/proxy-panel.conf
nginx -t
systemctl start proxy-panel-apply.service
systemctl restart proxy-panel-shaping.service proxy-panel.service
restore_complete=true
printf 'Backup restored. Run %s/status.sh to verify the services.\n' "$SCRIPT_DIR"
