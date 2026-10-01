@echo off
setlocal
cd /d %~dp0

echo [1/5] Installing Python packages...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if not exist "tesseract\tesseract.exe" (
  echo.
  echo Tesseract not found in .\tesseract\
  echo.
  echo Install Tesseract OCR on this Windows build machine, then copy the Tesseract folder here so this exists:
  echo   %cd%\tesseract\tesseract.exe
  echo The folder should also contain tessdata\eng.traineddata and any required DLL files.
  echo.
  echo Example source location after installing Tesseract:
  echo   C:\Program Files\Tesseract-OCR\
  echo.
  pause
  exit /b 1
)

echo [2/5] Cleaning old build...
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul

echo [3/5] Building EXE...
python -m PyInstaller --noconfirm --clean --windowed --name "MB Code Scanner" --collect-all tkinterdnd2 --add-data "tesseract;tesseract" mb_code_scanner\app.py
if errorlevel 1 goto :fail

if exist "dist\MB Code Scanner\tesseract\tesseract.exe" goto :ok

echo [4/5] Copying Tesseract bundle...
xcopy /E /I /Y "tesseract" "dist\MB Code Scanner\tesseract" >nul

:ok
echo [5/5] Build complete.
echo Output folder: %cd%\dist\MB Code Scanner\
echo EXE: %cd%\dist\MB Code Scanner\MB Code Scanner.exe
pause
exit /b 0

:fail
echo Build failed.
pause
exit /b 1
