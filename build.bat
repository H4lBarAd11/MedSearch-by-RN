@echo off
:: ─────────────────────────────────────────────────────────────────────────────
::  MedSearch — Build Script (Windows)
::  Double-click this file to build medsearch.exe
:: ─────────────────────────────────────────────────────────────────────────────
echo.
echo   MedSearch Build Script (Windows)
echo   ─────────────────────────────────────────────
echo.

:: 1. Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo   X  Python not found. Download from python.org ^(tick "Add to PATH"^)
    pause
    exit /b 1
)
echo   OK  Python found
for /f "tokens=*" %%i in ('python --version') do echo   %%i

:: 2. Install PyInstaller
echo   ->  Installing PyInstaller...
python -m pip install --upgrade --quiet pyinstaller
if errorlevel 1 (
    echo   X  pip failed. Try running as Administrator.
    pause
    exit /b 1
)
echo   OK  PyInstaller ready

:: 3. Build
echo   ->  Building medsearch.exe...
python -m PyInstaller --onefile --name medsearch --console --clean medsearch.py
if errorlevel 1 (
    echo   X  Build failed. Check output above.
    pause
    exit /b 1
)

:: 4. Copy launcher batch file next to the exe
copy /Y launch_medsearch.bat dist\launch_medsearch.bat >nul 2>&1

echo.
echo   OK  Build complete!
echo   OK  Binary: %cd%\dist\medsearch.exe
echo.
echo   Give your colleagues the "dist" folder.
echo   They should double-click "launch_medsearch.bat"
echo.
pause
