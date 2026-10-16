@echo off
cd /d "%~dp0"
py -3 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m unittest discover -s tests -v
if errorlevel 1 goto failed
echo Setup and tests completed. Run start.bat.
pause
exit /b 0
:failed
echo Setup or tests failed. Check the message above.
pause
exit /b 1
