@echo off
REM Windows 打包脚本 — 生成 BiliLiveMonitor.exe
REM 用法: 双击运行或在命令行执行 build_windows.bat
echo === B站直播数据监控 - Windows 打包 ===
echo.

echo 安装依赖...
pip install bilibili-api-python aiohttp certifi matplotlib pyinstaller

echo.
echo 开始打包...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "BiliLiveMonitor" ^
    --add-data "live_monitor_core.py;." --add-data "live_monitor_notify.py;." ^
    --collect-submodules "bilibili_api" ^
    --hidden-import "aiohttp" ^
    --hidden-import "yarl" ^
    --hidden-import "multidict" ^
    --clean ^
    --noconfirm ^
    live_monitor_gui.py

echo.
echo 打包完成！输出: dist\BiliLiveMonitor.exe
echo 可直接双击运行或分发给用户
dir dist\BiliLiveMonitor* 2>nul || dir dist\
pause
