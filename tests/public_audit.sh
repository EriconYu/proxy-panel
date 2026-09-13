#!/usr/bin/env bash

set -Eeuo pipefail
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

tracked="$(git ls-files)"
grep -qx 'env.conf' <<< "$tracked" && { echo 'env.conf must not be tracked' >&2; exit 1; }
grep -Eq '(^|/)config[.]json$|[.]tar[.]gz$|[.]pem$|[.]key$' <<< "$tracked" && {
  echo 'runtime state, archives, or key material must not be tracked' >&2
  exit 1
}

patterns=(
  '38[.]76[.]202[.]132'
  'see[.]stackbang[.]com'
  'pm[.]stackbang[.]com'
  '@stackbang[.]com'
  'proxy-panel-backups/v1-'
  'value="eric"'
  'default="eric"'
  'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'
)

for pattern in "${patterns[@]}"; do
  if git grep -nEi "$pattern" -- . ':!tests/public_audit.sh' ':!tests/test_app.py'; then
    echo "public-safety audit failed for pattern: $pattern" >&2
    exit 1
  fi
done

if git log --format='%ae%n%ce' | grep -Eqi '@stackbang[.]com$'; then
  echo 'public-safety audit found a personal commit email' >&2
  exit 1
fi

git check-ignore -q env.conf || { echo 'env.conf is not ignored' >&2; exit 1; }
grep -q '^DOMAIN=proxy[.]example[.]com$' env.conf.example
grep -q '^ADMIN_USERNAME=admin$' env.conf.example
grep -q '^ADMIN_PASSWORD=111111$' env.conf.example
printf 'Public-safety audit passed.\n'
