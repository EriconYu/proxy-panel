SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV ?= env.conf
BACKUP ?=

.PHONY: help configure validate install deploy status logs restart backup restore update uninstall test lint audit check

help:
	@printf '%s\n' \
	  'Proxy Panel commands / 简体中文 / 繁體中文' \
	  '' \
	  '  make configure  Create local env.conf / 创建本地配置 / 建立本機設定' \
	  '  make validate   Validate env.conf / 校验部署配置 / 驗證部署設定' \
	  '  make install    Install on this server / 安装到本机 / 安裝至本機' \
	  '  make deploy     Alias for make install / 安装别名 / 安裝別名' \
	  '  make status     Show service status / 查看服务状态 / 查看服務狀態' \
	  '  make logs       Follow service logs / 持续查看日志 / 持續查看日誌' \
	  '  make restart    Restart owned services / 重启本工具服务 / 重啟本工具服務' \
	  '  make backup     Create protected backup / 创建安全备份 / 建立安全備份' \
	  '  make restore BACKUP=/absolute/file.tar.gz' \
	  '                   Restore backup / 恢复备份 / 還原備份' \
	  '  make update     Pull and update / 拉取并更新 / 拉取並更新' \
	  '  make uninstall  Backup and remove / 备份后卸载 / 備份後解除安裝' \
	  '  make test       Run tests / 运行测试 / 執行測試' \
	  '  make lint       Run ShellCheck / 运行静态检查 / 執行靜態檢查' \
	  '  make audit      Scan public content / 扫描公开内容 / 掃描公開內容' \
	  '  make check      Run all local checks / 运行全部检查 / 執行全部檢查' \
	  '' \
	  'Options / 参数 / 參數:' \
	  '  ENV=path        Config file (default: env.conf)' \
	  '  BACKUP=path     Backup output or restore input path'

configure:
	@if [[ -e "$(ENV)" ]]; then \
	  printf 'Keeping existing %s; no changes made.\n' "$(ENV)"; \
	else \
	  install -m 600 env.conf.example "$(ENV)"; \
	  printf 'Created %s. Edit it before installation.\n' "$(ENV)"; \
	fi

validate:
	@bash -c 'source scripts/lib.sh; load_env "$$1"; validate_config; printf "Configuration is valid.\n"' _ "$(ENV)"

install: validate
	sudo ./install.sh "$(ENV)"

deploy: install

status:
	sudo ./scripts/status.sh

logs:
	sudo journalctl -u proxy-panel.service -u proxy-panel-xray.service -f

restart:
	sudo systemctl restart proxy-panel-xray.service proxy-panel-shaping.service proxy-panel.service

backup:
	sudo ./scripts/backup.sh "$(BACKUP)"

restore:
	@test -n "$(BACKUP)" || { printf 'Set BACKUP to an absolute backup path.\n' >&2; exit 2; }
	@[[ "$(BACKUP)" == /* ]] || { printf 'BACKUP must be an absolute path.\n' >&2; exit 2; }
	sudo ./scripts/restore.sh "$(BACKUP)"

update:
	git pull --ff-only
	sudo ./scripts/update.sh

uninstall:
	sudo ./scripts/uninstall.sh

test:
	python3 -m unittest discover -s tests -v
	bash tests/test_env.sh

lint:
	@command -v shellcheck >/dev/null || { printf 'shellcheck is required.\n' >&2; exit 2; }
	bash -n install.sh scripts/*.sh tests/*.sh
	shellcheck -x install.sh scripts/*.sh tests/*.sh

audit:
	bash tests/public_audit.sh
	@if command -v gitleaks >/dev/null; then \
	  gitleaks git --config .gitleaks.toml --no-banner --redact; \
	else \
	  printf 'gitleaks is not installed; CI still performs the full secret scan.\n'; \
	fi

check: test lint audit
