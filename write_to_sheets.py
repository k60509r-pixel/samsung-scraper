import json
import os
import sys
import gspread
from google.oauth2.service_account import Credentials


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 欄位順序: A=機型 B=容量 C=回收估價 D=價格補正(手動) E=更新時間
SHEET_HEADERS = ["機型", "容量", "回收估價（NT$）", "價格補正", "更新時間"]


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

    # 先讀取現有的「價格補正」欄（D 欄），避免覆寫手動調整值
    existing_adjustments = {}
    try:
        existing_data = sheet.get_all_values()
        if len(existing_data) > 1:
            headers = existing_data[0]
            if "價格補正" in headers:
                adj_col = headers.index("價格補正")
                model_col = headers.index("機型") if "機型" in headers else 0
                for row in existing_data[1:]:
                    if len(row) > max(adj_col, model_col):
                        model_key = row[model_col].strip()
                        adj_val = row[adj_col].strip()
                        if model_key and adj_val:
                            existing_adjustments[model_key] = adj_val
    except Exception:
        pass  # 第一次執行，無既有資料

    # 建立新資料列（D 欄補正值沿用舊值，預設空白）
    rows = [SHEET_HEADERS]
    for item in data:
        model = item.get("model", "")
        adj = existing_adjustments.get(model, "")
        rows.append([
            model,
            item.get("storage", ""),
            format_price(item.get("price")),
            adj,
            item.get("scraped_at", ""),
        ])

    sheet.clear()
    sheet.update("A1", rows)

    # 標題列加粗
    sheet.format("A1:E1", {"textFormat": {"bold": True}})

    print(f"已寫入 {len(data)} 筆資料到 Google Sheets（價格補正欄已保留）")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "results.json"
    try:
        with open(input_file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"找不到 {input_file}，請先執行 scraper.py")
        sys.exit(1)

    write_to_sheets(data)
