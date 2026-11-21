@echo off
REM Mo giao dien dytool va tu bat chay tu dong. Dong cua so nay = tat tool.
REM Muon tu chay khi dang nhap Windows: tao shortcut file nay trong thu muc shell:startup
REM Neu app desktop dytool dang mo thi KHONG chay them file nay (2 ban cung luc chi 1 ban duoc chay tu dong).
cd /d "%~dp0.."
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
REM Python cua uv nam trong thu muc du an (may chan chay exe trong AppData)
set UV_PYTHON_INSTALL_DIR=%~dp0..\.python
REM Trinh duyet dang nhap / dang bai = ShardBrowser (dytool tu mo neu chua chay)
start "" http://127.0.0.1:8000
REM --no-sync: khong dong bo lai moi truong (giu ban torch GPU neu da cai)
uv run --no-sync dytool ui --autostart
pause
