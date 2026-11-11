@echo off
setlocal
rem 鉴己 · 笔记洞察 · 桌面版（PyQt6）开发模式启动器
rem 需要已装 PyQt6 + PyQt6-WebEngine（pip install PyQt6 PyQt6-WebEngine）
cd /d "%~dp0"
python main.py %*
if errorlevel 1 pause
