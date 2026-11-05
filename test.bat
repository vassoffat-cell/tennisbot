@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY="%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe"
%PY% analyze.py test.png
pause
