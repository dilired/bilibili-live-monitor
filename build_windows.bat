@echo off
REM Windows 打包脚本 — 生成 NookLiveMonitor.exe
REM 用法: 双击运行或在命令行执行 build_windows.bat
echo === Nook's Live Monitor - Windows 打包 ===
echo.

echo 安装依赖...
pip install bilibili-api-python aiohttp pyinstaller

echo.
echo 开始打包...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "NookLiveMonitor" ^
    --add-data "live_monitor_core.py;." ^
    --hidden-import "aiohttp" ^
    --hidden-import "aiohttp.client" ^
    --hidden-import "aiohttp.client_ws" ^
    --hidden-import "aiohttp.cookiejar" ^
    --hidden-import "yarl" ^
    --hidden-import "multidict" ^
    --clean ^
    --noconfirm ^
    live_monitor_gui.py

echo.
echo 打包完成！输出: dist\NookLiveMonitor.exe
echo 可直接双击运行或分发给用户
dir dist\NookLiveMonitor* 2>nul || dir dist\
pause
