PYTHON ?= python3
PIPX_BIN ?= pipx
VERSION := $(shell $(PYTHON) -c "import configparser;cfg=configparser.ConfigParser();cfg.read('setup.cfg');print(cfg['metadata']['version'])")

build:
	$(PYTHON) -m build

zipapp:
	rm -rf dist/pyz-src && mkdir -p dist/pyz-src
	rsync -a --delete --exclude '.git' --exclude 'dist' --exclude '.tasks' --exclude '.tmp' --exclude '__pycache__' --exclude 'tests' ./ dist/pyz-src
	$(PYTHON) -m zipapp dist/pyz-src -m "tasks:main" -o dist/apply_task-$(VERSION).pyz -p "/usr/bin/env python3" -c

smoke:
	PIPX_HOME=/tmp/pipxhome PIPX_BIN_DIR=/tmp/pipxbin $(PIPX_BIN) install --force dist/apply_task-$(VERSION)-py3-none-any.whl
	PATH=/tmp/pipxbin:$$PATH apply_task help

test:
	pytest -q

# GUI (Tauri) helpers — for humans
gui-dev:
	cd gui && pnpm install && pnpm tauri dev

gui-build:
	cd gui && pnpm install && pnpm tauri build

checksums:
	cd dist && find . -maxdepth 1 -type f -print0 | xargs -0 sha256sum > SHA256SUMS

.PHONY: build zipapp smoke test checksums gui-dev gui-build
