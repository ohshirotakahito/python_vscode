@echo off
rem MLパネル（Tkinter版）を起動します。このファイルをダブルクリックしてください。
rem ブラウザは使いません。デスクトップアプリとしてウィンドウが開きます。

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

"%ANACONDA_PYTHON%" "%~dp0main.py"
if errorlevel 1 pause
