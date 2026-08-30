@echo off
title VeriVault Launcher

REM ---------------------------------------------------------------------
REM  Starts the VeriVault web app and the camera supervisor together.
REM
REM  Two windows, not one, on purpose: both processes stream logs
REM  continuously, and you need to stop one without killing the other -
REM  restarting the supervisor on its own is how you pick up a camera
REM  you just added under Admin -> Cameras.
REM
REM  The web app goes first because importing app.py is what creates and
REM  migrates the database schema. The supervisor reads those tables on
REM  boot, so starting it first on a clean checkout gives you a crash.
REM ---------------------------------------------------------------------

cd /d "%~dp0"

echo.
echo  ==================================================================
echo    VeriVault  -  web app + camera supervisor
echo  ==================================================================
echo.

REM -- locate an interpreter -------------------------------------------
set "PY="
if exist "venv\Scripts\python.exe"  (set "PY=venv\Scripts\python.exe"  & goto :gotpy)
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe" & goto :gotpy)
py -3 --version >nul 2>&1
if not errorlevel 1 (set "PY=py -3" & goto :gotpy)
python --version >nul 2>&1
if not errorlevel 1 (set "PY=python" & goto :gotpy)
echo   [X] No Python interpreter found.
echo       Install Python 3.11+, or create a venv in this folder.
goto :fail
:gotpy
echo   [i] Interpreter : %PY%

REM -- the files we are about to run ------------------------------------
if not exist "app.py"        echo   [X] app.py not found in %CD%        & goto :fail
if not exist "supervisor.py" echo   [X] supervisor.py not found in %CD% & goto :fail

REM -- SECRET_KEY: app.py refuses to boot without one outside debug -----
set "HAVE_KEY="
if defined SECRET_KEY set "HAVE_KEY=1"
if not exist ".env" goto :keycheck
findstr /b /i /c:"SECRET_KEY=" ".env" >nul 2>&1
if not errorlevel 1 set "HAVE_KEY=1"
:keycheck
if not defined HAVE_KEY (
    echo.
    echo   [!] SECRET_KEY is not set, and no .env defines one.
    echo       app.py will refuse to start: that key signs session cookies
    echo       AND the rotating QR tokens, so a default would be forgeable.
    echo       Add SECRET_KEY=^<a long random string^> to .env, then rerun.
    echo.
    pause
)

REM -- 1/2  web app ------------------------------------------------------
echo   [1/2] Starting web app          ... http://localhost:5000
start "VeriVault - Web App" cmd /k "%PY% app.py"

REM -- wait for it to actually listen, rather than sleeping and hoping ---
echo   [..] Waiting for port 5000 ^(~30s: DeepFace pulls in TensorFlow^) ...
set /a TRIES=0
:waitloop
set /a TRIES+=1
netstat -an | findstr ":5000" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 goto :webup
if %TRIES% GEQ 180 goto :webtimeout
ping -n 2 127.0.0.1 >nul
goto :waitloop

:webtimeout
echo.
echo   [X] The web app did not open port 5000 within 3 minutes.
echo       Read the "VeriVault - Web App" window - the error is in there.
echo       Not starting the supervisor: it needs the schema app.py builds.
goto :fail

:webup
echo   [OK] Web app is listening.

REM -- 2/2  camera supervisor -------------------------------------------
echo   [2/2] Starting camera supervisor ... (engine + one worker per camera)
start "VeriVault - Camera Supervisor" cmd /k "%PY% supervisor.py"

echo.
echo  ==================================================================
echo    Both running, in their own windows.
echo.
echo    Portal      : http://localhost:5000
echo    Web App     : window "VeriVault - Web App"
echo    Supervisor  : window "VeriVault - Camera Supervisor"
echo.
echo    No cameras yet? Add one under Admin -^> Cameras, then restart
echo    the supervisor window - it reads the camera list once, on boot.
echo.
echo    Stop either one with Ctrl+C in its own window.
echo  ==================================================================
echo.
echo  This launcher window can be closed. Press any key.
pause >nul
exit /b 0

:fail
echo.
echo  Startup aborted.
pause
exit /b 1
