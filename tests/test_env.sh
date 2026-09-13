#!/usr/bin/env bash

set -Eeuo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
temporary="$(mktemp -d /tmp/proxy-panel-env-test.XXXXXX)"
marker=/tmp/proxy-panel-env-must-not-execute
trap 'rm -rf "$temporary"; rm -f "$marker"' EXIT
rm -f "$marker"

cat > "$temporary/env.conf" <<'CONFIG'
DOMAIN=proxy.example.com
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$(touch /tmp/proxy-panel-env-must-not-execute)
TLS_MODE=certbot
LETSENCRYPT_EMAIL=admin@example.com
CONFIG

# shellcheck source=scripts/lib.sh
# shellcheck disable=SC2016
source "$repo_root/scripts/lib.sh"
load_env "$temporary/env.conf"
# shellcheck disable=SC2016
[[ "$ADMIN_PASSWORD" == '$(touch /tmp/proxy-panel-env-must-not-execute)' ]]
[[ ! -e "$marker" ]]
validate_config
printf 'Environment parser test passed.\n'
