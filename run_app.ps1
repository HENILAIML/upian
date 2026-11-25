$env:CURL_CA_BUNDLE = ""
$env:REQUESTS_CA_BUNDLE = ""
$env:SSL_CERT_FILE = ""

Write-Host "====================================================" -ForegroundColor Cyan
Write-Host "          Starting Upian AI Server...              " -ForegroundColor Green
Write-Host "====================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Opening browser at http://127.0.0.1:5000..." -ForegroundColor Yellow
Start-Process "http://127.0.0.1:5000"
Write-Host "Running python app.py..." -ForegroundColor Cyan
python app.py
