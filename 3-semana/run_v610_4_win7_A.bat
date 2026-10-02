@echo off
setlocal

REM ==================================================================
REM CLF Viewer Balloon-spectra V6.10.4 - production resume - win7-A
REM
REM Critical fix:
REM   CLF Viewer truncates long filenames in the MIDDLE of its title.
REM   The unique local-cache digest at the END remains visible.
REM
REM Example:
REM   AUDIOPERFORMANCE-..._PRO__f0e0004185.CF2
REM                                ^^^^^^^^^^
REM
REM V6.10.4 recognizes that digest directly in the MAIN WINDOW TITLE.
REM ==================================================================

cd /d C:\Users\Docker\Desktop\Shared\storage
set PYTHONUNBUFFERED=1

python balloon_v6_10_4_title_digest.py ^
  --cf2-dir C:\Users\Docker\Desktop\Shared\storage\speaker_cf2 ^
  --output-dir C:\Users\Docker\Desktop\Shared\storage\clfviewer_balloon_spectra_final ^
  --recursive ^
  --resume ^
  --distributed ^
  --worker-id win7-A ^
  --fixed-grid 72 37 ^
  --local-input-cache C:\CLF_BATCH_CACHE ^
  --open-timeout 45 ^
  --slow-open-grace 30 ^
  --retry-open-timeout 60 ^
  --viewer-interactive-timeout 45 ^
  --open-trigger win32 ^
  --open-strategy hybrid-fast ^
  --ready-strategy native ^
  --ready-probe functional ^
  --ready-probe-timeout 5 ^
  --native-cabinet-timeout 8 ^
  --native-scrollbar-timeout 20 ^
  --upper-render-mode cabinet ^
  --sft-view T ^
  --crop-mode auto ^
  --verification-mode hybrid-row ^
  --hotpath-scroll-reader getscrollpos ^
  --gdi-capture-mode persistent ^
  --capture-payload raw-worker ^
  --capture-storage row-zip ^
  --png-compress-level 1 ^
  --save-angle-labels audit ^
  --audit-every 100 ^
  --save-workers 3 ^
  --max-pending-saves 16 ^
  --row-zip-max-pending 2 ^
  --claim-timeout-min 30 ^
  --claim-heartbeat-sec 10 ^
  --multipass-wait-sec 10 ^
  --max-idle-passes 3

set EXITCODE=%ERRORLEVEL%
echo.
echo ==================================================================
echo V6.10.4 finished with exit code %EXITCODE%
echo ==================================================================
exit /b %EXITCODE%
