@echo off
cd /d "%~dp0"
"C:\Users\user\AppData\Roaming\kimi-desktop\daimon-bundle\runtime\git\usr\bin\ssh.exe" -o StrictHostKeyChecking=no -o ServerAliveInterval=30 -R 80:localhost:8000 nokey@localhost.run > tunnel.log 2>&1
