@echo off
rem Installs rnfs from the files next to this script -- no internet needed.
rem Windows, Python 3.11 or newer (python.org). Double-click, or run it in a terminal.
setlocal
cd /d "%~dp0"
set WHEELS=rnspure-1.5.7-py3-none-any.whl lxmf-1.2.0-py3-none-any.whl rnfs-0.1.1-py3-none-any.whl
for %%w in (%WHEELS%) do if not exist "%%w" (echo Missing %%w -- download it from the Stump's Tools page into this folder. & goto end)
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% -c "import sys; sys.exit(sys.version_info < (3, 11))" || (echo rnfs needs Python 3.11 or newer, from python.org. & goto end)
%PY% -m venv "%USERPROFILE%\.rnfs-venv" || goto end
"%USERPROFILE%\.rnfs-venv\Scripts\python.exe" -m pip install --quiet --no-index --no-deps %WHEELS% || goto end
echo.
echo rnfs is installed: %USERPROFILE%\.rnfs-venv\Scripts\rnfs.exe
echo Start it with:  rnfs daemon     then, in another window:  rnfs get ^<cid^> -o ^<file^>
:end
pause
