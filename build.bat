@echo off
:: ─────────────────────────────────────────────────────────────────────────────
::  MedSearch — Standalone Build (Windows)
::  Double-click this file to build MedSearch.exe (no Python needed to run it).
:: ─────────────────────────────────────────────────────────────────────────────
echo.
echo   MedSearch - Standalone Build (Windows)
echo   ---------------------------------------------
echo.

:: 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo   X  Python not found. Download from python.org ^(tick "Add to PATH"^)
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('python --version') do echo   OK  %%i

:: 2. Install the app's dependencies + PyInstaller
echo   ->  Installing dependencies...
python -m pip install --upgrade --quiet pip
python -m pip install --quiet -r requirements.txt
python -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 (
    echo   X  pip failed. Try running as Administrator.
    pause
    exit /b 1
)
echo   OK  Dependencies ready

:: 3. Build. On Windows the --add-data separator is a SEMICOLON.
::    The templates folder MUST be bundled or the UI won't load.
::    --windowed keeps it GUI-only (no console window).
echo   ->  Building MedSearch.exe...
python -m PyInstaller --onefile --windowed --name MedSearch --add-data "templates;templates" --add-data "VERSION;." --clean app.py
if errorlevel 1 (
    echo   X  Build failed. Check output above.
    pause
    exit /b 1
)

echo.
echo   OK  Build complete!
echo   OK  Binary: %cd%\dist\MedSearch.exe
echo.
echo   Give your colleagues the MedSearch.exe from the "dist" folder.
echo   They just double-click it - no Python needed.
echo.
pause
