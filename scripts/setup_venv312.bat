@echo off
REM Создать venv312 с PyTorch+CUDA для index-build (аналог rag_norm).
REM В PowerShell из корня mr_norm: .\scripts\setup_venv312.bat
cd /d "%~dp0.."
set "ROOT=%CD%"

echo Project root: %ROOT%

if exist "%ROOT%\venv312\Scripts\python.exe" (
    echo venv312 already exists — upgrading packages...
) else (
    echo Creating venv312...
    where py >nul 2>&1
    if %errorlevel%==0 (
        py -3.12 -m venv "%ROOT%\venv312" 2>nul
        if errorlevel 1 py -3.11 -m venv "%ROOT%\venv312" 2>nul
        if errorlevel 1 (
            echo py -3.12 / -3.11 failed, trying default python -m venv
            python -m venv "%ROOT%\venv312"
        )
    ) else (
        python -m venv "%ROOT%\venv312"
    )
    if errorlevel 1 (
        echo ERROR: could not create venv312
        exit /b 1
    )
)

call "%ROOT%\venv312\Scripts\activate.bat"
python -m pip install --upgrade pip

echo Installing mr_norm package...
pip install -e "%ROOT%[rtf]"

echo Installing PyTorch with CUDA (cu124)...
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124

echo Installing GPU indexing dependencies...
pip install -r "%ROOT%\requirements-gpu.txt"

echo.
echo === CUDA check (venv312) ===
python -c "import torch; print('torch', torch.__version__); print('cuda built', torch.version.cuda); print('cuda available', torch.cuda.is_available()); print('device', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '-')"

echo.
echo Done. Run indexing: .\src\mr_norm\apps\run_index_build.bat
exit /b 0
