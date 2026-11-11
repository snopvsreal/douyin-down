@echo off
REM Mo giao dien dytool va tu bat chay tu dong. Dong cua so nay = tat tool.
REM Muon tu chay khi dang nhap Windows: tao shortcut file nay trong thu muc shell:startup
cd /d "%~dp0.."
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
start "" http://127.0.0.1:8000
uv run dytool ui --autostart
pause
