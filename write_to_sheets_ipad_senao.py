"""
把 scraper_ipad_senao.py 的結果寫進「iPad回收報價(神腦測試)」這個獨立測試分頁，
不會動到前端正在讀取的「iPad回收報價」分頁。確認資料沒問題後，
再改成呼叫既有的 write_to_sheets_ipad.py（或把分頁名稱換成正式的）即可上線。
"""

import json
import os
import sys
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "iPad回收報價(神腦測試)"
SHEET_HEADERS = ["系列", "機型", "容量", "回收估價（NT$）", "價格補正", "更新時間"]


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
    ss = client.open_by_key(spreadsheet_id)

    try:
        sheet = ss.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        sheet = ss.add_worksheet(title=SHEET_NAME, rows=1000, cols=12)
        print(f"已建立新分頁：{SHEET_NAME}")

    rows = [SHEET_HEADERS]
    for item in data:
        rows.append([
            item.get("series", ""),
            item.get("model", ""),
            item.get("storage", ""),
            format_price(item.get("price")),
            "",
            item.get("scraped_at", ""),
        ])

    sheet.clear()
    sheet.update("A1", rows)
    sheet.format("A1:F1", {"textFormat": {"bold": True}})

    print(f"已寫入 {len(data)} 筆資料到「{SHEET_NAME}」測試分頁")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "results_ipad_senao.json"
    try:
        with open(input_file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"找不到 {input_file}，請先執行 scraper_ipad_senao.py")
        sys.exit(1)

    write_to_sheets(data)
