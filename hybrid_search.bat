@echo off
setlocal
if exist "%SystemRoot%\System32\chcp.com" "%SystemRoot%\System32\chcp.com" 65001 >nul

set "QUERY=%~1"
if "%QUERY%"=="" (
  set /p "QUERY=Enter search query: "
)
if "%QUERY%"=="" (
  echo Search query is required.
  exit /b 1
)

set "COLLECTION=%~2"
if "%COLLECTION%"=="" set "COLLECTION=mr_norm_docs_bge_m3"

set "LIMIT=%~3"
if "%LIMIT%"=="" set "LIMIT=5"

echo Running hybrid retrieval compare
echo Query: %QUERY%
echo Collection: %COLLECTION%
echo Limit: %LIMIT%
echo.

python -m mr_norm.apps.main retrieval-compare ^
  --collection-name "%COLLECTION%" ^
  --query "%QUERY%" ^
  --pipelines payload,vector,hybrid ^
  --limit %LIMIT% ^
  --save-report

exit /b %ERRORLEVEL%
