@echo off
REM Fix MINDEX Database Connection - Feb 11, 2026
REM Simple batch script to restart MINDEX services on VM 189

echo ============================================================
echo   MINDEX Database Fix Script
echo   Target: 192.168.0.189
echo ============================================================
echo.

echo [1/10] Testing VM connectivity...
ping -n 1 192.168.0.189 >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Cannot ping VM 192.168.0.189
    exit /b 1
)
echo [OK] VM is reachable
echo.

echo [2/10] Checking current API health...
curl -s http://192.168.0.189:8000/api/mindex/health
echo.
echo.

echo [3/10] SSH into VM and restart containers...
echo [INFO] You will be prompted for SSH password
echo.

ssh mycosoft@192.168.0.189 "cd /home/mycosoft/mindex && echo 'Current containers:' && docker compose ps && echo '' && echo 'Restarting postgres...' && docker compose restart mindex-postgres && sleep 8 && echo 'Restarting redis...' && docker compose restart mindex-redis && sleep 3 && echo 'Restarting qdrant...' && docker compose restart mindex-qdrant && sleep 3 && echo 'Restarting mindex-api...' && docker compose restart mindex-api && sleep 10 && echo '' && echo 'Final status:' && docker compose ps && echo '' && echo 'Health check:' && curl -s http://localhost:8000/api/mindex/health && echo '' && echo '' && echo 'Test observations:' && curl -s 'http://localhost:8000/api/mindex/observations?limit=3' | head -100"

echo.
echo ============================================================
echo   Verifying from local machine...
echo ============================================================
timeout /t 3 >nul 2>&1

curl -s http://192.168.0.189:8000/api/mindex/health
echo.
echo.

echo ============================================================
echo   [DONE] Fix Complete
echo ============================================================
echo.
echo Next: Test these pages in browser:
echo   - http://localhost:3010/natureos/mindex
echo   - http://localhost:3010/natureos/mindex/explorer
echo   - http://localhost:3010/mindex
echo.
