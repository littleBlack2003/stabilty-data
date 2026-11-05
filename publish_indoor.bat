@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  一键刷新 + 推送 indoor 稳定性数据
rem  双击运行；下面的配置按需修改
rem ============================================================
set "DATA_DIR=D:\zlc2026年11月4日"
set "SUNS=0.55"
set "AREA=0.1"
set "TAIL_HOURS=2"
set "STABLE_SLOPE=0.1"
set "EXCLUDE=3,7,9"
rem 归档脚本的源头（可选，存在就同步到 indoor\）；留空则不同步
set "SCRIPT_SRC=E:\OneDrive\009自编写软件\Stability test softwares\tools\stability_report.py"

cd /d "%~dp0"
set "REPORT=%DATA_DIR%\REPORT"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python，请确认已加入 PATH
    goto :end
)

echo ============================================================
echo  1/4  分析原始数据
echo       %DATA_DIR%
echo ============================================================
python "indoor\stability_report.py" --data "%DATA_DIR%" --suns %SUNS% --area %AREA% ^
       --tail-hours %TAIL_HOURS% --stable-slope %STABLE_SLOPE% --exclude %EXCLUDE% > "_last_run.log" 2>&1
if errorlevel 1 (
    echo [错误] 分析失败，详见 _last_run.log
    goto :end
)
for /f "tokens=*" %%L in ('findstr /c:"完成：" _last_run.log') do echo       %%L

if exist "%SCRIPT_SRC%" copy /y "%SCRIPT_SRC%" "indoor\stability_report.py" >nul

echo.
echo ============================================================
echo  2/4  复制结果到 indoor\
echo ============================================================
for %%F in (report.txt summary_cells.csv summary_groups.csv points_all.csv rounds.csv) do (
    if exist "%REPORT%\%%F" copy /y "%REPORT%\%%F" "indoor\%%F" >nul
)
for %%F in (aligned_pce.csv aligned_pce_norm.csv aligned_isc_norm.csv aligned_voc_norm.csv aligned_ff_norm.csv aligned_group_mean.csv) do (
    if exist "%REPORT%\%%F" copy /y "%REPORT%\%%F" "indoor\%%F" >nul
)
for %%F in (fig_pce.png fig_group.png fig_ranking.png fig_iv_last.png) do (
    if exist "%REPORT%\%%F" copy /y "%REPORT%\%%F" "indoor\%%F" >nul
)
echo       已复制 15 个文件

echo.
echo ============================================================
echo  3/4  提交
echo ============================================================
git add -A
git diff --cached --quiet
if errorlevel 1 (
    for /f "tokens=1-3 delims=/- " %%a in ("%DATE%") do set "D=%%a-%%b-%%c"
    git commit -q -m "更新 indoor 数据快照 !D! %TIME:~0,5%"
    echo       已提交：!D! %TIME:~0,5%
) else (
    echo       与上次提交无差异，跳过提交
)

echo.
echo ============================================================
echo  4/4  推送到 GitHub
echo ============================================================
git push
if errorlevel 1 (
    echo.
    echo [提示] 推送失败，可能是网络或凭据问题。
    echo        本地提交已保留，网络恢复后再运行一次本脚本即可补推。
    goto :end
)
echo.
echo [完成] https://github.com/littleBlack2003/stabilty-data
echo.

:end
echo.
pause
