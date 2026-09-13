#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"
require_root

[[ -f /opt/proxy-panel/data/config.json ]] || die "Proxy Panel is not installed"
python3 -m py_compile "$PROJECT_ROOT/app/app.py" "$PROJECT_ROOT/app/apply_config.py"
backup_output="$("$SCRIPT_DIR/backup.sh")"
printf '%s\n' "$backup_output"

install -m 755 "$PROJECT_ROOT/app/app.py" /opt/proxy-panel/app/app.py
install -m 755 "$PROJECT_ROOT/app/apply_config.py" /opt/proxy-panel/app/apply_config.py
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel.service" /etc/systemd/system/proxy-panel.service
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel-xray.service" /etc/systemd/system/proxy-panel-xray.service
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel-apply.service" /etc/systemd/system/proxy-panel-apply.service
install -m 644 "$PROJECT_ROOT/systemd/proxy-panel-shaping.service" /etc/systemd/system/proxy-panel-shaping.service

systemctl daemon-reload
systemctl start proxy-panel-apply.service
systemctl restart proxy-panel-shaping.service proxy-panel.service
systemctl is-active --quiet proxy-panel-xray.service
systemctl is-active --quiet proxy-panel.service
printf 'Proxy Panel application updated successfully.\n'
