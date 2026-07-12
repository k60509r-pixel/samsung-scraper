import json
import os
import sys
import gspread
from google.oauth2.service_account import Credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
SHEET_HEADERS = ["機型", "容量", "回收估價（NT$）", "價格補正", "更新時間"]


def get_credentials():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        raise ValueError("環境變數 GOOGLE_CREDENTIALS_JSON 未設定")
    return Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)


def format_price(price):
    return "不予回收" if price is None else f"{price:,}"


def write_to_sheets(data: list[dict]):
    spreadsheet_id = os.environ.get("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise ValueError("環境變數 SPREADSHEET_ID 未設定")
    client = gspread.authorize(get_credentials())
    sheet = client.open_by_key(spreadsheet_id).worksheet("Xiaomi 回收報價")

    existing_adjustments = {}
    existing_gk = {}
    try:
        existing_data = sheet.get_all_values()
        if len(existing_data) > 1:
            headers = existing_data[0]
            adj_col   = headers.index("價格補正") if "價格補正" in headers else 3
            model_col = headers.index("機型")    if "機型"    in headers else 0
            for row in existing_data[1:]:
                padded = row + [''] * max(0, 11 - len(row))
                model_key = padded[model_col].strip()
                if not model_key:
                    continue
                adj_val = padded[adj_col].strip()
                if adj_val:
                    existing_adjustments[model_key] = adj_val
                existing_gk[model_key] = padded[6:11]
    except Exception:
        pass

    rows = [SHEET_HEADERS]
    for item in data:
        model = item.get("model", "")
        rows.append([model, item.get("storage",""), format_price(item.get("price")),
                     existing_adjustments.get(model,""), item.get("scraped_at","")])

    sheet.clear()
    sheet.update("A1", rows)
    sheet.format("A1:E1", {"textFormat": {"bold": True}})

    gk_data = [existing_gk.get(item.get("model",""), ['','','','','']) for item in data]
    if gk_data:
        sheet.update(f"G2:K{1+len(gk_data)}", gk_data)

    print(f"已寫入 {len(data)} 筆 Xiaomi（小米+紅米）資料到 Google Sheets（價格補正及G-K欄已保留）")


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "results_xiaomi.json"
    try:
        with open(input_file, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"找不到 {input_file}，請先執行 scraper_xiaomi.py")
        sys.exit(1)
    write_to_sheets(data)
