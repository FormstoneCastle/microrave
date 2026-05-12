@echo off
REM Nightly rotation-detection job. Run via Windows Task Scheduler.
REM
REM Defaults: 500 photos per night, 4 sec between requests (gentle on wsrv/SSB).
REM At this rate the full archive (~42k photos) finishes in ~12 weeks.
REM
REM Adjust the numbers below if you want it faster or slower.

setlocal
cd /d "%~dp0"

set LIMIT=500
set DELAY=4

REM Date-stamped log so each night gets its own file.
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value ^| find "="') do set DT=%%I
set TODAY=%DT:~0,8%
set LOG=logs\detect_%TODAY%.log

if not exist logs mkdir logs

echo === Starting %DATE% %TIME% === >> "%LOG%"
python detect_rotations.py --limit %LIMIT% --delay %DELAY% >> "%LOG%" 2>&1
echo === Finished %DATE% %TIME% === >> "%LOG%"

endlocal
