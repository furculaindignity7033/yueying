@echo off
chcp 65001 >nul
setlocal
echo === yueying install ===
echo.

set "ROOT=%~dp0"
set "PY="
for %%P in (python py) do (
  if not defined PY (
    %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1 && set "PY=%%P"
  )
)
if not defined PY (
  echo Python 3.9+ not found. Install from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)

set "VENV=%LOCALAPPDATA%\yueying\venv"
echo [1/3] Python venv: %VENV%
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" || (echo venv failed & pause & exit /b 1)

echo [2/3] pip install yueying (faster-whisper, yt-dlp, ffmpeg) - a few hundred MB, please wait
"%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip
if exist "%ROOT%pyproject.toml" (
  "%VENV%\Scripts\python.exe" -m pip install -q "%ROOT%."
) else (
  "%VENV%\Scripts\python.exe" -m pip install -q yueying
)
if errorlevel 1 (echo install failed & pause & exit /b 1)
nvidia-smi >nul 2>&1 && (
  echo       NVIDIA GPU detected, installing CUDA runtime for fast transcription
  "%VENV%\Scripts\python.exe" -m pip install -q nvidia-cublas-cu12 nvidia-cudnn-cu12
)

echo [3/3] Claude Code skill -> %USERPROFILE%\.claude\skills\yueying
"%VENV%\Scripts\yueying.exe" --install-skill

set "BIN=%LOCALAPPDATA%\yueying\bin"
if not exist "%BIN%" mkdir "%BIN%"
> "%BIN%\yueying.cmd" echo @"%VENV%\Scripts\yueying.exe" %%*

echo.
echo Done.
echo   CLI:         "%BIN%\yueying.cmd" ^<video file or URL^>
echo   Claude Code: just ask it to look at a video file or link.
echo   First transcription downloads the model (large-v3-turbo, ~1.6 GB); everything runs offline afterwards.
echo.
pause
