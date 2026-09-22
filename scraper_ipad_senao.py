"""
從神腦 (helpcenter.senao.com.tw) 抓 iPad 回收估價，取代原本 scraper_ipad.py 的洋蔥網通來源。

做法是直接打神腦的內部 AJAX 介面（不開瀏覽器），只抓 APPLE 這個品牌，並且只留下
型號字串是 iPad 的項目（神腦把 iPhone 跟 iPad 混在同一個 APPLE 清單裡）。

注意：這支程式刻意「自成一體」，不 import scraper_senao.py / scraper_senao_prices.py，
因為 scraper_senao.py 開頭有 `from playwright.async_api import async_playwright`，
只要 import 它就會要求裝 Playwright（即使我們根本用不到瀏覽器）。
所以把需要的 parse_model_string() / get_models() / get_price() 直接複製一份進來，
這樣只要 `pip install requests` 就能跑，不需要 Playwright。

輸出格式跟 scraper_ipad.py 的 results_ipad.json 完全相同：
    [{"series": "...", "model": "...", "storage": "", "price": ..., "scraped_at": "..."}, ...]
所以可以直接沿用既有的 write_to_sheets_ipad.py（正式上線時）。
測試階段請改用 write_to_sheets_ipad_senao.py，會寫到另一個獨立的測試分頁，
不會動到前端正在讀取的「iPad回收報價」分頁。
"""

import json
import re
import time
import urllib.parse
import warnings
from datetime import datetime

import requests
from urllib3.exceptions import InsecureRequestWarning

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BRAND = "APPLE"

BASE_AJAX = "https://helpcenter.senao.com.tw/include/ajax/second_hand.php"
HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://helpcenter.senao.com.tw/SecondHand_Evaluate.php",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# 最佳狀況：所有條件選「正常/完整」（跟 scraper_senao_prices.py 一致）
BEST_CONDITION = {
    "attribute1": "0",  # Power（開關機）: 正常
    "attribute2": "0",  # Call（電話）: 正常
    "attribute3": "0",  # Camera（照相）: 正常
    "attribute4": "0",  # WiFi: 正常
    "attribute5": "0",  # Screen（螢幕外觀）: 正常
    "attribute6": "0",  # Moist（受潮）: 正常
    "attribute7": "0",  # ComponentD count（缺件數）: 0 = 完整
    "attribute9": "0",  # FingerPrint（指紋）: 正常
}


# ─────────────────────── 以下複製自 scraper_senao.py ───────────────────────

def _normalize_cap(num: int, unit: str) -> str:
    u = re.sub(r"[Bb]$", "", unit).upper()
    if u == "T":
        return f"{num}TB" if 1 <= num <= 4 else ""
    if u == "G":
        if num >= 1024 and num % 1024 == 0:
            return f"{num // 1024}TB"
        return f"{num}GB" if num >= 16 else ""
    return ""


def parse_model_string(raw: str) -> dict:
    working = re.sub(r"^\(神腦\)\s*", "", raw.strip()).strip()
    year = capacity = ""

    m = re.search(r"\(\s*(\d{4})\s*\)\)?\s*$", working)
    if m:
        year = m.group(1)
        working = working[: m.start()].strip()
    else:
        m = re.search(r"(?<![./\d])(\d{4})\s*$", working)
        if m and 2015 <= int(m.group(1)) <= 2030:
            year = m.group(1)
            working = working[: m.start()].strip()

    m = re.search(
        r"\b\d+\s*G(?:B)?\s*/\s*(\d+)\s*(G(?:B)?|T(?:B)?)\s*$",
        working, re.IGNORECASE,
    )
    if m:
        cap = _normalize_cap(int(m.group(1)), m.group(2))
        if cap:
            capacity = cap
            working = working[: m.start()].strip()

    if not capacity:
        pm = re.search(r"\(([^)]+)\)\s*$", working)
        if pm:
            inner = pm.group(1)
            ms = re.search(r"/\s*(\d+)\s*(G(?:B)?|T(?:B)?)\s*$", inner, re.IGNORECASE)
            if ms:
                cap = _normalize_cap(int(ms.group(1)), ms.group(2))
                if cap:
                    capacity = cap
            working = working[: pm.start()].strip()

    if not capacity:
        for cm in reversed(
            list(re.finditer(r"(\d+)\s*(T(?:B)?|G(?:B)?)\b", working, re.IGNORECASE))
        ):
            cap = _normalize_cap(int(cm.group(1)), cm.group(2))
            if cap:
                capacity = cap
                working = (working[: cm.start()] + working[cm.end():]).strip()
                working = re.sub(r"\s+", " ", working)
                break

    model = re.sub(r"\s+", " ", working).strip(" /,()")

    return {"raw": raw.strip(), "model": model, "capacity": capacity, "year": year}


# ─────────────────────── 以下複製自 scraper_senao_prices.py ───────────────────────

def get_models(session: requests.Session, brand: str) -> list[str]:
    resp = session.post(
        BASE_AJAX, headers=HEADERS, data={"strType": "C", "brand": brand},
        timeout=15, verify=False,
    )
    resp.raise_for_status()
    data = resp.json()
    models: list[str] = []
    unique: set[str] = set()
    for d in data.get("secPriceBookDataList") or []:
        for line in d.get("secPriceBookDataLineList") or []:
            if line.get("brand") == brand:
                mdl = line.get("model", "")
                if mdl and mdl not in unique:
                    models.append(mdl)
                    unique.add(mdl)
    return models


def get_price(session: requests.Session, brand: str, model: str) -> int | None:
    data = {
        "strType": "D",
        "brand": brand,
        "model": urllib.parse.quote(model),
        **BEST_CONDITION,
    }
    resp = session.post(BASE_AJAX, headers=HEADERS, data=data, timeout=15, verify=False)
    resp.raise_for_status()
    result = resp.json()
    try:
        line = result["secPriceBookDataList"][0]["secPriceBookDataLineList"][0]
        total = int(line["price"]) - int(line["discountPrice"])
        return total if total > 0 else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ─────────────────────── 以下是 iPad 專用邏輯 ───────────────────────

def is_ipad(raw: str) -> bool:
    """神腦原始字串是否為 iPad（排除 iPhone / Apple Watch 等其他 Apple 產品）。"""
    cleaned = raw.strip()
    if cleaned.startswith("(神腦)"):
        cleaned = cleaned[len("(神腦)"):]
    return cleaned.strip().upper().startswith("IPAD")


def classify_series(model: str) -> str:
    """依型號名稱判斷系列，對應現有 Google Sheet 的「系列」欄位分類方式。"""
    mu = model.upper()
    if "PRO" in mu:
        return "iPad Pro系列"
    if "AIR" in mu:
        return "iPad Air系列"
    if "MINI" in mu:
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

    ipad_models = [mo for mo in models if is_ipad(mo)]
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
