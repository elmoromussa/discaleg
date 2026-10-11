@echo off
setlocal
set DB=%~dp0arxiu_discs.db
set BASE=%~dp0

echo Important audits a la base de dades...
echo DB: %DB%
echo.

for %%D in (EXT-A EXT-B EXT-C EXT-D EXT-E EXT-F EXT-G EXT-H INT-1 INT-2 INT-3 INT-4 INT-5 INT-6 INT-7 INT-8 SSD-1 SSD-2 SSD-3 PC-D) do (
    echo [%%D] Important...
    python "%~dp0import_audit_to_db.py" --db "%DB%" --audit "%BASE%\%%D" --drive %%D
    if errorlevel 1 (
        echo [%%D] ERROR
    ) else (
        echo [%%D] OK
    )
    echo.
)

echo ========================================
echo Acabat.
echo.
echo Discs importats a la DB:
python "%~dp0import_audit_to_db.py" --db "%DB%" --list
echo.
pause
