# Proxy Panel

一套自托管、单管理员的 Clash 兼容代理管理工具。每台设备拥有独立的 VLESS/WebSocket 订阅、二维码、流量统计、流量额度、双向限速和即时停用能力。

文档：[English](../README.md) | **简体中文** | [繁體中文](README.zh-TW.md)

## 安装内容与兼容性

- 仅监听 `127.0.0.1` 的无第三方依赖 Python 管理程序
- 独立的 Xray 二进制和 `proxy-panel-xray.service`，不会复用已有 `xray.service`
- 独立的 Nginx 站点及自动生成的设备路由片段
- 独立系统用户、配置目录、运行状态和 systemd 单元
- 可自动申请 Let's Encrypt 证书，也可只引用已有证书

支持 Ubuntu 22.04/24.04、AMD64/ARM64。公网只需开放 TCP 80 和 443。若安装路径已存在、域名已经被其他 Nginx 站点使用，或本地端口冲突，安装器会停止，不会覆盖。

## 安装

先把域名直接解析到服务器，再执行：

```bash
sudo apt-get update && sudo apt-get install -y git make
git clone https://github.com/EriconYu/proxy-panel.git
cd proxy-panel
make configure
$EDITOR env.conf
make validate
make install
```

`make` 只是便捷入口。服务器没有 Make 时，等价命令仍然是 `cp env.conf.example env.conf`、`chmod 600 env.conf`、编辑该文件，然后执行 `sudo ./install.sh`。

`env.conf` 已被 Git 忽略。仓库中的示例特意保留首次登录账号 `admin / 111111`；这是公开密码，登录后必须立即修改。只有从管理页成功修改密码后，初始密码警告才会消失。

配置字段：

| 配置项 | 含义 |
| --- | --- |
| `DOMAIN` | 独立公网域名；不能复用已由其他 Nginx 站点占用的域名 |
| `ADMIN_USERNAME`、`ADMIN_PASSWORD` | 首次登录账号；密码可以包含空格和 Shell 符号，但不能换行 |
| `TLS_MODE` | `certbot` 或 `existing` |
| `LETSENCRYPT_EMAIL` | `certbot` 模式必填 |
| `TLS_CERT_PATH`、`TLS_KEY_PATH` | `existing` 模式必填 |
| `PANEL_PORT`、`XRAY_API_PORT` | 未被占用的本地 TCP 端口 |
| `DEVICE_PORT_MIN`、`DEVICE_PORT_MAX` | 独立本地端口段，最多 1,000 个端口；面板最多管理 100 台设备 |
| `CHECK_DNS` | DNS 解析预检；关闭它不会绕过 Certbot 的验证 |
| `XRAY_VERSION` 及校验值 | 固定版本及 AMD64/ARM64 必填完整性哈希 |

使用 `TLS_MODE=certbot` 时，域名必须解析到本机，公网 TCP 80/443 必须可达。使用 `TLS_MODE=existing` 时填写可读的证书和私钥绝对路径；安装器只引用它们，不复制，也不取得其所有权。

## 日常运维

```bash
make help
make status
make backup
make restore BACKUP=/var/backups/proxy-panel/proxy-panel-TIMESTAMP.tar.gz
make update
make uninstall
```

`make deploy` 是 `make install` 的别名。部署配置不在仓库目录时可传入 `ENV=/path/to/env.conf`。Make 命令调用的仍是同一组已审阅脚本，也继续支持直接执行脚本。

更新前会自动备份，再替换应用和服务文件；不会暗中升级 Xray 或改变部署参数。卸载默认先备份，并保留软件包、备份、TLS 证书以及所有无关的 Nginx 配置。

## 为什么不能自动识别手机型号

ClashMi 通过订阅和代理协议连接时，不会提供可信的手机型号、系统身份或稳定硬件标识。因此，本工具把“每个独立订阅”定义为一台设备：请为每部手机单独创建设备，并导入各自的二维码。同一个订阅若复制给多部手机，它们会被合并统计和控制，之后无法仅凭网络流量可靠拆分。

## 安全与风险边界

- 只能用于你拥有或获授权管理的基础设施，并遵守所在地法律和服务商条款。本项目不提供法律意见，也不承诺匿名性。
- 为兼容 Clash，当前使用 VLESS/WebSocket。网络运营方仍可能识别、限速或封锁它，它不是“无法被封锁”的保证。
- 管理密码和每条订阅 URL 都是访问凭证。必须使用 HTTPS。随机管理路径只是附加防护，不能代替身份验证。
- `config.json` 和备份包含密码哈希、设备 UUID、订阅令牌。文件权限为 `0600`，备份仍需单独安全保管。
- Web 进程不能任意执行 root 命令。特权辅助程序只接受严格校验的设备字段，只写本工具拥有的 Xray、Nginx 片段和运行状态，并只重启自己的服务及重载 Nginx。
- 限速规则只匹配本工具为设备分配的回环 TCP 端口，不应命中其他公网或回环流量。超速流量可能被丢包，而不是排队。
- 额度每 10 秒检查一次，停用前可能产生少量超额。统计值来自 Xray 用户流量，不适合作为财务或取证计量。
- 修改设备会重启本工具的独立 Xray 核心，其他面板设备会短暂断线；Nginx 重载是平滑的，但作用于同一个 Nginx 实例。包含访问凭证的请求路径不会写入应用或 Nginx 访问日志。
- 安装会安装系统软件包并重载 Nginx，但不会修改防火墙。生产部署前应审阅脚本并创建服务器快照。

漏洞报告与完整信任模型见 [SECURITY.md](../SECURITY.md)。

## 文件边界

| 路径 | 用途 |
| --- | --- |
| `/opt/proxy-panel` | 应用、Xray 二进制和敏感应用状态 |
| `/etc/proxy-panel` | 生成的 Xray 配置及非敏感安装参数 |
| `/etc/nginx/sites-available/proxy-panel.conf` | 本工具拥有的 Nginx 站点 |
| `/etc/nginx/snippets/proxy-panel-devices.conf` | 严格校验后生成的设备路由 |
| `/var/lib/proxy-panel-runtime` | 限速运行状态 |
| `/var/backups/proxy-panel` | 权限为 `0700` 的备份目录 |

## 开发检查

```bash
make test
make lint
make audit
# 或一次执行：make check
```

应用只使用 Python 标准库；运行时通过系统 `qrencode` 命令生成二维码。项目采用 MIT 许可证。
