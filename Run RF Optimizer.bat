@echo off
REM Double-click to start the RF Optimizer app, then open http://localhost:8601
cd /d "%~dp0"
echo Starting RF Optimizer  ...  (leave this window open; close it to stop)
echo Open your browser at:  http://localhost:8601
echo.
".venv\Scripts\python.exe" -m streamlit run "app\Home.py" --server.port 8601 --server.headless false --browser.gatherUsageStats false
pause
