#!/usr/bin/env python3
import argparse
import grp
import json
import os
import re
import subprocess
import tempfile
import uuid as uuid_module


CONFIG_PATH = os.environ.get("PROXY_PANEL_CONFIG", "/opt/proxy-panel/data/config.json")
XRAY_CONFIG = os.environ.get("PROXY_PANEL_XRAY_CONFIG", "/etc/xray/config.json")
NGINX_SNIPPET = os.environ.get("PROXY_PANEL_NGINX_SNIPPET", "/etc/nginx/snippets/proxy-panel-devices.conf")
TC_STATE = os.environ.get("PROXY_PANEL_TC_STATE", "/var/lib/proxy-panel-runtime/tc.json")
XRAY_GROUP = os.environ.get("PROXY_PANEL_XRAY_GROUP", "proxy-panel-xray")
XRAY_BIN = os.environ.get("PROXY_PANEL_XRAY_BIN", "/opt/proxy-panel/bin/xray")
XRAY_SERVICE = os.environ.get("PROXY_PANEL_XRAY_SERVICE", "proxy-panel-xray.service")
DEFAULT_PORT_MIN = 11000
DEFAULT_PORT_MAX = 11999
DEFAULT_API_PORT = 10085


def run(command, check=True):
    return subprocess.run(command, text=True, capture_output=True, check=check)


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write(path, content, mode=0o640):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".proxy-panel-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validated_devices(config):
    port_min = int(config.get("device_port_min", DEFAULT_PORT_MIN))
    port_max = int(config.get("device_port_max", DEFAULT_PORT_MAX))
    if not 1024 <= port_min <= port_max <= 65535:
        raise ValueError("invalid reserved port range")
    devices = config.get("devices")
    if not isinstance(devices, list) or len(devices) > 100:
        raise ValueError("invalid device list")
    seen_ids = set()
    seen_ports = set()
    seen_paths = set()
    seen_emails = set()
    output = []
    for raw in devices:
        device_id = raw.get("id", "")
        email = raw.get("email", "")
        ws_path = raw.get("ws_path", "")
        if not re.fullmatch(r"[a-f0-9]{12}", device_id):
            raise ValueError("invalid device id")
        if not re.fullmatch(r"device-[a-f0-9]{12}", email):
            raise ValueError("invalid device email")
        if not re.fullmatch(r"/edge-[a-f0-9]{24}", ws_path):
            raise ValueError("invalid websocket path")
        try:
            parsed_uuid = str(uuid_module.UUID(raw.get("uuid", "")))
            port = int(raw.get("port"))
            speed_mbps = float(raw.get("speed_mbps", 0))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("invalid device value") from exc
        if not port_min <= port <= port_max:
            raise ValueError("device port outside reserved range")
        if not 0 <= speed_mbps <= 1000:
            raise ValueError("invalid speed limit")
        if device_id in seen_ids or port in seen_ports or ws_path in seen_paths or email in seen_emails:
            raise ValueError("duplicate device identity")
        seen_ids.add(device_id)
        seen_ports.add(port)
        seen_paths.add(ws_path)
        seen_emails.add(email)
        output.append({
            "id": device_id,
            "email": email,
            "ws_path": ws_path,
            "uuid": parsed_uuid,
            "port": port,
            "speed_mbps": speed_mbps,
            "enabled": raw.get("enabled") is True,
        })
    return output


def build_xray(devices, api_port=DEFAULT_API_PORT):
    inbounds = [{
        "tag": "api-in",
        "listen": "127.0.0.1",
        "port": api_port,
        "protocol": "dokodemo-door",
        "settings": {"address": "127.0.0.1"},
    }]
    for device in devices:
        if not device["enabled"]:
            continue
        inbounds.append({
            "tag": f'in-{device["id"]}',
            "listen": "127.0.0.1",
            "port": device["port"],
            "protocol": "vless",
            "settings": {
                "clients": [{
                    "id": device["uuid"],
                    "email": device["email"],
                    "level": 0,
                }],
                "decryption": "none",
            },
            "streamSettings": {
                "network": "ws",
                "wsSettings": {"path": f'/internal-{device["id"]}'},
            },
        })
    payload = {
        "log": {"loglevel": "warning"},
        "stats": {},
        "policy": {"levels": {"0": {"statsUserUplink": True, "statsUserDownlink": True}}},
        "api": {"tag": "api", "services": ["StatsService"]},
        "inbounds": inbounds,
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "rules": [{"type": "field", "inboundTag": ["api-in"], "outboundTag": "api"}]
        },
    }
    return json.dumps(payload, ensure_ascii=True, indent=2) + "\n"


