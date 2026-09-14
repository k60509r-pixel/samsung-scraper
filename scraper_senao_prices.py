import json
import time
import urllib.parse
import warnings
from datetime import datetime

import requests
from urllib3.exceptions import InsecureRequestWarning

warnings.filterwarnings("ignore", category=InsecureRequestWarning)

from scraper_senao import parse_model_string

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_AJAX = "https://helpcenter.senao.com.tw/include/ajax/second_hand.php"
BRANDS = [
    "APPLE", "ASUS", "GOOGLE", "HTC", "OPPO",
    "REALME", "SAMSUNG", "SONY MOBILE", "VIVO", "小米",
]
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

# 最佳狀況：所有條件選「正常/完整」
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


def get_models(session: requests.Session, brand: str) -> list[str]:
    resp = session.post(
        BASE_AJAX,
        headers=HEADERS,
        data={"strType": "C", "brand": brand},
        timeout=15,
        verify=False,
    )
    resp.raise_for_status()
    data = resp.json()
    models: list[str] = []
    unique: set[str] = set()
    for d in data.get("secPriceBookDataList") or []:
        for line in d.get("secPriceBookDataLineList") or []:
            if line.get("brand") == brand:
                m = line.get("model", "")
                if m and m not in unique:
                    models.append(m)
                    unique.add(m)
    return models


def get_price(session: requests.Session, brand: str, model: str) -> int | None:
    # 複製 JS 行為：先 encodeURIComponent，讓 requests 再編碼一次
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


def main() -> list[dict]:
    session = requests.Session()
    all_results: list[dict] = []

    for brand in BRANDS:
        print(f"\n── {brand} ──")
        try:
            models = get_models(session, brand)
        except Exception as e:
            print(f"  [ERROR] 取得機型清單失敗：{e}")
            continue

        print(f"  {len(models)} 個機型")
        no_price = 0

        for model in models:
            parsed = parse_model_string(model)
            try:
                price = get_price(session, brand, model)
            except Exception as e:
                print(f"  [ERROR] {model}：{e}")
                price = None

            record = {
                "brand": brand,
                "raw": model,
                "model": parsed["model"],
                "capacity": parsed["capacity"],
                "year": parsed["year"],
                "price": price,
                "scraped_at": SCRAPED_AT,
            }
            all_results.append(record)

            if price:
                print(f"  {model}: NT${price:,}")
            else:
                no_price += 1

            time.sleep(0.3)

        print(f"  ✓ 完成（不予回收：{no_price} 筆）")
        time.sleep(1.0)

    with open("results_senao_prices.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    total_with_price = sum(1 for r in all_results if r["price"])
    print(f"\n完成！共 {len(all_results)} 筆，有報價：{total_with_price} 筆")
    print("已存入 results_senao_prices.json")
    return all_results


if __name__ == "__main__":
    main()
