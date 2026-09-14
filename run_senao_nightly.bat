@echo off
chcp 65001 > nul
cd /d "%~dp0"

set SPREADSHEET_ID=填入你的Spreadsheet ID

echo [%date% %time%] 神腦爬蟲開始 >> senao_nightly.log

python scraper_senao_prices.py >> senao_nightly.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] 爬蟲失敗，中止 >> senao_nightly.log
    exit /b 1
)

python write_to_sheets_senao_prices.py results_senao_prices.json >> senao_nightly.log 2>&1
if %errorlevel% neq 0 (
    echo [%date% %time%] 寫入 Sheets 失敗 >> senao_nightly.log
    exit /b 1
)

echo [%date% %time%] 完成 >> senao_nightly.log
