@echo off
REM AWS 95 Tools - Windows launcher. Double-click to run.
cd /d "%~dp0"
where uv >nul 2>nul
if %errorlevel%==0 (
    uv run python -m aws95 %*
) else (
    .venv\Scripts\python.exe -m aws95 %*
)
