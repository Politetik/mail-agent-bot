@echo off
rem Мастер настройки .env. Двойной щелчок или запуск из консоли.
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "ROOT=%~dp0.."
set "PY=%ROOT%\python\.venv\Scripts\python.exe"

rem Пауза нужна только при запуске двойным щелчком (новое окно cmd).
set "PAUSE_AT_END=0"
echo %cmdcmdline% | find /i "%~nx0" >nul && set "PAUSE_AT_END=1"

if exist "%PY%" (
    "%PY%" "%~dp0setup_env.py" %*
    goto :done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0setup_env.py" %*
    goto :done
)

python "%~dp0setup_env.py" %*

:done
set "CODE=%errorlevel%"
if "%PAUSE_AT_END%"=="1" pause
exit /b %CODE%
