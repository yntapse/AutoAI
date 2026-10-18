@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set LOG=%~dp0logs\restart_backend.log
if not exist "%~dp0logs" mkdir "%~dp0logs"
echo ==== restart_backend %DATE% %TIME% ==== > "%LOG%"

echo STEP 1 - listeners on port 8000 BEFORE >> "%LOG%"
netstat -ano | findstr /R /C:":8000 .*LISTENING" >> "%LOG%" 2>&1

rem uvicorn --reload runs a reloader AND a worker; either may own the socket.
rem Kill every owner, re-check, repeat until the port is really free.
for /L %%i in (1,1,5) do (
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do (
        echo   pass %%i: taskkill /F /T /PID %%p >> "%LOG%"
        taskkill /F /T /PID %%p >> "%LOG%" 2>&1
    )
    timeout /t 1 /nobreak >nul
)

echo STEP 2 - listeners on port 8000 AFTER kill >> "%LOG%"
netstat -ano | findstr /R /C:":8000 .*LISTENING" >> "%LOG%" 2>&1
if errorlevel 1 echo   PORT 8000 IS FREE >> "%LOG%"

set PY=python
if exist "%~dp0..\..\venv\Scripts\python.exe" set PY="%~dp0..\..\venv\Scripts\python.exe"
echo STEP 3 - python that will run the backend: %PY% >> "%LOG%"
%PY% -c "import fastapi, uvicorn, openai, pyodbc; print('deps OK fastapi', fastapi.__version__, 'uvicorn', uvicorn.__version__)" >> "%LOG%" 2>&1

echo STEP 4 - starting ONE backend in a new window >> "%LOG%"
start "AutoAI backend" cmd /k %PY% -m uvicorn main:app --host localhost --port 8000 --reload

timeout /t 25 /nobreak >nul

echo STEP 5 - listeners on port 8000 after start >> "%LOG%"
netstat -ano | findstr /R /C:":8000 .*LISTENING" >> "%LOG%" 2>&1
echo STEP 6 - GET /__build >> "%LOG%"
curl -s http://localhost:8000/__build >> "%LOG%" 2>&1
echo. >> "%LOG%"
echo ==== done ==== >> "%LOG%"
type "%LOG%"
timeout /t 8 >nul
