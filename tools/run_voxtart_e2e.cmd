@echo off
rem SPDX-FileCopyrightText: 2026 VOXTART contributors
rem SPDX-License-Identifier: MIT

setlocal
set "VOXTART_ROOT=%~dp0.."
python "%VOXTART_ROOT%\tests\python\voxtart_e2e.py" %*
set "VOXTART_EXIT=%ERRORLEVEL%"
endlocal & exit /b %VOXTART_EXIT%
