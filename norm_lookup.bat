@echo off
setlocal EnableExtensions
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul

cd /d "%~dp0"

set "PY=python"
if exist "%~dp0venv312\Scripts\python.exe" set "PY=%~dp0venv312\Scripts\python.exe"

set "QUERY=%~1"
echo MR Norm norm-lookup interactive, no doc_name filter
echo.

if not "%QUERY%"=="" goto with_query

"%PY%" -m mr_norm.apps.main norm-lookup --no-doc-filter %*
exit /b %ERRORLEVEL%

:with_query
shift
"%PY%" -m mr_norm.apps.main norm-lookup --no-doc-filter --query "%QUERY%" %*
exit /b %ERRORLEVEL%
