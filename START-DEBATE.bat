@echo off
rem Audible Debate: double-click to start. Opens http://127.0.0.1:4797/ in your browser.
rem The server runs hidden in the background; use "Stop server" on the page to close it.
setlocal
cd /d "%~dp0"
set "URL=http://127.0.0.1:4797/"

rem Already running? Just open the page.
powershell -NoProfile -Command "try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 '%URL%api/status' | Out-Null; exit 0}catch{exit 1}" >nul 2>&1
if %errorlevel%==0 goto open

set "PYW="
where pythonw >nul 2>&1 && set "PYW=pythonw"
if not defined PYW (
  where pyw >nul 2>&1 && set "PYW=pyw -3"
)
if not defined PYW (
  echo.
  echo  Python was not found on this computer.
  echo  Install Python 3.10 or newer from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  echo  Then double-click START-DEBATE again.
  echo.
  pause
  exit /b 1
)

echo Starting Audible Debate in the background...
start "" /b %PYW% -m audible_debate serve

rem Wait up to 20 s for the page to answer.
for /l %%i in (1,1,20) do (
  powershell -NoProfile -Command "try{Invoke-WebRequest -UseBasicParsing -TimeoutSec 1 '%URL%api/status' | Out-Null; exit 0}catch{exit 1}" >nul 2>&1
  if not errorlevel 1 goto open
  timeout /t 1 /nobreak >nul
)
echo.
echo  The debate server did not start. The reason is written in:
echo  %~dp0.debate-server.log
echo  Send that file to Jimmy.
echo.
pause
exit /b 1

:open
start "" "%URL%"
exit /b 0
