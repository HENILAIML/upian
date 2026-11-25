@echo off
title Upian AI Server

:: Clear any broken PostgreSQL TLS bundle variables
set CURL_CA_BUNDLE=
set REQUESTS_CA_BUNDLE=
set SSL_CERT_FILE=

echo ====================================================
echo           Starting Upian AI Server...
echo ====================================================
echo.
echo Opening browser at http://127.0.0.1:5000 in 3 seconds...
start "" http://127.0.0.1:5000
echo Running python app.py...
python app.py
pause
