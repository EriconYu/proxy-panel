#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"
require_root

domain="$(installed_value DOMAIN)"
panel_base="$(installed_value PANEL_BASE)"
printf 'Panel URL: https://%s%s\n' "$domain" "$panel_base"
printf 'Nginx configuration: '
if nginx -t >/dev/null 2>&1; then printf 'ok\n'; else printf 'failed\n'; fi

for service in proxy-panel-xray.service proxy-panel-shaping.service proxy-panel.service; do
  printf '%-36s %s\n' "$service" "$(systemctl is-active "$service" 2>/dev/null || true)"
done

python3 - "$INSTALL_ROOT/data/config.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    config = json.load(handle)
devices = config.get("devices", [])
enabled = sum(device.get("enabled") is True for device in devices)
print(f"Devices: {len(devices)} total, {enabled} enabled")
print(f"Initial password changed: {'yes' if config.get('password_changed') else 'no'}")
PY
