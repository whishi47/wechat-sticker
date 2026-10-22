@echo off
setlocal
set "HERE=%~dp0"
if exist "%HERE%.venv\Scripts\python.exe" (
  "%HERE%.venv\Scripts\python.exe" "%HERE%wxpost.py" %*
) else (
  python "%HERE%wxpost.py" %*
)
