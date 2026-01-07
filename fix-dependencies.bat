@echo off
echo Fixing Python dependencies...
echo.

REM Upgrade pip first
python -m pip install --upgrade pip

REM Uninstall conflicting packages
echo Uninstalling old versions...
pip uninstall -y fastapi pydantic uvicorn

REM Install compatible versions (tested working combination)
echo Installing compatible versions...
pip install "fastapi>=0.100.0"
pip install "pydantic>=2.0.0,<3.0.0"
pip install "pydantic-settings>=2.0.0"
pip install "uvicorn[standard]>=0.24.0"
pip install python-dotenv motor pymongo passlib[bcrypt] python-jose httpx

echo.
echo Testing imports...
python -c "import fastapi, uvicorn, pydantic; print('✅ All dependencies OK!')"

if errorlevel 1 (
    echo.
    echo ❌ Dependencies still have issues
    echo Try: pip install --upgrade fastapi pydantic uvicorn
    pause
    exit /b 1
) else (
    echo.
    echo ✅ Dependencies fixed! You can now start the backend.
    pause
)

