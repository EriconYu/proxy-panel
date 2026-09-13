# Proxy Panel

Self-hosted, single-admin management panel for per-device Clash-compatible VLESS/WebSocket subscriptions. It provides a QR code and subscription URL for each device, persistent upload/download accounting, traffic quotas, bidirectional rate limits, and immediate subscription revocation.

Documentation: **English** | [简体中文](docs/README.zh-CN.md) | [繁體中文](docs/README.zh-TW.md)

## What it installs

- A dependency-free Python management application bound to `127.0.0.1`
- A dedicated Xray binary and `proxy-panel-xray.service`; an existing `xray.service` is not reused
- Dedicated Nginx site and generated device snippet
- Dedicated system users, configuration, runtime state, and systemd units
- Optional Let's Encrypt issuance, or references to an existing certificate

The installer targets Ubuntu 22.04/24.04 on AMD64 or ARM64. Only ports 80 and 443 must be publicly reachable. It refuses an existing installation path, an existing Nginx owner for the configured domain, or occupied local application ports.

## Install

Point a domain directly at the server first, then run:

```bash
git clone https://github.com/EriconYu/proxy-panel.git
cd proxy-panel
cp env.conf.example env.conf
chmod 600 env.conf
$EDITOR env.conf
sudo ./install.sh
```

`env.conf` is ignored by Git. The tracked example intentionally uses the first-login credentials `admin / 111111`. These are public and must be changed immediately in the panel. The warning remains visible until the password is changed through the panel.

Configuration fields:

| Setting | Meaning |
| --- | --- |
| `DOMAIN` | Dedicated public hostname; do not reuse a hostname already owned by another Nginx site |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | First-login credentials; the password may contain spaces and shell symbols, but not a newline |
| `TLS_MODE` | `certbot` or `existing` |
| `LETSENCRYPT_EMAIL` | Required in `certbot` mode |
| `TLS_CERT_PATH`, `TLS_KEY_PATH` | Required in `existing` mode |
| `PANEL_PORT`, `XRAY_API_PORT` | Unoccupied local TCP ports |
| `DEVICE_PORT_MIN`, `DEVICE_PORT_MAX` | Dedicated local range, at most 1,000 ports; the panel supports up to 100 devices |
| `CHECK_DNS` | DNS resolution preflight; disabling it does not bypass Certbot validation |
| `XRAY_VERSION`, checksums | Pinned release and mandatory AMD64/ARM64 integrity hashes |

For `TLS_MODE=certbot`, the domain must resolve to this server and inbound TCP 80/443 must work. For `TLS_MODE=existing`, provide readable absolute certificate and key paths; the installer references them and never copies or owns them.

## Operate

```bash
sudo scripts/status.sh
sudo scripts/backup.sh
sudo scripts/restore.sh /var/backups/proxy-panel/proxy-panel-TIMESTAMP.tar.gz
git pull --ff-only && sudo scripts/update.sh
sudo scripts/uninstall.sh
```

Updates back up state before replacing application and service files. They do not silently change Xray or deployment settings. Uninstall creates a backup by default and retains packages, backups, certificates, and any unrelated Nginx configuration.

## Device identity

ClashMi does not send trustworthy phone model, OS identity, or a stable hardware identifier through a proxy subscription. Proxy Panel therefore treats each generated subscription as one device identity. Create a separate device and import its unique QR code on each phone. If the same subscription is copied to several phones, they are deliberately counted and controlled as one device and cannot be separated later from network traffic alone.

## Security and risk boundaries

- Use this software only on infrastructure you own or are authorized to administer, and comply with local law and provider terms. It does not provide legal advice or guaranteed anonymity.
- VLESS over WebSocket is retained for broad Clash compatibility. Networks can still fingerprint, throttle, or block it. It is not a promise of censorship resistance.
- The admin password and every subscription URL are bearer secrets. HTTPS is mandatory. The hidden panel path is defense in depth, not authentication.
- `config.json` and backups contain password hashes, device UUIDs, and subscription tokens. They are mode `0600`; backups must be protected separately.
- The web process cannot run arbitrary privileged commands. A root helper validates a narrow device schema, writes only the owned Xray/Nginx/runtime files, manages filters on dedicated loopback ports, and restarts only owned services plus Nginx.
- Rate limits use Linux traffic control on each device's dedicated loopback TCP port. They do not intentionally match other public or loopback traffic. Policing may drop excess packets rather than queue them.
- Quotas are checked every 10 seconds, so a device can exceed its quota slightly before revocation. Traffic counters represent Xray user traffic, not a forensic billing system.
- Device changes restart the dedicated Xray core and briefly interrupt other Proxy Panel devices. Nginx reloads are graceful but global to that Nginx instance. Credential-bearing request paths are excluded from application and Nginx access logs.
- Installation installs OS packages and reloads Nginx. It does not change firewall rules. Review the scripts and take a server snapshot before production use.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and the complete trust model.

## Layout

| Path | Purpose |
| --- | --- |
| `/opt/proxy-panel` | Application, Xray binary, and secret application state |
| `/etc/proxy-panel` | Generated Xray and non-secret install/runtime settings |
| `/etc/nginx/sites-available/proxy-panel.conf` | Owned Nginx virtual host |
| `/etc/nginx/snippets/proxy-panel-devices.conf` | Validated generated device routes |
| `/var/lib/proxy-panel-runtime` | Traffic-control state |
| `/var/backups/proxy-panel` | Mode `0700` backup directory |

## Development

```bash
python3 -m unittest discover -s tests -v
bash tests/public_audit.sh
bash -n install.sh scripts/*.sh
```

The application uses only the Python standard library. Runtime QR rendering uses the system `qrencode` command.

## License

MIT
