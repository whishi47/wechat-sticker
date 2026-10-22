@echo off
rem wxpost installer for Windows: create venv, install deps, deploy SKILL.md
setlocal
set "HERE=%~dp0"
echo ==^> wxpost @ %HERE%

if not exist "%HERE%.venv\Scripts\python.exe" (
  echo ==^> creating virtualenv
  python -m venv "%HERE%.venv"
  if errorlevel 1 (
    echo !! failed to create venv. Check that Python 3.9+ is on PATH.
    exit /b 1
  )
)

set "PY=%HERE%.venv\Scripts\python.exe"

echo ==^> installing dependencies
"%PY%" -m pip install -q --disable-pip-version-check requests Pillow

echo ==^> deploying SKILL.md
if not exist "%USERPROFILE%\.workbuddy\skills\wxpost" mkdir "%USERPROFILE%\.workbuddy\skills\wxpost"
copy /Y "%HERE%SKILL.md" "%USERPROFILE%\.workbuddy\skills\wxpost\SKILL.md" >nul
echo     SKILL.md -^> %USERPROFILE%\.workbuddy\skills\wxpost
if not exist "%USERPROFILE%\.claude\skills\wxpost" mkdir "%USERPROFILE%\.claude\skills\wxpost"
copy /Y "%HERE%SKILL.md" "%USERPROFILE%\.claude\skills\wxpost\SKILL.md" >nul
echo     SKILL.md -^> %USERPROFILE%\.claude\skills\wxpost

echo.
echo Done. Next steps:
echo   %HERE%wxpost.cmd init
echo   %HERE%wxpost.cmd check
endlocal
