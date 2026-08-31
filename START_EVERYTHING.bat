@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Anonymous RPG - Launcher

set "BOT_DIR=%~dp0anonymous_bot"
set "WEB_URL=http://127.0.0.1:18474"

if not exist "%BOT_DIR%\bot.py" (
  echo [ERROR] anonymous_bot\bot.py was not found.
  pause
  exit /b 1
)

set "PYTHON="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON="%~dp0.venv\Scripts\python.exe""
if not defined PYTHON (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON=py -3"
)
if not defined PYTHON (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>&1
  if not errorlevel 1 set "PYTHON=python"
)
if not defined PYTHON (
  echo [ERROR] A working Python 3.11+ installation was not found.
  echo Install Python from python.org, then run START_EVERYTHING again.
  pause
  exit /b 1
)

echo Checking Python dependencies...
%PYTHON% -c "import discord, dotenv" >nul 2>&1
if errorlevel 1 (
  %PYTHON% -m pip install -r "%BOT_DIR%\requirements.txt"
  if errorlevel 1 (
    echo [ERROR] Could not install the required Python packages.
    pause
    exit /b 1
  )
)

where ollama >nul 2>&1
if not errorlevel 1 (
  curl.exe -fsS --max-time 2 http://127.0.0.1:11434/api/tags >nul 2>&1
  if errorlevel 1 start "Anonymous RPG - Ollama" /B ollama serve
)

for /f "delims=" %%R in ('powershell -NoProfile -Command "try { $r=Invoke-RestMethod -TimeoutSec 2 '%WEB_URL%/healthz'; if($r.discord_ready -and $r.guild_ready){'READY'}else{'BUSY'} } catch {'FREE'}"') do set "WEB_STATE=%%R"
if /I "%WEB_STATE%"=="READY" (
  echo Anonymous RPG is already connected to Discord. Opening the existing website.
  start "" "%WEB_URL%"
  exit /b 0
)
if /I "%WEB_STATE%"=="BUSY" (
  echo [ERROR] Port 18474 is being used by an older or disconnected website process.
  echo Close the old Anonymous RPG bot/window first, then run START_EVERYTHING again.
  pause
  exit /b 1
)

echo Starting the Discord bot and local website...
start "Anonymous RPG - Bot" /D "%~dp0" cmd.exe /k %PYTHON% -m anonymous_bot.bot

set /a WAIT_SECONDS=0
:WAIT_FOR_DISCORD
powershell -NoProfile -Command "try { $r=Invoke-RestMethod -TimeoutSec 2 '%WEB_URL%/healthz'; if($r.ok -and $r.discord_ready -and $r.guild_ready){exit 0}else{exit 1} } catch {exit 1}" >nul 2>&1
if not errorlevel 1 goto DISCORD_READY
set /a WAIT_SECONDS+=1
if %WAIT_SECONDS% GEQ 75 goto DISCORD_TIMEOUT
timeout /t 1 /nobreak >nul
goto WAIT_FOR_DISCORD

:DISCORD_READY
start "" "%WEB_URL%"
echo The Discord bot, campaign guild, and website are connected.
exit /b 0

:DISCORD_TIMEOUT
echo [ERROR] Discord did not connect to the configured campaign server within 75 seconds.
echo The website was not treated as healthy. Check the "Anonymous RPG - Bot" window for the real startup error.
pause
exit /b 1
