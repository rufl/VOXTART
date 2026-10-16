@echo off
rem SPDX-FileCopyrightText: 2026 VOXTART contributors
rem SPDX-License-Identifier: GPL-3.0-or-later

setlocal
set "VOXTART_ROOT=%~dp0.."
set "VOXTART_OUTPUT=%VOXTART_ROOT%\VoxTart.exe"
set "VOXTART_RUNTIME=%VOXTART_ROOT%\build\blender-5.2.2\blender-5.2.2-windows-x64"
set "VOXTART_CORE=%VOXTART_RUNTIME%\blender-core.exe"
set "VOXTART_RES=%TEMP%\voxtart_launcher_%RANDOM%.res"

pushd "%VOXTART_ROOT%"
if not exist "%VOXTART_CORE%" (
    if exist "%VOXTART_RUNTIME%\blender.exe.voxtart-original" (
        copy /y "%VOXTART_RUNTIME%\blender.exe.voxtart-original" "%VOXTART_CORE%" >nul
    ) else (
        copy /y "%VOXTART_RUNTIME%\blender.exe" "%VOXTART_CORE%" >nul
    )
    if errorlevel 1 (
        popd
        exit /b 1
    )
)
windres tools\voxtart_launcher.rc -O coff -o "%VOXTART_RES%"
if errorlevel 1 (
    popd
    del /q "%VOXTART_RES%" 2>nul
    exit /b 1
)
gcc -mwindows -municode -O2 -s tools\voxtart_launcher.c "%VOXTART_RES%" -o "%VOXTART_OUTPUT%" -lshell32 -luser32
if errorlevel 1 (
    popd
    del /q "%VOXTART_RES%" 2>nul
    exit /b 1
)
gcc -mwindows -municode -O2 -s -DVOXTART_RUNTIME_WRAPPER tools\voxtart_launcher.c "%VOXTART_RES%" -o "%VOXTART_RUNTIME%\blender.exe" -lshell32 -luser32
set "VOXTART_EXIT=%ERRORLEVEL%"
popd
del /q "%VOXTART_RES%" 2>nul
exit /b %VOXTART_EXIT%
