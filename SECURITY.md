# Security Policy / 安全政策 / 安全政策

## English

### Reporting

Do not publish an exploitable vulnerability or real subscription URL in a public issue. Use GitHub's private vulnerability reporting for this repository. Include the affected revision, reproduction steps, impact, and a minimal sanitized proof of concept. Do not test against systems you do not own.

### Trust model

The public Internet is untrusted. Nginx terminates TLS and forwards only the randomized admin prefix, subscription endpoints, and generated WebSocket paths. The panel process runs as an unprivileged user. Its only privileged capability is the exact passwordless command that starts `proxy-panel-apply.service`.

The root apply helper treats panel state as hostile input. It accepts at most 100 devices and validates every ID, UUID, internal port, WebSocket path, email tag, Boolean state, and speed before generating files. It writes fixed paths only. Compromise of the host root account, Nginx, Xray binary, Git checkout used for an update, DNS, TLS private key, or a device subscription is outside this boundary.

Supported security fixes target the latest commit only. Pin a reviewed revision for production and verify changes before `scripts/update.sh`.

## 简体中文

### 漏洞报告

不要在公开 Issue 中发布可直接利用的漏洞或真实订阅 URL。请使用本仓库的 GitHub 私密漏洞报告，并附上受影响版本、复现步骤、影响和最小化的脱敏验证样例。不要测试你不拥有的系统。

### 信任模型

公网是不可信边界。Nginx 终止 TLS，只转发随机管理路径、订阅端点和生成的 WebSocket 路径。面板以非特权用户运行，唯一的提权能力是免密启动 `proxy-panel-apply.service` 的精确命令。

root 辅助程序把面板状态当作不可信输入：最多接受 100 台设备，并逐项校验 ID、UUID、本地端口、WebSocket 路径、统计标签、启用状态和限速值，然后才生成固定路径下的文件。主机 root、Nginx、Xray 二进制、用于更新的 Git 工作区、DNS、TLS 私钥或设备订阅一旦失陷，均超出本边界。

安全修复只针对最新提交。生产环境应固定到已审阅的版本，并在执行 `scripts/update.sh` 前检查变更。

## 繁體中文

### 漏洞回報

不要在公開 Issue 發布可直接利用的漏洞或真實訂閱 URL。請使用本儲存庫的 GitHub 私密漏洞回報，並附上受影響版本、重現步驟、影響及最小化的去識別驗證範例。不要測試你不擁有的系統。

### 信任模型

公網是不可信邊界。Nginx 終止 TLS，只轉送隨機管理路徑、訂閱端點及產生的 WebSocket 路徑。面板以非特權使用者執行，唯一的提權能力是免密啟動 `proxy-panel-apply.service` 的精確命令。

root 輔助程式將面板狀態視為不可信輸入：最多接受 100 台裝置，逐項驗證 ID、UUID、本機連接埠、WebSocket 路徑、統計標籤、啟用狀態及限速值，之後才產生固定路徑下的檔案。主機 root、Nginx、Xray 執行檔、用於更新的 Git 工作區、DNS、TLS 私鑰或裝置訂閱一旦失陷，皆超出此邊界。

安全修正僅支援最新提交。正式環境應固定至已審閱的版本，並在執行 `scripts/update.sh` 前檢查變更。
