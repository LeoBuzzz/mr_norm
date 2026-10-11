@echo off
REM Эмбеддинги + upsert в Qdrant на RTX 4090 (cuda:0).
REM PowerShell из корня mr_norm: .\src\mr_norm\apps\run_index_build.bat

set "ROOT=%~dp0..\..\.."
cd /d "%ROOT%"

set "RAG_EMBEDDING_DEVICE=cuda:0"
set "PY="

if exist "%ROOT%\venv312\Scripts\python.exe" (
    echo Checking venv312 for RTX 4090 support...
    call :check_rtx4090 "%ROOT%\venv312\Scripts\python.exe"
    if not errorlevel 1 set "PY=%ROOT%\venv312\Scripts\python.exe"
)

if not defined PY (
    echo venv312 is not CUDA-capable; checking system Python...
    call :check_rtx4090 "python"
    if not errorlevel 1 set "PY=python"
)

if not defined PY (
    echo ERROR: no Python environment with CUDA and NVIDIA GeForce RTX 4090 was found.
    echo Fix venv312 with .\scripts\setup_venv312.bat and retry.
    exit /b 1
)

echo Using Python: %PY%
echo Project root: %ROOT%
echo RAG_EMBEDDING_DEVICE=%RAG_EMBEDDING_DEVICE%
if defined MR_NORM_QDRANT_COLLECTION echo MR_NORM_QDRANT_COLLECTION=%MR_NORM_QDRANT_COLLECTION%

echo.
echo Running index-build...
"%PY%" -m mr_norm.apps.main index-build --rebuild %*
if errorlevel 1 exit /b %errorlevel%

echo.
echo Running index-verify...
"%PY%" -m mr_norm.apps.main index-verify %*
exit /b %errorlevel%

:check_rtx4090
"%~1" -c "import sys, torch; available=torch.cuda.is_available(); name=torch.cuda.get_device_name(0) if available else '-'; print('torch:', torch.__version__); print('cuda available:', available); print('cuda:0:', name); sys.exit(0 if available and 'RTX 4090' in name else 1)"
exit /b %errorlevel%
