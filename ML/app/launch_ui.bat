@echo off
rem MLパネル（Streamlit UI）を起動します。このファイルをダブルクリックしてください。
rem 初回はブラウザが自動的に開くまで少し時間がかかります。

setlocal

set ANACONDA_PYTHON=%USERPROFILE%\anaconda3\python.exe

if not exist "%ANACONDA_PYTHON%" (
    echo Anacondaのpython.exeが見つかりません: %ANACONDA_PYTHON%
    echo このファイルをテキストエディタで開き、ANACONDA_PYTHON の行を
    echo お使いの環境の python.exe のパスに書き換えてください。
    pause
    exit /b 1
)

cd /d "%~dp0.."

"%ANACONDA_PYTHON%" "%~dp0_run_app.py"

pause
