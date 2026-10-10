@echo off
setlocal
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if exist "%~dp0venv312\Scripts\python.exe" (
    "%~dp0venv312\Scripts\python.exe" -c "import pymorphy3, qdrant_client, sentence_transformers" >nul 2>&1
    if not errorlevel 1 (
        "%~dp0venv312\Scripts\python.exe" "%~dp0sandbox\question_parser\router2_cli.py" %*
        exit /b %errorlevel%
    )
)
python "%~dp0sandbox\question_parser\router2_cli.py" %*
exit /b %errorlevel%
