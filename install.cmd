@echo off
chcp 65001 >nul
setlocal
echo === yueying install (Windows, no uv needed) ===
echo.

set "ROOT=%~dp0"
set "PY="
for %%P in (python py) do (
  if not defined PY (
    %%P -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=%%P"
  )
)
if not defined PY (
  echo Python 3.10+ not found. Install from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  echo Or skip this script and use uv instead:  winget install astral-sh.uv  then  uvx yueying mcp --setup
  pause & exit /b 1
)

set "VENV=%LOCALAPPDATA%\yueying\venv"
echo [1/4] Python venv: %VENV%
if not exist "%VENV%\Scripts\python.exe" %PY% -m venv "%VENV%" || (echo venv failed & pause & exit /b 1)

echo [2/4] pip install yueying (faster-whisper, yt-dlp, ffmpeg, mcp) - a few hundred MB, please wait
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

echo [3/4] Claude Code skill -^> %USERPROFILE%\.claude\skills\yueying
"%VENV%\Scripts\yueying.exe" --install-skill

set "BIN=%LOCALAPPDATA%\yueying\bin"
if not exist "%BIN%" mkdir "%BIN%"
> "%BIN%\yueying.cmd" echo @"%VENV%\Scripts\yueying.exe" %%*
> "%BIN%\yueying-mcp.cmd" echo @"%VENV%\Scripts\yueying-mcp.exe" %%*

echo [4/4] MCP setup: GPU probe, speech-model download, 2-second smoke test (needs network once)
"%VENV%\Scripts\yueying.exe" mcp --setup
if errorlevel 1 echo       setup reported a problem - see the messages above; the server can still be configured.

set "MCP_EXE=%VENV%\Scripts\yueying-mcp.exe"
rem JSON needs escaped backslashes, but cmd.exe cannot reliably turn \ into \\ (the %%V:\=\\%% form yields a single
rem backslash on current builds). Forward slashes are valid JSON and accepted by Windows CreateProcess.
set "MCP_JSON=%MCP_EXE:\=/%"
echo.
echo Done.
echo   CLI:           "%BIN%\yueying.cmd" ^<video file or URL^>
echo   MCP server:    "%MCP_EXE%"
echo   Claude Code:   claude mcp add --transport stdio --scope user yueying --env PYTHONUTF8=1 -- "%MCP_EXE%"
echo   Claude Desktop: paste into %%APPDATA%%\Claude\claude_desktop_config.json, then fully quit and reopen Claude:
echo.
echo   {
echo     "mcpServers": {
echo       "yueying": {
echo         "command": "%MCP_JSON%",
echo         "env": { "PYTHONUTF8": "1" }
echo       }
echo     }
echo   }
echo.
echo   Cursor / Windsurf / Cline: same block ^(Cline: add "type": "stdio" and "timeout": 1800^).
echo   Everything runs offline after the first model download.
echo.
pause
