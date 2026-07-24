#!/bin/bash
# Mac 打包脚本 — 生成 BiliLiveMonitor.app
# 用法: bash build_mac.sh

set -e

echo "=== B站直播数据监控 - Mac 打包 ==="

PYTHON=$(which python3.12 2>/dev/null || which python3)
echo "Python: $PYTHON ($($PYTHON --version))"

echo "安装依赖..."
$PYTHON -m pip install --break-system-packages -r requirements.txt -r requirements-build.txt 2>/dev/null || \
$PYTHON -m pip install -r requirements.txt -r requirements-build.txt

echo "开始打包..."
$PYTHON -m PyInstaller \
    --onefile \
    --windowed \
    --name "BiliLiveMonitor" \
    --add-data "live_monitor_core.py:." --add-data "live_monitor_notify.py:." \
    --collect-submodules "bilibili_api" \
    --hidden-import "aiohttp" \
    --hidden-import "yarl" \
    --hidden-import "multidict" \
    --clean \
    --noconfirm \
    live_monitor_gui.py

echo ""
echo "打包完成！输出: dist/BiliLiveMonitor.app"
echo "可直接双击运行或分发给用户"
ls -lh dist/BiliLiveMonitor* 2>/dev/null || ls -lh dist/
