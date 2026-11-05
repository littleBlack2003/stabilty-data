@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  one-click: refresh indoor stability data and push to GitHub
rem  （双击运行即可；下面的配置可按需修改）
rem ============================================================
set "DATA_DIR=D:\zlc2026年11月4日"
set "SUNS=0.55"
set "AREA=0.1"
set "TAIL_HOURS=1"
set "STABLE_SLOPE=0.1"
set "EXCLUDE=3,7,9"
rem 归档脚本源头（可选，存在就同步到 indoor\）
set "SCRIPT_SRC=E:\OneDrive\009自编写软件\Stability test softwares\tools\stability_report.py"

cd /d "%~dp0"
set "REPORT=%DATA_DIR%\REPORT"
set "LOG=%TEMP%\stability_publish.log"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] python not found in PATH
    goto :end
)

echo ============================================================
echo  [1/4] analyzing raw data
echo        %DATA_DIR%
echo ============================================================
rem 先把最新脚本同步进 indoor\，再跑，避免用到上一版副本
if exist "%SCRIPT_SRC%" (
    copy /y "%SCRIPT_SRC%" "indoor\stability_report.py" >nul
    echo        script synced
)
python "indoor\stability_report.py" --data "%DATA_DIR%" --suns %SUNS% --area %AREA% ^
       --tail-hours %TAIL_HOURS% --stable-slope %STABLE_SLOPE% --exclude %EXCLUDE% > "%LOG%" 2>&1
if errorlevel 1 (
    echo [ERROR] analysis failed, see: %LOG%
    goto :end
)
echo        done

echo.
echo ============================================================
echo  [2/4] copying results into indoor\
echo ============================================================
set /a COPIED=0
for %%F in (report.txt summary_cells.csv summary_groups.csv points_all.csv rounds.csv ^
            aligned_pce.csv aligned_pce_norm.csv aligned_isc_norm.csv ^
            aligned_voc_norm.csv aligned_ff_norm.csv aligned_group_mean.csv ^
            fig_pce.png fig_group.png fig_ranking.png fig_iv_last.png) do (
    if exist "%REPORT%\%%F" (
        copy /y "%REPORT%\%%F" "indoor\%%F" >nul
        set /a COPIED+=1
    )
)
echo        !COPIED! files copied

echo.
echo ============================================================
echo  [3/4] summarizing
echo ============================================================
rem 只打印两行摘要；注意不要在这里用 %% 号，批处理会吞掉
python -c "import csv;rows=list(csv.reader(open('indoor/summary_groups.csv',encoding='utf-8-sig')));[print('       ',r[1],'PCE0',r[4],'last',r[5]) for r in rows[1:]]"
if errorlevel 1 echo [WARN] summary skipped

echo.
echo ============================================================
echo  [4/4] commit and push
echo ============================================================
git add -A
git diff --cached --quiet
if errorlevel 1 (
    git commit -q -m "更新 indoor 数据快照"
    echo        committed
) else (
    echo        no changes, commit skipped
)
git push
if errorlevel 1 (
    echo.
    echo [WARN] push failed - network or credential problem. Commit kept locally;
    echo        run this script again later to push.
    goto :end
)
echo        pushed
echo.
echo [DONE] https://github.com/littleBlack2003/stabilty-data

:end
echo.
pause
