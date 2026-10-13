@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   Discaleg - Execucio rapida
echo ========================================
echo.
echo Projecte (aquesta instancia): %CD%
echo Si el port 8000 esta en us, tanca l'altra finestra del servidor abans.
echo.
echo Iniciant servidor a http://127.0.0.1:8000
echo Per aturar: tanca aquesta finestra o Ctrl+C.
echo.

start /b cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"

call "%~dp0venv\Scripts\activate.bat"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5

if errorlevel 1 (
    echo.
    echo Si falten llibreries, executa abans: pip install -r requirements.txt
    echo O crea el venv: python -m venv venv
    pause
)
