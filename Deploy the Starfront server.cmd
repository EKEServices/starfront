@echo off
rem  Push the server code to the VPS and restart it there.
rem
rem  Run from this folder (the Dropbox copy). It copies server/, astrocontrol/
rem  and requirements.txt to the VPS over scp, then runs the installer there,
rem  which rebuilds the virtual environment and restarts the service. Your
rem  ssh key does the signing in; no password is typed here.
rem
rem  One file rather than three pasted lines, because a long ssh line pasted
rem  into PowerShell wraps, and the hostname ends up as a command of its own.
rem
rem  LEGACY keeps the server's old address answering as well, for telescopes
rem  that still point at it. Make it empty once they have all been updated.

set VPS=root@137.184.219.128
set PUBLIC=collab.starfront.space
set LEGACY=starfront-bray.duckdns.org

title Deploying the Starfront collaboration server
cd /d "%~dp0"
echo == copying the code to %VPS%
rem  The staging copy on the VPS is replaced, not added to, so a plugin
rem  release removed here is removed there too.
ssh %VPS% "mkdir -p /root/starfront && rm -rf /root/starfront/server /root/starfront/astrocontrol"
if errorlevel 1 goto failed
scp -r server astrocontrol requirements.txt %VPS%:/root/starfront/
if errorlevel 1 goto failed
echo.
echo == installing and restarting on the server
ssh %VPS% "cd /root/starfront && STARFRONT_LEGACY_HOST=%LEGACY% bash server/deploy/install.sh %PUBLIC%"
if errorlevel 1 goto failed
echo.
echo == checking what is running now
curl -s https://%PUBLIC%/api/v1/health
echo.
echo.
echo The N.I.N.A. plugin feed people add as a plugin repository:  https://%PUBLIC%/nina
echo Done. Restart Starfront to pick up the new deal.
pause
exit /b 0

:failed
echo.
echo Something went wrong above. Nothing on the server was changed by a step that failed.
pause
exit /b 1
