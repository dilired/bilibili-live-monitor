#!/bin/bash
# Mac 打包脚本 — 生成 NookLiveMonitor.app
# 用法: bash build_mac.sh

set -e

echo "=== Nook's Live Monitor - Mac 打包 ==="

PYTHON=$(which python3.12 2>/dev/null || which python3)
echo "Python: $PYTHON ($($PYTHON --version))"

echo "安装依赖..."
$PYTHON -m pip install --break-system-packages bilibili-api-python aiohttp pyinstaller 2>/dev/null || \
$PYTHON -m pip install bilibili-api-python aiohttp pyinstaller

echo "开始打包..."
$PYTHON -m PyInstaller \
    --onefile \
    --windowed \
    --name "NookLiveMonitor" \
    --add-data "live_monitor_core.py:." \
    --hidden-import "aiohttp" \
    --hidden-import "aiohttp.client" \
    --hidden-import "aiohttp.client_ws" \
    --hidden-import "aiohttp.cookiejar" \
    --hidden-import "yarl" \
    --hidden-import "multidict" \
    --clean \
    --noconfirm \
    live_monitor_gui.py

echo ""
echo "打包完成！输出: dist/NookLiveMonitor.app"
echo "可直接双击运行或分发给用户"
ls -lh dist/NookLiveMonitor* 2>/dev/null || ls -lh dist/
