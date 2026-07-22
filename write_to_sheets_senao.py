import json
import os
import sys
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SHEET_NAME = "神腦Raw"
HEADERS = ["品牌", "神腦原始字串", "機型", "容量", "年份", "抓取時間"]


def get_credentials():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("環境變數 GOOGLE_CREDENTIALS_JSON 未設定")
    return Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)


def write_to_sheets(data: list[dict]):
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("環境變數 SPREADSHEET_ID 未設定")

    client = gspread.authorize(get_credentials())
    ss = client.open_by_key(spreadsheet_id)

    # 建立或取得「神腦Raw」分頁
    try:
        sheet = ss.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        sheet = ss.add_worksheet(title=SHEET_NAME, rows=5000, cols=10)
        print(f"已建立新分頁：{SHEET_NAME}")

    rows = [HEADERS]
    for item in data:
        rows.append([
            item.get("brand", ""),
            item.get("raw", ""),
            item.get("model", ""),
            item.get("capacity", ""),
            item.get("year", ""),
            item.get("scraped_at", ""),
        ])

    sheet.clear()
    sheet.update("A1", rows)
    sheet.format("A1:F1", {"textFormat": {"bold": True}})

    print(f"已寫入 {len(data)} 筆資料到「{SHEET_NAME}」分頁")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "results_senao.json"
    try:
        with open(input_file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"找不到 {input_file}，請先執行 scraper_senao.py")
        sys.exit(1)
    write_to_sheets(data)
