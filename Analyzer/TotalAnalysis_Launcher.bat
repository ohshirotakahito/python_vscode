@echo off
setlocal

rem ===== 設定 =====================================================
rem Python の場所（Anaconda）
set "PY=%USERPROFILE%\anaconda3\python.exe"
rem Python スクリプトのフォルダ（このバッチと同じ場所の python フォルダ）
set "SRC=%~dp0python"
rem ================================================================

if not exist "%PY%" (
    echo Python が見つかりません: %PY%
    echo バッチ先頭の PY= を書き換えてください。
    pause
    exit /b 1
)

rem ドラッグ&ドロップされたファイル/フォルダ
set "TARGET=%~1"

:menu
echo.
echo ==============================================
echo   Total Analysis (Python版) ランチャー
echo ==============================================
echo   1. 1st Step  生データ  -^> A@@ (信号検出)
echo   2. 2nd Step  A@@       -^> B@@ (塩基帰属)
echo   3. 1st + 2nd 生データ  -^> A@@ -^> B@@
echo   0. 終了
echo ----------------------------------------------
set "MODE="
set /p "MODE=番号を選んでください: "
if "%MODE%"=="0" goto :eof
if "%MODE%"=="1" goto ask_path
if "%MODE%"=="2" goto ask_path
if "%MODE%"=="3" goto ask_path
echo 1, 2, 3, 0 のどれかを入力してください。
goto menu

:ask_path
if defined TARGET goto have_path
echo.
if "%MODE%"=="2" (echo A@@ ファイルまたはフォルダをドラッグ^&ドロップして Enter:) else (echo 生データの tdms ファイルまたはフォルダをドラッグ^&ドロップして Enter:)
set /p "TARGET=> "
set "TARGET=%TARGET:"=%"
if not defined TARGET goto ask_path
:have_path
if not exist "%TARGET%" (
    echo 見つかりません: %TARGET%
    set "TARGET="
    goto ask_path
)

set "CSV="
set "ANS="
set /p "ANS=CSV も出力しますか? (y/N): "
if /i "%ANS%"=="y" set "CSV=--csv"

if "%MODE%"=="1" goto run_first

rem ----- 2nd Step の設定 -----
:ask_settings
echo.
echo 2nd Step の設定:
echo   1. 既存の B@@ ファイルの設定 (BC Table) を使う
echo   2. 配列とサンプル名を入力する
set "SRCSET="
set /p "SRCSET=番号: "
if "%SRCSET%"=="1" goto ask_bfile
if "%SRCSET%"=="2" goto ask_seq
goto ask_settings

:ask_bfile
echo 設定を流用する B@@ ファイルをドラッグ^&ドロップして Enter:
set "BFILE="
set /p "BFILE=> "
set "BFILE=%BFILE:"=%"
if not exist "%BFILE%" (
    echo 見つかりません: %BFILE%
    goto ask_bfile
)
set "OPTS=--settings-from "%BFILE%""
goto ask_numbers

:ask_seq
set "SEQ="
set /p "SEQ=Sample Sequence (例 UGUUGUACUUUUUUUUUUGUUC): "
if not defined SEQ goto ask_seq
set "NAME="
set /p "NAME=Sample name (例 hsa-miR-3613-5p): "
set "OPTS=--sequence %SEQ% --name "%NAME%""

:ask_numbers
set "RRMIN="
set /p "RRMIN=Right Read MIN (Enter で既定/設定値): "
if defined RRMIN set "OPTS=%OPTS% --right-read-min %RRMIN%"
set "NMAX="
set /p "NMAX=noise MAX (Enter で既定/設定値): "
if defined NMAX set "OPTS=%OPTS% --noise-max %NMAX%"

if "%MODE%"=="3" goto run_both
goto run_second

:run_first
echo.
echo ===== 1st Step 実行中 =====
"%PY%" "%SRC%\first_step_io.py" "%TARGET%" %CSV%
goto done

:run_second
echo.
echo ===== 2nd Step 実行中 =====
"%PY%" "%SRC%\second_step_io.py" "%TARGET%" %OPTS% %CSV%
goto done

:run_both
echo.
echo ===== 1st Step + 2nd Step 実行中 =====
"%PY%" "%SRC%\second_step_io.py" "%TARGET%" --from-raw %OPTS% %CSV%
goto done

:done
echo.
echo ===== 終了 =====
set "TARGET="
set "AGAIN="
set /p "AGAIN=続けて別の処理をしますか? (y/N): "
if /i "%AGAIN%"=="y" goto menu
endlocal
