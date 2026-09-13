#!/usr/bin/env python3
import argparse
import base64
import copy
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse


CONFIG_PATH = os.environ.get("PROXY_PANEL_CONFIG", "/opt/proxy-panel/data/config.json")
XRAY_BIN = os.environ.get("PROXY_PANEL_XRAY_BIN", "/opt/proxy-panel/bin/xray")
APPLY_COMMAND = ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "--wait", "restart", "proxy-panel-apply.service"]
CONFIG_LOCK = threading.RLock()
SESSION_LOCK = threading.Lock()
SESSIONS = {}
LOGIN_ATTEMPTS = {}
DEFAULT_PORT_MIN = 11000
DEFAULT_PORT_MAX = 11999
DEFAULT_API_PORT = 10085
DEFAULT_LISTEN_PORT = 18080


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(config):
    directory = os.path.dirname(CONFIG_PATH)
    fd, temporary = tempfile.mkstemp(prefix="config.", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, CONFIG_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def password_record(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def verify_password(password, config):
    try:
        salt = base64.b64decode(config["password_salt"])
        expected = base64.b64decode(config["password_hash"])
    except (KeyError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return hmac.compare_digest(actual, expected)


def set_password(config, password):
    salt, digest = password_record(password)
    config.update({
        "password_salt": salt,
        "password_hash": digest,
        "password_changed": True,
        "weak_password": False,
    })


def human_bytes(value):
    value = max(0, int(value or 0))
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024


def parse_number(value, minimum, maximum, label):
    try:
        number = float(value or 0)
    except ValueError as exc:
        raise ValueError(f"{label}格式不正确") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{label}必须在 {minimum} 到 {maximum} 之间")
    return number


def query_core_stats(config):
    api_port = int(config.get("xray_api_port", DEFAULT_API_PORT))
    try:
        result = subprocess.run(
            [XRAY_BIN, "api", "statsquery", f"--server=127.0.0.1:{api_port}", "-pattern=user>>>"],
            text=True,
            capture_output=True,
            timeout=4,
            check=False,
        )
        if result.returncode != 0:
            return None, result.stderr.strip() or "流量统计暂不可用"
        payload = json.loads(result.stdout or "{}")
        stats = {}
        for record in payload.get("stat", []):
            name = record.get("name", "")
            match = re.fullmatch(r"user>>>(device-[a-f0-9]{12})>>>traffic>>>(uplink|downlink)", name)
            if match:
                stats.setdefault(match.group(1), {"uplink": 0, "downlink": 0})[match.group(2)] = int(record.get("value", 0))
        return stats, None
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError) as exc:
        return None, str(exc)


def xray_online(config):
    try:
        with socket.create_connection(("127.0.0.1", int(config.get("xray_api_port", DEFAULT_API_PORT))), timeout=1):
            return True
    except OSError:
        return False


def sync_stats_locked(config, enforce=True):
    stats, error = query_core_stats(config)
    if stats is None:
        return False, error, False
    now = int(time.time())
    changed = False
    quota_changed = False
    for device in config.get("devices", []):
        current = stats.get(device["email"], {"uplink": 0, "downlink": 0})
        for direction in ("uplink", "downlink"):
            core_key = f"core_{direction}"
            used_key = f"used_{direction}"
            previous = int(device.get(core_key, 0))
            value = int(current[direction])
            delta = value - previous if value >= previous else value
            if delta > 0:
                device[used_key] = int(device.get(used_key, 0)) + delta
                device["last_activity"] = now
                changed = True
            if value != previous:
                device[core_key] = value
                changed = True
        used = int(device.get("used_uplink", 0)) + int(device.get("used_downlink", 0))
        quota = int(device.get("quota_bytes", 0))
        if enforce and device.get("enabled") and quota > 0 and used >= quota:
            device["enabled"] = False
            device["disabled_reason"] = "quota"
            quota_changed = True
            changed = True
    if changed:
        save_config(config)
    return changed, error, quota_changed


def apply_runtime_locked(config):
    try:
        result = subprocess.run(APPLY_COMMAND, text=True, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"运行配置应用失败: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "运行配置应用失败")
    for device in config.get("devices", []):
        device["core_uplink"] = 0
        device["core_downlink"] = 0
    save_config(config)


def sync_stats(enforce=True):
    with CONFIG_LOCK:
        config = load_config()
        previously_enabled = {device.get("id") for device in config.get("devices", []) if device.get("enabled")}
        _, error, quota_changed = sync_stats_locked(config, enforce=enforce)
        if quota_changed:
            try:
                apply_runtime_locked(config)
            except RuntimeError:
                for device in config.get("devices", []):
                    if device.get("id") in previously_enabled and device.get("disabled_reason") == "quota":
                        device["enabled"] = True
                        device["disabled_reason"] = ""
                save_config(config)
                raise
        return config, error


def mutate_devices(mutator, apply_runtime=True):
    with CONFIG_LOCK:
        config = load_config()
        sync_stats_locked(config, enforce=False)
        old_config = copy.deepcopy(config)
        mutator(config)
        save_config(config)
        if not apply_runtime:
            return config
        try:
            apply_runtime_locked(config)
        except Exception:
            save_config(old_config)
            try:
                apply_runtime_locked(old_config)
            except Exception:
                pass
            raise
        return config


def next_port(config):
    devices = config.get("devices", [])
    port_min = int(config.get("device_port_min", DEFAULT_PORT_MIN))
    port_max = int(config.get("device_port_max", DEFAULT_PORT_MAX))
    used = {int(device["port"]) for device in devices}
    for port in range(port_min, port_max + 1):
        if port not in used:
            return port
    raise ValueError("设备数量已达到上限")


def new_device(name, quota_gb=0, speed_mbps=0):
    device_id = secrets.token_hex(6)
    return {
        "id": device_id,
        "name": name,
        "email": f"device-{device_id}",
        "uuid": str(uuid.uuid4()),
        "subscription_token": secrets.token_hex(24),
        "ws_path": f"/edge-{secrets.token_hex(12)}",
        "port": None,
        "enabled": True,
        "disabled_reason": "",
        "quota_bytes": int(quota_gb * 1024 * 1024 * 1024),
        "speed_mbps": speed_mbps,
        "used_uplink": 0,
        "used_downlink": 0,
        "core_uplink": 0,
        "core_downlink": 0,
        "last_activity": None,
        "created_at": int(time.time()),
    }


def find_device(config, device_id):
    for device in config.get("devices", []):
        if device.get("id") == device_id:
            return device
    return None


def subscription_url(config, device):
    return f'https://{config["public_host"]}/clash/{device["subscription_token"]}'


def clash_yaml(config, device):
    name = device["name"]
    host = config["public_host"]
    return f'''# Managed by Proxy Panel
mixed-port: 7890
allow-lan: false
mode: rule
log-level: warning
ipv6: false

proxies:
  - name: {json.dumps(name, ensure_ascii=False)}
    type: vless
    server: {host}
    port: 443
    uuid: {device["uuid"]}
    network: ws
    tls: true
    udp: true
    servername: {host}
    client-fingerprint: chrome
    ws-opts:
      path: {device["ws_path"]}
      headers:
        Host: {host}

proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - {json.dumps(name, ensure_ascii=False)}

rules:
  - MATCH,PROXY
'''


def base_styles():
    return '''
    :root { color-scheme:light; --ink:#18201d; --muted:#65706b; --line:#dce2df;
      --paper:#fff; --bg:#f3f5f4; --green:#157347; --green-soft:#e7f4ec;
      --amber:#8a6100; --amber-soft:#fff4d6; --red:#b42318; --red-soft:#fff0ee; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family:-apple-system,BlinkMacSystemFont,
      "Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; letter-spacing:0; }
    button,input { font:inherit; letter-spacing:0; }
    .shell { width:min(1040px,calc(100% - 32px)); margin:0 auto; }
    header { background:var(--paper); border-bottom:1px solid var(--line); }
    .topbar { min-height:64px; display:flex; align-items:center; justify-content:space-between; gap:16px; }
    .brand { font-size:18px; font-weight:700; }.subtle { color:var(--muted); font-size:13px; }
    main { padding:24px 0 48px; }h1 { margin:0; font-size:24px; line-height:1.3; }
    h2 { margin:0 0 16px; font-size:17px; }h3 { margin:0; font-size:16px; }p { line-height:1.55; }
    .row { display:flex; align-items:center; justify-content:space-between; gap:16px; }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; margin:20px 0; }
    .metric,.panel { background:var(--paper); border:1px solid var(--line); border-radius:8px; }
    .metric { padding:18px; min-height:104px; }.metric strong { display:block; margin-top:8px; font-size:22px; }
    .panel { padding:20px; margin-top:16px; }.device { padding:18px 0; border-top:1px solid var(--line); }
    .device:first-of-type { border-top:0; padding-top:0; }.device:last-child { padding-bottom:0; }
    .device-head { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:12px; }
    .device-stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; margin:14px 0; }
    .mini { background:transparent; border-left:2px solid var(--line); padding:4px 11px; min-height:52px; }
    .mini strong { display:block; margin-top:5px; font-size:15px; }
    .badge { display:inline-flex; align-items:center; min-height:28px; padding:4px 10px; border-radius:999px;
      font-size:12px; font-weight:650; white-space:nowrap; }.dot { width:7px; height:7px; border-radius:50%; background:currentColor; margin-right:7px; }
    .badge.online { color:var(--green); background:var(--green-soft); }.badge.waiting { color:var(--amber); background:var(--amber-soft); }
    .badge.offline { color:var(--red); background:var(--red-soft); }
    .field { margin-top:12px; }label { display:block; margin-bottom:7px; font-size:13px; font-weight:650; }
    input[type=text],input[type=password],input[type=number] { width:100%; min-height:44px; border:1px solid #bdc7c2;
      border-radius:6px; padding:9px 11px; background:#fff; color:var(--ink); }
    input:focus { outline:3px solid #bde3cc; border-color:var(--green); }
    .form-grid { display:grid; grid-template-columns:2fr 1fr 1fr; gap:10px; }.password-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .linkline { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:8px; }
    .button { display:inline-flex; min-height:40px; align-items:center; justify-content:center; border:1px solid transparent;
      border-radius:6px; padding:8px 14px; cursor:pointer; font-weight:650; text-decoration:none; white-space:nowrap; }
    .button.primary { color:#fff; background:var(--green); }.button.secondary { color:var(--ink); background:#fff; border-color:#bdc7c2; }
    .button.danger { color:var(--red); background:#fff; border-color:#e3b6b1; }.button:hover { filter:brightness(.96); }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }.inline { display:inline; }
    details { margin-top:12px; }summary { cursor:pointer; font-weight:650; color:var(--green); }
    .connection { display:grid; grid-template-columns:minmax(0,1fr) 156px; gap:18px; align-items:start; margin-top:12px; }
    .qr { width:156px; height:156px; border:1px solid var(--line); border-radius:6px; padding:7px; background:#fff; }
    progress { width:100%; height:8px; border:0; border-radius:4px; overflow:hidden; }progress::-webkit-progress-bar { background:#e7ebe9; }
    progress::-webkit-progress-value { background:var(--green); }progress::-moz-progress-bar { background:var(--green); }
    .notice { padding:12px 14px; border-radius:6px; margin:16px 0; font-size:14px; }.notice.warn { color:#684900; background:var(--amber-soft); border:1px solid #f0d78b; }
    .notice.ok { color:#0e5936; background:var(--green-soft); border:1px solid #b8ddc6; }.notice.error { color:var(--red); background:var(--red-soft); border:1px solid #e3b6b1; }
    .login-wrap { min-height:100vh; display:grid; place-items:center; padding:24px; }.login-box { width:min(390px,100%); background:#fff; border:1px solid var(--line); border-radius:8px; padding:28px; }
    .login-box h1 { margin-bottom:6px; }.login-box .button { width:100%; margin-top:18px; }.footer-note { margin-top:10px; color:var(--muted); font-size:12px; }
    @media(max-width:700px) { .shell { width:min(100% - 24px,1040px); }.topbar { min-height:58px; }main { padding-top:16px; }
      .grid,.device-stats,.form-grid,.password-grid { grid-template-columns:1fr; }.metric { min-height:88px; }.connection { grid-template-columns:1fr; }
      .qr { width:196px; height:196px; justify-self:center; }.linkline { grid-template-columns:1fr; }.linkline .button { width:100%; } }
    '''


def login_page(config, message=""):
    base = config["base_path"]
    error = f'<div class="notice error">{html.escape(message)}</div>' if message else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>网络连接管理</title><style>{base_styles()}</style></head><body><div class="login-wrap">
    <form class="login-box" method="post" action="{base}login"><h1>网络连接管理</h1><p class="subtle">请输入管理账号</p>
    <div class="field"><label for="username">用户名</label><input id="username" name="username" type="text" autocomplete="username" autocapitalize="none" value="{html.escape(config.get('username', 'admin'), quote=True)}" autofocus required></div>
    <div class="field"><label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required></div>
    {error}<button class="button primary" type="submit">进入管理页</button></form></div></body></html>'''


def device_block(config, device, base):
    device_id = device["id"]
    used_up = int(device.get("used_uplink", 0))
    used_down = int(device.get("used_downlink", 0))
    used = used_up + used_down
    quota = int(device.get("quota_bytes", 0))
    quota_gb = quota / 1024 / 1024 / 1024 if quota else 0
    percent = min(100, used * 100 / quota) if quota else 0
    last = device.get("last_activity")
    recent = bool(last and time.time() - last < 180)
    if not device.get("enabled"):
        status_class, status_text = "offline", "额度已用完" if device.get("disabled_reason") == "quota" else "已停用"
    elif recent:
        status_class, status_text = "online", "最近活跃"
    else:
        status_class, status_text = "waiting", "等待连接"
    sub_url = html.escape(subscription_url(config, device), quote=True)
    speed = float(device.get("speed_mbps", 0))
    speed_value = "0" if speed == 0 else f"{speed:g}"
    toggle_text = "重新启用" if not device.get("enabled") else "停用并踢下线"
    toggle_class = "secondary" if not device.get("enabled") else "danger"
    quota_text = "不限量" if not quota else f"{human_bytes(used)} / {human_bytes(quota)}"
    return f'''<div class="device" data-device="{device_id}"><div class="device-head"><div><h3>{html.escape(device["name"])}</h3>
      <div class="subtle">编号 {device_id}</div></div><span id="status-{device_id}" class="badge {status_class}"><span class="dot"></span>{status_text}</span></div>
      <div class="device-stats"><div class="mini"><span class="subtle">上传</span><strong id="up-{device_id}">{human_bytes(used_up)}</strong></div>
      <div class="mini"><span class="subtle">下载</span><strong id="down-{device_id}">{human_bytes(used_down)}</strong></div>
      <div class="mini"><span class="subtle">流量额度</span><strong id="quota-{device_id}">{quota_text}</strong></div></div>
      <progress id="progress-{device_id}" max="100" value="{percent:.2f}" {'hidden' if not quota else ''}></progress>
      <details><summary>订阅与二维码</summary><div class="connection"><div><div class="field"><label for="sub-{device_id}">专属订阅地址</label>
      <div class="linkline"><input id="sub-{device_id}" type="text" value="{sub_url}" readonly><button class="button primary copy" type="button" data-copy="sub-{device_id}">复制</button></div></div>
      <p class="footer-note">此订阅只供这台设备使用。</p></div><img class="qr" src="{base}device/{device_id}/qr.png" alt="{html.escape(device['name'])} 订阅二维码" width="156" height="156"></div></details>
      <details><summary>设备设置</summary><form method="post" action="{base}device/{device_id}/update"><input type="hidden" name="csrf" value="{{csrf}}">
      <div class="form-grid"><div class="field"><label>设备名称</label><input name="name" type="text" maxlength="40" value="{html.escape(device['name'], quote=True)}" required></div>
      <div class="field"><label>流量额度 (GB)</label><input name="quota_gb" type="number" min="0" max="10000" step="0.1" value="{quota_gb:g}"></div>
      <div class="field"><label>双向限速 (Mbps)</label><input name="speed_mbps" type="number" min="0" max="1000" step="0.1" value="{speed_value}"></div></div>
      <p class="footer-note">数值 0 表示不限量或不限速。</p><div class="actions"><button class="button primary" type="submit">保存设备设置</button></div></form></details>
      <div class="actions"><form class="inline" method="post" action="{base}device/{device_id}/toggle"><input type="hidden" name="csrf" value="{{csrf}}"><button class="button {toggle_class}" type="submit">{toggle_text}</button></form>
      <form class="inline" method="post" action="{base}device/{device_id}/reset"><input type="hidden" name="csrf" value="{{csrf}}"><button class="button secondary" type="submit">流量归零</button></form>
      <form class="inline delete-form" method="post" action="{base}device/{device_id}/delete"><input type="hidden" name="csrf" value="{{csrf}}"><button class="button danger" type="submit">删除设备</button></form></div></div>'''


def dashboard_page(config, csrf, notice="", error=""):
    base = config["base_path"]
    devices = config.get("devices", [])
    total_up = sum(int(d.get("used_uplink", 0)) for d in devices)
    total_down = sum(int(d.get("used_downlink", 0)) for d in devices)
    blocks = "".join(device_block(config, device, base).replace("{csrf}", csrf) for device in devices)
    if not blocks:
        blocks = '<div class="subtle">尚未添加设备。</div>'
    notice_html = f'<div class="notice ok">{html.escape(notice)}</div>' if notice else ""
    error_html = f'<div class="notice error">{html.escape(error)}</div>' if error else ""
    initial_html = '<div class="notice warn">当前使用初始密码，请在页面底部修改。</div>' if not config.get("password_changed", False) else ""
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>网络连接管理</title><style>{base_styles()}</style></head><body><header><div class="shell topbar"><div><div class="brand">网络连接管理</div>
    <div class="subtle">{html.escape(config['public_host'])}</div></div><form method="post" action="{base}logout"><input type="hidden" name="csrf" value="{csrf}"><button class="button secondary">退出</button></form></div></header>
    <main class="shell"><div class="row"><div><h1>设备与流量</h1><div class="subtle">数据每 5 秒更新</div></div>
    <span id="service" class="badge {'online' if xray_online(config) else 'offline'}"><span class="dot"></span>{'服务正常' if xray_online(config) else '服务异常'}</span></div>
    {initial_html}{notice_html}{error_html}<section class="grid"><div class="metric"><span class="subtle">设备数量</span><strong>{len(devices)}</strong></div>
    <div class="metric"><span class="subtle">总上传</span><strong id="total-up">{human_bytes(total_up)}</strong></div><div class="metric"><span class="subtle">总下载</span><strong id="total-down">{human_bytes(total_down)}</strong></div></section>
    <section class="panel"><h2>设备列表</h2>{blocks}</section>
    <section class="panel"><h2>添加设备</h2><form method="post" action="{base}device/add"><input type="hidden" name="csrf" value="{csrf}">
    <div class="form-grid"><div class="field"><label for="add-name">设备名称</label><input id="add-name" name="name" type="text" maxlength="40" placeholder="例如：我的 iPhone" required></div>
    <div class="field"><label for="add-quota">流量额度 (GB)</label><input id="add-quota" name="quota_gb" type="number" min="0" max="10000" step="0.1" value="0"></div>
    <div class="field"><label for="add-speed">双向限速 (Mbps)</label><input id="add-speed" name="speed_mbps" type="number" min="0" max="1000" step="0.1" value="0"></div></div>
    <div class="actions"><button class="button primary" type="submit">创建设备</button></div></form></section>
    <section class="panel"><h2>修改管理密码</h2><form method="post" action="{base}password"><input type="hidden" name="csrf" value="{csrf}">
    <div class="form-grid password-grid"><div class="field"><label>当前密码</label><input name="current" type="password" autocomplete="current-password" required></div>
    <div class="field"><label>新密码</label><input name="new" type="password" autocomplete="new-password" minlength="8" required></div>
    <div class="field"><label>确认新密码</label><input name="confirm" type="password" autocomplete="new-password" minlength="8" required></div></div>
    <div class="actions"><button class="button primary" type="submit">保存新密码</button></div></form></section></main>
    <script>const base={json.dumps(base)};const fmt=(n)=>{{const u=['B','KB','MB','GB','TB'];let v=Math.max(0,Number(n)||0),i=0;while(v>=1024&&i<u.length-1){{v/=1024;i++;}}return(i===0?v.toFixed(0):v.toFixed(2))+' '+u[i];}};
    document.querySelectorAll('.copy').forEach(b=>b.addEventListener('click',async()=>{{const input=document.getElementById(b.dataset.copy);try{{await navigator.clipboard.writeText(input.value);}}catch(e){{input.select();document.execCommand('copy');}}b.textContent='已复制';setTimeout(()=>b.textContent='复制',1500);}}));
    document.querySelectorAll('.delete-form').forEach(f=>f.addEventListener('submit',e=>{{if(!confirm('确定删除这台设备并使其订阅永久失效吗？'))e.preventDefault();}}));
    async function refresh(){{try{{const r=await fetch(base+'api/stats',{{cache:'no-store'}});if(!r.ok)return;const s=await r.json();document.getElementById('total-up').textContent=fmt(s.total_uplink);document.getElementById('total-down').textContent=fmt(s.total_downlink);
    s.devices.forEach(d=>{{const up=document.getElementById('up-'+d.id),down=document.getElementById('down-'+d.id),q=document.getElementById('quota-'+d.id),st=document.getElementById('status-'+d.id),p=document.getElementById('progress-'+d.id);if(!up)return;up.textContent=fmt(d.used_uplink);down.textContent=fmt(d.used_downlink);q.textContent=d.quota_bytes?fmt(d.used_total)+' / '+fmt(d.quota_bytes):'不限量';p.hidden=!d.quota_bytes;p.value=d.quota_percent;
    st.className='badge '+d.status_class;st.innerHTML='<span class="dot"></span>'+d.status_text;}});const svc=document.getElementById('service');svc.className='badge '+(s.service_online?'online':'offline');svc.innerHTML='<span class="dot"></span>'+(s.service_online?'服务正常':'服务异常');}}catch(e){{}}}}setInterval(refresh,5000);</script></body></html>'''


NOTICE_MESSAGES = {
    "device-added": "设备已创建，请为它单独导入订阅。",
    "device-updated": "设备设置已保存。",
    "device-disabled": "设备已停用并踢下线。",
    "device-enabled": "设备已重新启用。",
    "device-deleted": "设备及其订阅已删除。",
    "traffic-reset": "该设备的累计流量已归零。",
    "password-changed": "管理密码已修改。",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "ProxyPanel"
    sys_version = ""

    def log_message(self, fmt, *args):
        status = args[1] if len(args) > 1 else "-"
        print(f"{self.client_address[0]} - response {status}", flush=True)

    def security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")

    def send_body(self, status, body, content_type="text/html; charset=utf-8", extra=None, head=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def redirect(self, location, cookie_value=None):
        self.send_response(303)
        self.security_headers()
        self.send_header("Location", location)
        if cookie_value is not None:
            self.send_header("Set-Cookie", cookie_value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def form(self):
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 16384)
        except ValueError:
            length = 0
        return {key: values[0] for key, values in parse_qs(self.rfile.read(length).decode(), keep_blank_values=True).items()}

    def client_ip(self):
        return self.headers.get("X-Real-IP", self.client_address[0]).split(",", 1)[0].strip()

    def session(self):
        jar = cookies.SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
            sid = jar["proxy_session"].value
        except (KeyError, cookies.CookieError):
            return None, None
        with SESSION_LOCK:
            record = SESSIONS.get(sid)
            if not record or record["expires"] < time.time():
                SESSIONS.pop(sid, None)
                return None, None
            record["expires"] = time.time() + 12 * 3600
        return sid, record

    def require_session(self, config):
        sid, session = self.session()
        if not session:
            self.redirect(config["base_path"])
            return None, None
        return sid, session

    def checked_form(self, config):
        sid, session = self.require_session(config)
        if not session:
            return None, None, None
        form = self.form()
        if not hmac.compare_digest(form.get("csrf", ""), session["csrf"]):
            self.send_body(403, "请求校验失败", "text/plain; charset=utf-8")
            return None, None, None
        return sid, session, form

    def subscription_device(self, config, path):
        match = re.fullmatch(r"/clash/([a-f0-9]{48})", path)
        if not match:
            return None
        return next((d for d in config.get("devices", []) if hmac.compare_digest(d["subscription_token"], match.group(1))), None)

    def serve_subscription(self, config, device, head=False):
        used_up = int(device.get("used_uplink", 0))
        used_down = int(device.get("used_downlink", 0))
        total = int(device.get("quota_bytes", 0)) or 1099511627776
        self.send_body(200, clash_yaml(config, device), "text/yaml; charset=utf-8", {
            "Content-Disposition": 'inline; filename="clashmi.yaml"',
            "Profile-Update-Interval": "24",
            "Subscription-Userinfo": f"upload={used_up}; download={used_down}; total={total}",
        }, head=head)

    def do_HEAD(self):
        config, _ = sync_stats()
        device = self.subscription_device(config, urlparse(self.path).path)
        if device and device.get("enabled"):
            self.serve_subscription(config, device, head=True)
        else:
            self.send_body(404, b"", "text/plain", head=True)

    def do_GET(self):
        config, stats_error = sync_stats()
        parsed = urlparse(self.path)
        path = parsed.path
        base = config["base_path"]
        device = self.subscription_device(config, path)
        if device:
            if device.get("enabled"):
                self.serve_subscription(config, device)
            else:
                self.send_body(403, "Device disabled", "text/plain; charset=utf-8")
            return
        if path == base[:-1]:
            self.redirect(base)
            return
        if path == base:
            _, session = self.session()
            if not session:
                self.send_body(200, login_page(config))
                return
            query = parse_qs(parsed.query)
            notice = NOTICE_MESSAGES.get(query.get("notice", [""])[0], "")
            error = query.get("error", [stats_error or ""])[0]
            self.send_body(200, dashboard_page(config, session["csrf"], notice, error))
            return
        if path == base + "api/stats":
            _, session = self.require_session(config)
            if not session:
                return
            now = time.time()
            devices = []
            for item in config.get("devices", []):
                used_up = int(item.get("used_uplink", 0))
                used_down = int(item.get("used_downlink", 0))
                used = used_up + used_down
                quota = int(item.get("quota_bytes", 0))
                recent = bool(item.get("last_activity") and now - item["last_activity"] < 180)
                if not item.get("enabled"):
                    status_class = "offline"
                    status_text = "额度已用完" if item.get("disabled_reason") == "quota" else "已停用"
                elif recent:
                    status_class, status_text = "online", "最近活跃"
                else:
                    status_class, status_text = "waiting", "等待连接"
                devices.append({"id": item["id"], "used_uplink": used_up, "used_downlink": used_down, "used_total": used,
                    "quota_bytes": quota, "quota_percent": min(100, used * 100 / quota) if quota else 0,
                    "status_class": status_class, "status_text": status_text})
            self.send_body(200, json.dumps({"devices": devices, "total_uplink": sum(d["used_uplink"] for d in devices),
                "total_downlink": sum(d["used_downlink"] for d in devices), "service_online": xray_online(config), "error": stats_error}), "application/json; charset=utf-8")
            return
        qr_match = re.fullmatch(re.escape(base) + r"device/([a-f0-9]{12})/qr[.]png", path)
        if qr_match:
            _, session = self.require_session(config)
            if not session:
                return
            device = find_device(config, qr_match.group(1))
            if not device:
                self.send_body(404, "Not found", "text/plain")
                return
            try:
                result = subprocess.run(["/usr/bin/qrencode", "-o", "-", "-t", "PNG", "-s", "7", "-m", "1", subscription_url(config, device)], capture_output=True, timeout=4)
                if result.returncode != 0:
                    raise RuntimeError("qrencode failed")
                self.send_body(200, result.stdout, "image/png")
            except (OSError, subprocess.SubprocessError, RuntimeError):
                self.send_body(500, "二维码生成失败", "text/plain; charset=utf-8")
            return
        self.send_body(404, "Not found", "text/plain; charset=utf-8")

    def do_POST(self):
        config = load_config()
        path = urlparse(self.path).path
        base = config["base_path"]
        if path == base + "login":
            form = self.form()
            ip, now = self.client_ip(), time.time()
            with SESSION_LOCK:
                if len(LOGIN_ATTEMPTS) >= 10_000:
                    for address in list(LOGIN_ATTEMPTS):
                        recent = [stamp for stamp in LOGIN_ATTEMPTS[address] if now - stamp < 300]
                        if recent:
                            LOGIN_ATTEMPTS[address] = recent
                        else:
                            LOGIN_ATTEMPTS.pop(address, None)
                if len(LOGIN_ATTEMPTS) >= 10_000 and ip not in LOGIN_ATTEMPTS:
                    self.send_body(429, login_page(config, "登录请求过多，请稍后再试。"))
                    return
                attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(ip, []) if now - stamp < 300]
                LOGIN_ATTEMPTS[ip] = attempts
            if len(attempts) >= 5:
                self.send_body(429, login_page(config, "尝试次数过多，请五分钟后再试。"))
                return
            username_ok = hmac.compare_digest(form.get("username", ""), config.get("username", "admin"))
            if not username_ok or not verify_password(form.get("password", ""), config):
                with SESSION_LOCK:
                    LOGIN_ATTEMPTS[ip].append(now)
                time.sleep(0.35)
                self.send_body(401, login_page(config, "用户名或密码不正确。"))
                return
            sid = secrets.token_urlsafe(32)
            with SESSION_LOCK:
                LOGIN_ATTEMPTS.pop(ip, None)
                for expired_sid in [key for key, value in SESSIONS.items() if value["expires"] < now]:
                    SESSIONS.pop(expired_sid, None)
                if len(SESSIONS) >= 1024:
                    SESSIONS.pop(min(SESSIONS, key=lambda key: SESSIONS[key]["expires"]), None)
                SESSIONS[sid] = {"csrf": secrets.token_urlsafe(24), "expires": now + 12 * 3600}
            self.redirect(base, f"proxy_session={sid}; Path={base}; Max-Age=43200; Secure; HttpOnly; SameSite=Strict")
            return
        sid, session, form = self.checked_form(config)
        if not session:
            return
        if path == base + "logout":
            with SESSION_LOCK:
                SESSIONS.pop(sid, None)
            self.redirect(base, f"proxy_session=; Path={base}; Max-Age=0; Secure; HttpOnly; SameSite=Strict")
            return
        if path == base + "password":
            new, confirm = form.get("new", ""), form.get("confirm", "")
            if len(new) < 8 or new != confirm:
                self.redirect(base + "?error=" + quote("新密码至少八位且两次输入必须一致"))
                return
            with CONFIG_LOCK:
                config = load_config()
                if not verify_password(form.get("current", ""), config):
                    self.redirect(base + "?error=" + quote("当前密码不正确"))
                    return
                set_password(config, new)
                save_config(config)
            with SESSION_LOCK:
                SESSIONS.clear()
                SESSIONS[sid] = session
            self.redirect(base + "?notice=password-changed")
            return
        try:
            if path == base + "device/add":
                name = form.get("name", "").strip()
                if not name or len(name) > 40:
                    raise ValueError("设备名称应为 1 到 40 个字符")
                quota = parse_number(form.get("quota_gb"), 0, 10000, "流量额度")
                speed = parse_number(form.get("speed_mbps"), 0, 1000, "限速")
                def add(config):
                    device = new_device(name, quota, speed)
                    device["port"] = next_port(config)
                    config.setdefault("devices", []).append(device)
                mutate_devices(add)
                self.redirect(base + "?notice=device-added")
                return
            match = re.fullmatch(re.escape(base) + r"device/([a-f0-9]{12})/(update|toggle|delete|reset)", path)
            if not match:
                self.send_body(404, "Not found", "text/plain; charset=utf-8")
                return
            device_id, action = match.groups()
            if action == "update":
                name = form.get("name", "").strip()
                if not name or len(name) > 40:
                    raise ValueError("设备名称应为 1 到 40 个字符")
                quota = parse_number(form.get("quota_gb"), 0, 10000, "流量额度")
                speed = parse_number(form.get("speed_mbps"), 0, 1000, "限速")
                def update(config):
                    device = find_device(config, device_id)
                    if not device:
                        raise ValueError("设备不存在")
                    device["name"] = name
                    device["quota_bytes"] = int(quota * 1024 * 1024 * 1024)
                    device["speed_mbps"] = speed
                    used = int(device.get("used_uplink", 0)) + int(device.get("used_downlink", 0))
                    if device["enabled"] and device["quota_bytes"] and used >= device["quota_bytes"]:
                        device["enabled"] = False
                        device["disabled_reason"] = "quota"
                mutate_devices(update)
                self.redirect(base + "?notice=device-updated")
                return
            if action == "toggle":
                result = {"enabled": False}
                def toggle(config):
                    device = find_device(config, device_id)
                    if not device:
                        raise ValueError("设备不存在")
                    if device.get("enabled"):
                        device["enabled"] = False
                        device["disabled_reason"] = "manual"
                    else:
                        used = int(device.get("used_uplink", 0)) + int(device.get("used_downlink", 0))
                        if device.get("quota_bytes", 0) and used >= device["quota_bytes"]:
                            raise ValueError("请先增加流量额度或将流量归零")
                        device["enabled"] = True
                        device["disabled_reason"] = ""
                    result["enabled"] = device["enabled"]
                mutate_devices(toggle)
                self.redirect(base + ("?notice=device-enabled" if result["enabled"] else "?notice=device-disabled"))
                return
            if action == "delete":
                def delete(config):
                    before = len(config.get("devices", []))
                    config["devices"] = [d for d in config.get("devices", []) if d.get("id") != device_id]
                    if len(config["devices"]) == before:
                        raise ValueError("设备不存在")
                mutate_devices(delete)
                self.redirect(base + "?notice=device-deleted")
                return
            if action == "reset":
                def reset(config):
                    device = find_device(config, device_id)
                    if not device:
                        raise ValueError("设备不存在")
                    device["used_uplink"] = 0
                    device["used_downlink"] = 0
                mutate_devices(reset, apply_runtime=False)
                self.redirect(base + "?notice=traffic-reset")
                return
        except (ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            self.redirect(base + "?error=" + quote(str(exc)))


def quota_monitor():
    while True:
        time.sleep(10)
        try:
            sync_stats(enforce=True)
        except Exception as exc:
            print(f"quota monitor: {exc}", flush=True)


def initialize(args):
    if not re.fullmatch(r"(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}", args.host):
        raise SystemExit("invalid public host")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", args.username):
        raise SystemExit("invalid admin username")
    password = sys.stdin.readline().rstrip("\n") if args.password_stdin else args.password
    if len(password) < 6 or len(password) > 256:
        raise SystemExit("initial password must be 6 to 256 characters")
    if not re.fullmatch(r"/[a-zA-Z0-9_-]+/", args.base_path):
        raise SystemExit("invalid panel base path")
    if not 1024 <= args.xray_api_port <= 65535:
        raise SystemExit("invalid Xray API port")
    if not 1024 <= args.device_port_min <= args.device_port_max <= 65535:
        raise SystemExit("invalid device port range")
    if args.device_port_min <= args.xray_api_port <= args.device_port_max:
        raise SystemExit("Xray API port overlaps device port range")
    os.makedirs(os.path.dirname(CONFIG_PATH), mode=0o700, exist_ok=True)
    salt, digest = password_record(password)
    device = new_device("我的设备")
    device["port"] = args.device_port_min
    save_config({"schema_version": 2, "public_host": args.host, "username": args.username, "base_path": args.base_path,
        "password_salt": salt, "password_hash": digest, "password_changed": False, "weak_password": True,
        "xray_api_port": args.xray_api_port, "device_port_min": args.device_port_min, "device_port_max": args.device_port_max,
        "devices": [device]})


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    init = subparsers.add_parser("init")
    password_group = init.add_mutually_exclusive_group(required=True)
    password_group.add_argument("--password")
    password_group.add_argument("--password-stdin", action="store_true")
    init.add_argument("--host", required=True)
    init.add_argument("--username", default="admin")
    init.add_argument("--base-path", required=True)
    init.add_argument("--xray-api-port", type=int, default=DEFAULT_API_PORT)
    init.add_argument("--device-port-min", type=int, default=DEFAULT_PORT_MIN)
    init.add_argument("--device-port-max", type=int, default=DEFAULT_PORT_MAX)
    args = parser.parse_args()
    if args.command == "init":
        initialize(args)
        return
    monitor = threading.Thread(target=quota_monitor, daemon=True)
    monitor.start()
    listen_port = int(os.environ.get("PROXY_PANEL_LISTEN_PORT", DEFAULT_LISTEN_PORT))
    server = ThreadingHTTPServer(("127.0.0.1", listen_port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
