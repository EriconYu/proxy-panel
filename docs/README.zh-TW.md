# Proxy Panel

一套自架、單一管理員的 Clash 相容代理管理工具。每台裝置擁有獨立的 VLESS/WebSocket 訂閱、QR Code、流量統計、流量額度、雙向限速及即時停用能力。

文件：[English](../README.md) | [简体中文](README.zh-CN.md) | **繁體中文**

## 安裝內容與相容性

- 僅監聽 `127.0.0.1`、無第三方相依套件的 Python 管理程式
- 獨立 Xray 執行檔與 `proxy-panel-xray.service`，不會沿用既有 `xray.service`
- 獨立 Nginx 站台及自動產生的裝置路由片段
- 獨立系統使用者、設定目錄、執行狀態與 systemd 單元
- 可自動申請 Let's Encrypt 憑證，也可只引用既有憑證

支援 Ubuntu 22.04/24.04、AMD64/ARM64。公網只需開放 TCP 80 與 443。若安裝路徑已存在、網域已由其他 Nginx 站台使用，或本機連接埠衝突，安裝程式會停止而不覆寫。

## 安裝

先將網域直接解析至伺服器，再執行：

```bash
sudo apt-get update && sudo apt-get install -y git make
git clone https://github.com/EriconYu/proxy-panel.git
cd proxy-panel
make configure
$EDITOR env.conf
make validate
make install
```

`make` 只是便捷入口。伺服器沒有 Make 時，等價指令仍是 `cp env.conf.example env.conf`、`chmod 600 env.conf`、編輯該檔案，然後執行 `sudo ./install.sh`。

`env.conf` 已被 Git 忽略。儲存庫範例刻意保留首次登入帳號 `admin / 111111`；這是公開密碼，登入後必須立即修改。只有從管理頁成功修改密碼後，初始密碼警告才會消失。

設定欄位：

| 設定項目 | 含義 |
| --- | --- |
| `DOMAIN` | 獨立公網網域；不能沿用已由其他 Nginx 站台占用的網域 |
| `ADMIN_USERNAME`、`ADMIN_PASSWORD` | 首次登入帳號；密碼可包含空格與 Shell 符號，但不能換行 |
| `TLS_MODE` | `certbot` 或 `existing` |
| `LETSENCRYPT_EMAIL` | `certbot` 模式必填 |
| `TLS_CERT_PATH`、`TLS_KEY_PATH` | `existing` 模式必填 |
| `PANEL_PORT`、`XRAY_API_PORT` | 尚未被占用的本機 TCP 連接埠 |
| `DEVICE_PORT_MIN`、`DEVICE_PORT_MAX` | 獨立本機連接埠範圍，最多 1,000 個；面板最多管理 100 台裝置 |
| `CHECK_DNS` | DNS 解析預檢；關閉它不會略過 Certbot 驗證 |
| `XRAY_VERSION` 及校驗值 | 固定版本及 AMD64/ARM64 必填完整性雜湊 |

使用 `TLS_MODE=certbot` 時，網域必須解析到本機，公網 TCP 80/443 必須可連線。使用 `TLS_MODE=existing` 時填入可讀取的憑證與私鑰絕對路徑；安裝程式只引用，不複製也不取得所有權。

## 日常維運

```bash
make help
make status
make backup
make restore BACKUP=/var/backups/proxy-panel/proxy-panel-TIMESTAMP.tar.gz
make update
make uninstall
```

`make deploy` 是 `make install` 的別名。部署設定不在儲存庫目錄時可傳入 `ENV=/path/to/env.conf`。Make 指令呼叫的仍是同一組已審閱腳本，也繼續支援直接執行腳本。

更新前會自動備份，再替換應用程式與服務檔案；不會暗中升級 Xray 或變更部署參數。解除安裝預設先備份，並保留套件、備份、TLS 憑證及所有無關的 Nginx 設定。

## 為什麼不能自動辨識手機型號

ClashMi 透過訂閱及代理協定連線時，不會提供可信的手機型號、系統身分或穩定硬體識別碼。因此，本工具將「每個獨立訂閱」定義為一台裝置：請為每部手機分別建立裝置，並匯入各自的 QR Code。同一訂閱若複製到多部手機，它們會合併統計與控制，之後無法僅靠網路流量可靠拆分。

## 安全與風險邊界

- 僅能用於你擁有或獲授權管理的基礎設施，並遵守所在地法律與服務商條款。本專案不提供法律意見，也不保證匿名性。
- 為相容 Clash，目前使用 VLESS/WebSocket。網路營運者仍可能辨識、限速或封鎖它；這不是「無法被封鎖」的保證。
- 管理密碼與每條訂閱 URL 都是存取憑證。必須使用 HTTPS。隨機管理路徑只是額外防護，不能取代身分驗證。
- `config.json` 與備份包含密碼雜湊、裝置 UUID、訂閱權杖。檔案權限為 `0600`，備份仍須另外安全保管。
- Web 程序不能任意執行 root 命令。特權輔助程式只接受嚴格驗證的裝置欄位，只寫入本工具擁有的 Xray、Nginx 片段與執行狀態，並只重啟自身服務及重新載入 Nginx。
- 限速規則只比對本工具分配給裝置的回環 TCP 連接埠，不應命中其他公網或回環流量。超速流量可能被丟棄，而非排隊。
- 額度每 10 秒檢查一次，停用前可能產生少量超額。統計值來自 Xray 使用者流量，不適合作為財務或鑑識計量。
- 修改裝置會重啟本工具的獨立 Xray 核心，其他面板裝置會短暫斷線；Nginx 重新載入是平順的，但作用於同一個 Nginx 執行個體。包含存取憑證的請求路徑不會寫入應用程式或 Nginx 存取日誌。
- 安裝會安裝系統套件並重新載入 Nginx，但不會修改防火牆。正式部署前應審閱腳本並建立伺服器快照。

漏洞回報與完整信任模型請見 [SECURITY.md](../SECURITY.md)。

## 檔案邊界

| 路徑 | 用途 |
| --- | --- |
| `/opt/proxy-panel` | 應用程式、Xray 執行檔及敏感狀態 |
| `/etc/proxy-panel` | 產生的 Xray 設定及非敏感安裝參數 |
| `/etc/nginx/sites-available/proxy-panel.conf` | 本工具擁有的 Nginx 站台 |
| `/etc/nginx/snippets/proxy-panel-devices.conf` | 嚴格驗證後產生的裝置路由 |
| `/var/lib/proxy-panel-runtime` | 限速執行狀態 |
| `/var/backups/proxy-panel` | 權限為 `0700` 的備份目錄 |

## 開發檢查

```bash
make test
make lint
make audit
# 或一次執行：make check
```

應用程式只使用 Python 標準函式庫；執行時透過系統 `qrencode` 指令產生 QR Code。專案採用 MIT 授權。
