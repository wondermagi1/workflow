@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (py -3 workflow.py --open & pause & exit /b)
where python >nul 2>nul
if not errorlevel 1 (python workflow.py --open & pause & exit /b)
if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" (
  "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" workflow.py --open
) else (
  echo 请先安装 Python 3.10 或更新版本，并勾选 Add Python to PATH。
)
pause
