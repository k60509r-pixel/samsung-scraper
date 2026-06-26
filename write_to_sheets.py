import json
import os
import sys
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_HEADERS = ["機型", "容量", "回收估價（NT$）", "更新時間"]


def get_credentials() -> Credentials:
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("環境變數 GOOGLE_CREDENTIALS_JSON 未設定")
    info = json.loads(creds_json)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def format_price(price) -> str:
    if price is None:
        return "不予回收"
    return f"{price:,}"


def write_to_sheets(data: list[dict]):
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("環境變數 SPREADSHEET_ID 未設定")

    creds = get_credentials()
    client = gspread.authorize(creds)
    sheet = client.open_by_key(spreadsheet_id).sheet1

    rows = [SHEET_HEADERS]
    for item in data:
        rows.append([
            item.get("model", ""),
            item.get("storage", ""),
            format_price(item.get("price")),
            item.get("scraped_at", ""),
        ])

    # Clear and overwrite
    sheet.clear()
    sheet.update("A1", rows)

    # Bold header row
    sheet.format("A1:D1", {"textFormat": {"bold": True}})

    print(f"已寫入 {len(data)} 筆資料到 Google Sheets")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    try:
        with open(input_file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"找不到 {input_file}，請先執行 scraper.py")
        sys.exit(1)

    write_to_sheets(data)
