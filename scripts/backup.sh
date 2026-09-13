#!/usr/bin/env bash

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib.sh
source "$SCRIPT_DIR/lib.sh"
require_root

[[ -f /opt/proxy-panel/data/config.json && -f /etc/proxy-panel/install.conf ]] || die "Proxy Panel is not installed"
install -d -m 700 "$BACKUP_ROOT"
output="${1:-$BACKUP_ROOT/proxy-panel-$(date -u +%Y%m%dT%H%M%SZ).tar.gz}"
[[ "$output" == /* ]] || output="$(pwd)/$output"
[[ ! -e "$output" ]] || die "backup already exists: $output"
temporary="$(mktemp "${output}.tmp.XXXXXX")"
trap 'rm -f "$temporary"' EXIT

tar -C / -czf "$temporary" \
  opt/proxy-panel/data/config.json \
  etc/proxy-panel/install.conf \
  etc/nginx/sites-available/proxy-panel.conf
chmod 600 "$temporary"
mv "$temporary" "$output"
trap - EXIT
printf 'Backup created: %s\n' "$output"
printf 'It contains password hashes and device subscription secrets; store it securely.\n'
