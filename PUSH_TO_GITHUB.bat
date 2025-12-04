@echo off
echo ========================================
echo Pushing MINDEX code to GitHub
echo ========================================
echo.

echo Step 1: Checking git status...
git status
echo.

echo Step 2: Adding all files...
git add -A
echo.

echo Step 3: Checking what will be committed...
git status
echo.

echo Step 4: Committing changes...
git commit -m "Add complete MINDEX codebase - API, ETL, migrations, tests, workflows"
echo.

echo Step 5: Pushing to GitHub...
echo (You may be prompted for GitHub credentials)
git push -u origin main
echo.

echo Step 6: Final status...
git status
echo.

echo ========================================
echo Done! Check https://github.com/MycosoftLabs/mindex
echo ========================================
pause