def build_nginx(devices):
    blocks = ["# Generated by proxy-panel-apply.\n"]
    for device in devices:
        if not device["enabled"]:
            continue
        blocks.append(f'''location = {device["ws_path"]} {{
    proxy_pass http://127.0.0.1:{device["port"]}/internal-{device["id"]};
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_read_timeout 86400s;
    proxy_send_timeout 86400s;
    proxy_buffering off;
    access_log off;
}}
''')
    return "\n".join(blocks)


def delete_tc_record(record):
    preference = int(record["preference"])
    handle = int(record["handle"])
    for direction in (0, 1):
        run(["/usr/sbin/tc", "filter", "del", "dev", "lo", "egress", "protocol", "ip",
             "pref", str(preference + direction), "handle", str(handle), "flower"], check=False)


def add_tc_record(record):
    port = int(record["port"])
    preference = int(record["preference"])
    handle = int(record["handle"])
    rate_kbps = int(record["rate_kbps"])
    burst_kb = max(64, min(4096, rate_kbps // 8))
    common = [
        "/usr/sbin/tc", "filter", "add", "dev", "lo", "egress", "protocol", "ip",
    ]
    action = [
        "action", "police", "rate", f"{rate_kbps}kbit", "burst", f"{burst_kb}kb",
        "mtu", "64kb", "conform-exceed", "drop",
    ]
    first = common + ["pref", str(preference), "handle", str(handle), "flower", "ip_proto", "tcp", "dst_port", str(port)] + action
    second = common + ["pref", str(preference + 1), "handle", str(handle), "flower", "ip_proto", "tcp", "src_port", str(port)] + action
    run(first)
    try:
        run(second)
    except Exception:
        run(["/usr/sbin/tc", "filter", "del", "dev", "lo", "egress", "protocol", "ip",
             "pref", str(preference), "handle", str(handle), "flower"], check=False)
        raise


def load_tc_state():
    try:
        payload = read_json(TC_STATE)
        return payload if isinstance(payload, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def apply_shaping(devices):
    qdiscs = run(["/usr/sbin/tc", "qdisc", "show", "dev", "lo"]).stdout
    if "clsact" not in qdiscs:
        run(["/usr/sbin/tc", "qdisc", "add", "dev", "lo", "clsact"])
    old_records = load_tc_state()
    new_records = []
    for index, device in enumerate(devices):
        if device["enabled"] and device["speed_mbps"] > 0:
            new_records.append({
                "port": device["port"],
                "rate_kbps": max(8, int(device["speed_mbps"] * 1000)),
                "preference": 30000 + index * 2,
                "handle": 4096 + index,
            })
    for record in old_records:
        delete_tc_record(record)
    added_records = []
    try:
        for record in new_records:
            add_tc_record(record)
            added_records.append(record)
    except Exception:
        for record in added_records:
            delete_tc_record(record)
        for record in old_records:
            add_tc_record(record)
        raise
    atomic_write(TC_STATE, json.dumps(new_records, indent=2) + "\n", 0o600)


def apply_runtime(devices, api_port=DEFAULT_API_PORT, shaping_only=False):
    if shaping_only:
        apply_shaping(devices)
        return
    old_xray = open(XRAY_CONFIG, "rb").read() if os.path.exists(XRAY_CONFIG) else None
    old_nginx = open(NGINX_SNIPPET, "rb").read() if os.path.exists(NGINX_SNIPPET) else None
    atomic_write(XRAY_CONFIG, build_xray(devices, api_port), 0o640)
    os.chown(XRAY_CONFIG, 0, grp.getgrnam(XRAY_GROUP).gr_gid)
    atomic_write(NGINX_SNIPPET, build_nginx(devices), 0o644)
    try:
        run([XRAY_BIN, "run", "-test", "-config", XRAY_CONFIG])
        run(["/usr/sbin/nginx", "-t"])
        apply_shaping(devices)
        run(["/usr/bin/systemctl", "restart", XRAY_SERVICE])
        run(["/usr/bin/systemctl", "is-active", "--quiet", XRAY_SERVICE])
        run(["/usr/bin/systemctl", "reload", "nginx.service"])
    except Exception:
        if old_xray is not None:
            with open(XRAY_CONFIG, "wb") as handle:
                handle.write(old_xray)
            os.chmod(XRAY_CONFIG, 0o640)
        if old_nginx is not None:
            with open(NGINX_SNIPPET, "wb") as handle:
                handle.write(old_nginx)
            os.chmod(NGINX_SNIPPET, 0o644)
        run(["/usr/bin/systemctl", "restart", XRAY_SERVICE], check=False)
        run(["/usr/bin/systemctl", "reload", "nginx.service"], check=False)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shaping-only", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("must run as root")
    config = read_json(CONFIG_PATH)
    devices = validated_devices(config)
    api_port = int(config.get("xray_api_port", DEFAULT_API_PORT))
    if not 1024 <= api_port <= 65535:
        raise SystemExit("invalid Xray API port")
    apply_runtime(devices, api_port, shaping_only=args.shaping_only)


if __name__ == "__main__":
    main()
