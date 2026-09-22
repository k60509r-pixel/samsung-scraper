"""
從神腦 (helpcenter.senao.com.tw) 抓 iPad 回收估價，取代原本 scraper_ipad.py 的洋蔥網通來源。

做法跟 scraper_senao_prices.py 一樣：直接打神腦的內部 AJAX 介面（不開瀏覽器），
只是這裡只抓 APPLE 這個品牌，並且只留下型號字串是 iPad 的項目（神腦把 iPhone 跟 iPad
混在同一個 APPLE 清單裡）。

輸出格式跟 scraper_ipad.py 的 results_ipad.json 完全相同：
    [{"series": "...", "model": "...", "storage": "", "price": ..., "scraped_at": "..."}, ...]
所以可以直接沿用既有的 write_to_sheets_ipad.py（正式上線時）。
測試階段請改用 write_to_sheets_ipad_senao.py，會寫到另一個獨立的測試分頁，
不會動到前端正在讀取的「iPad回收報價」分頁。
"""

import json
import re
import time
from datetime import datetime

from scraper_senao import parse_model_string
from scraper_senao_prices import get_models, get_price

import requests

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BRAND = "APPLE"


def is_ipad(raw: str) -> bool:
    """神腦原始字串是否為 iPad（排除 iPhone / Apple Watch 等其他 Apple 產品）。"""
    cleaned = raw.strip()
    if cleaned.startswith("(神腦)"):
        cleaned = cleaned[len("(神腦)"):]
    return cleaned.strip().upper().startswith("IPAD")


def classify_series(model: str) -> str:
    """依型號名稱判斷系列，對應現有 Google Sheet 的「系列」欄位分類方式。"""
    m = model.upper()
    if "PRO" in m:
        return "iPad Pro系列"
    if "AIR" in m:
        return "iPad Air系列"
    if "MINI" in m:
        return "iPad Mini系列"
    return "iPad系列"


def normalize_spacing(s: str) -> str:
    """神腦原始字串常常沒有空格（例如 'IPADAIR10.9LTE'），這裡把它整理成
    好讀的格式（'iPad Air 10.9 LTE'）。刻意用 {2,} 避免把 'M5' 這種晶片代號拆開。"""
    s = re.sub(r"(?i)IPAD", "iPad ", s)
    for kw in ["AIR", "MINI", "PRO"]:
        s = re.sub(rf"(?i){kw}", f" {kw.title()} ", s)
    for kw in ["WIFI", "LTE"]:
        s = re.sub(rf"(?i){kw}", f" {kw} ", s)
    s = re.sub(r"([A-Za-z]{2,})(\d)", r"\1 \2", s)
    s = re.sub(r"(\d)([A-Za-z]{2,})", r"\1 \2", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_display_model(parsed: dict) -> str:
    """把 model + capacity 組成單一字串（例如 'iPad Pro 11.0 WIFI (2022) 256GB'），
    容量務必放在最後一個詞，這樣前端 splitModelStorage() 的正規表示式才能正確切出容量。

    神腦同一個尺寸/容量的 iPad Pro、Air 常常有好幾個年份的版本（不同代），
    只留 model+capacity 會讓不同年份的機型撞名、在前端只剩最後一筆的價格。
    所以把年份放進型號字串裡（容量前面）當作區分依據。
    """
    model = normalize_spacing(parsed["model"].strip())
    year = parsed["year"].strip()
    capacity = parsed["capacity"].strip()
    if year:
        model = f"{model} ({year})"
    if capacity:
        return f"{model} {capacity}"
    return model


def main():
    session = requests.Session()
    results = []

    print(f"── {BRAND}（篩選 iPad）──")
    models = get_models(session, BRAND)
    print(f"  神腦 APPLE 品牌共 {len(models)} 個型號")

    ipad_models = [m for m in models if is_ipad(m)]
    print(f"  篩選出 {len(ipad_models)} 個 iPad 型號")

    no_price = 0
    for raw in ipad_models:
        parsed = parse_model_string(raw)
        try:
            price = get_price(session, BRAND, raw)
        except Exception as e:
            print(f"  [ERROR] {raw}：{e}")
            price = None

        if price is None:
            no_price += 1

        results.append({
            "series": classify_series(parsed["model"]),
            "model": build_display_model(parsed),
            "storage": "",
            "price": price,
            "scraped_at": SCRAPED_AT,
        })

        status = f"NT${price:,}" if price else "不予回收"
        print(f"  {raw}: {status}")
        time.sleep(0.3)

    print(f"\n完成！共 {len(results)} 筆，有報價：{len(results) - no_price} 筆")

    with open("results_ipad_senao.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("已存入 results_ipad_senao.json")

    return results


if __name__ == "__main__":
    main()
