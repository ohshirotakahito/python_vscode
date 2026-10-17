@echo off
rem プロジェクト内のTkinterアプリを起動するランチャー画面（launcher.py）を開きます。
rem このファイルをダブルクリックしてください。

setlocal

set ANACONDA_PYTHONW=%USERPROFILE%\anaconda3\pythonw.exe

if not exist "%ANACONDA_PYTHONW%" (
    echo Anacondaのpythonw.exeが見つかりません: %ANACONDA_PYTHONW%
    echo このファイルをテキストエディタで開き、ANACONDA_PYTHONW の行を
    echo お使いの環境の pythonw.exe のパスに書き換えてください。
    pause
    exit /b 1
)

start "" /D "%~dp0" "%ANACONDA_PYTHONW%" "%~dp0launcher.py"
