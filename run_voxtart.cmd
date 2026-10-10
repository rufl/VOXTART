@echo off
rem SPDX-FileCopyrightText: 2026 VOXTART contributors
rem SPDX-License-Identifier: MIT

setlocal
set "VOXTART_ROOT=%~dp0"
set "VOXTART_BLENDER=%VOXTART_ROOT%build\blender-5.2.2\blender-5.2.2-windows-x64\blender.exe"
set "VOXTART_LAUNCHER=%VOXTART_ROOT%VoxTart.exe"
set "VOXTART_START=%VOXTART_ROOT%tools\voxtart_desktop_start.py"
set "VOXTART_RUNTIME_BRAND=%VOXTART_ROOT%tools\brand_voxtart_runtime.py"
set "VOXTART_PYTHON=%VOXTART_ROOT%build\blender-5.2.2\blender-5.2.2-windows-x64\5.2\python\bin\python.exe"
set "VOXTART_PREFS_INIT=%VOXTART_ROOT%tools\voxtart_preferences_init.py"
if not defined VOXTART_CONFIG set "VOXTART_CONFIG=%VOXTART_ROOT%config\blender"
set "BLENDER_USER_CONFIG=%VOXTART_CONFIG%"

if not exist "%VOXTART_BLENDER%" (
    echo VOXTART Blender runtime not found:
    echo   %VOXTART_BLENDER%
    echo Install Blender 5.2.2 or update run_voxtart.cmd.
    exit /b 1
)
if not exist "%VOXTART_START%" (
    echo VOXTART startup script not found:
    echo   %VOXTART_START%
    exit /b 1
)
if not exist "%VOXTART_RUNTIME_BRAND%" (
    echo VOXTART runtime branding script not found:
    echo   %VOXTART_RUNTIME_BRAND%
    exit /b 1
)
if not exist "%VOXTART_PYTHON%" (
    echo Bundled Python runtime not found:
    echo   %VOXTART_PYTHON%
    exit /b 1
)
if not exist "%VOXTART_LAUNCHER%" (
    echo VoxTart executable not found:
    echo   %VOXTART_LAUNCHER%
    echo Build it with tools\build_voxtart_launcher.cmd.
    exit /b 1
)

pushd "%VOXTART_ROOT%"
"%VOXTART_PYTHON%" "%VOXTART_RUNTIME_BRAND%"
if errorlevel 1 (
    echo Could not apply native VoxTart runtime branding.
    popd
    exit /b 1
)
popd

if not exist "%VOXTART_CONFIG%\userpref.blend" (
    if not exist "%VOXTART_CONFIG%" mkdir "%VOXTART_CONFIG%"
    "%VOXTART_BLENDER%" --background --python "%VOXTART_PREFS_INIT%"
    if errorlevel 1 (
        echo Could not initialize VOXTART Blender preferences.
        exit /b 1
    )
)

pushd "%VOXTART_ROOT%"
"%VOXTART_LAUNCHER%" %*
set "VOXTART_EXIT=%ERRORLEVEL%"
popd
exit /b %VOXTART_EXIT%
