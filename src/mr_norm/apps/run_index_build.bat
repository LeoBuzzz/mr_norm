@echo off
REM Эмбеддинги + upsert в Qdrant (нужен venv312 с CUDA).
REM PowerShell из корня mr_norm: .\src\mr_norm\apps\run_index_build.bat
REM Сначала один раз: .\scripts\setup_venv312.bat

set "ROOT=%~dp0..\..\.."
cd /d "%ROOT%"

if not exist "%ROOT%\venv312\Scripts\python.exe" (
    echo venv312 not found — creating via scripts\setup_venv312.bat ...
    call "%ROOT%\scripts\setup_venv312.bat"
    if errorlevel 1 exit /b 1
    if not exist "%ROOT%\venv312\Scripts\python.exe" (
        echo ERROR: venv312 still missing after setup.
        exit /b 1
    )
)

set "PY=%ROOT%\venv312\Scripts\python.exe"
echo Using venv312
echo Project root: %ROOT%

REM GPU (как в rag_norm). Для CPU: set RAG_EMBEDDING_DEVICE=cpu
if not defined RAG_EMBEDDING_DEVICE set "RAG_EMBEDDING_DEVICE=cuda:0"
echo RAG_EMBEDDING_DEVICE=%RAG_EMBEDDING_DEVICE%
if defined MR_NORM_QDRANT_COLLECTION echo MR_NORM_QDRANT_COLLECTION=%MR_NORM_QDRANT_COLLECTION%

echo.
echo === CUDA check ===
"%PY%" -c "import torch; ok=torch.cuda.is_available(); print('cuda available:', ok); print('device:', torch.cuda.get_device_name(0) if ok else '-'); import sys; sys.exit(0 if ok or '%RAG_EMBEDDING_DEVICE%'=='cpu' else 1)"
if errorlevel 1 (
    echo ERROR: CUDA not available but RAG_EMBEDDING_DEVICE=%RAG_EMBEDDING_DEVICE%
    echo Fix: install NVIDIA driver, re-run scripts\setup_venv312.bat, or set RAG_EMBEDDING_DEVICE=cpu
    exit /b 1
)

echo.
echo Running index-build...
"%PY%" -m mr_norm.apps.main index-build %*
if errorlevel 1 exit /b %errorlevel%

echo.
echo Running index-verify...
"%PY%" -m mr_norm.apps.main index-verify %*
exit /b %errorlevel%
