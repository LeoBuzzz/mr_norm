@echo off
setlocal EnableExtensions
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul

cd /d "%~dp0"

set "PY=python"
if exist "%~dp0venv312\Scripts\python.exe" set "PY=%~dp0venv312\Scripts\python.exe"

set QDRANT_HOST=localhost
set QDRANT_PORT=6333
set MR_NORM_QDRANT_COLLECTION=mr_norm_docs_bge_m3
set RAG_EMBEDDING_DEVICE=cpu

"%PY%" -c "import vkbottle" 2>nul || (
    echo vkbottle not found, installing...
    "%PY%" -m pip install -r requirements-vk.txt
)

echo Starting MR Norm VK bot...
"%PY%" -m mr_norm.apps.vk_bot
exit /b %ERRORLEVEL%
