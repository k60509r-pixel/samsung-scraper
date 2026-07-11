import json
import re
import time
from datetime import datetime
from html.parser import HTMLParser

import requests
from bs4 import BeautifulSoup

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_URL = "https://www.onion-net.com.tw/used_recycle"
AJAX_URL = "https://www.onion-net.com.tw/ajax/phonename"

IPAD_SERIES = [
    {"value": "17", "text": "iPad系列"},
    {"value": "18", "text": "iPad Mini系列"},
    {"value": "19", "text": "iPad Air系列"},
    {"value": "20", "text": "iPad Pro系列"},
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
}


class OptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.options: list[tuple[str, str]] = []
        self._cur_val = None

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            d = dict(attrs)
            v = d.get("value", "")
            if v and v != "0":
                self._cur_val = v

    def handle_data(self, data):
        if self._cur_val is not None:
            self.options.append((self._cur_val, data.strip()))
            self._cur_val = None

    def handle_endtag(self, tag):
        if tag == "option":
            self._cur_val = None


def parse_price_from_url(url: str):
    m = re.search(r"total=(\d+)", url)
    return int(m.group(1)) if m else None


def setup_session() -> tuple[requests.Session, str, str, dict]:
    """
    建立 session，取得：
      - CSRF token
      - 表單 action URL
      - 全部 radio group 的「最佳值」（每組第一個 radio）
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    resp = session.get(BASE_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # CSRF
    csrf_inp = soup.find("input", {"name": "csrf_test_name"})
    csrf = csrf_inp["value"] if csrf_inp else ""
    print(f"CSRF: {csrf!r}")

    # 找含有 select#phonecata 的回收估價表單（不是頁面頂部的搜尋表單）
    phonecata_sel = soup.find("select", {"id": "phonecata"})
    form = phonecata_sel.find_parent("form") if phonecata_sel else None
    if not form:
        form = soup.find("form", action=lambda a: a and "recycle" in str(a).lower())
    action = form.get("action", BASE_URL) if form else BASE_URL
    if action.startswith("/"):
        action = "https://www.onion-net.com.tw" + action
    print(f"Form action: {action!r}")

    # 收集所有 radio group 的第一個值（最佳條件）
    radio_groups: dict[str, str] = {}
    for radio in soup.find_all("input", type="radio"):
        name = radio.get("name", "")
        if name and name not in radio_groups:
            radio_groups[name] = radio.get("value", "")

    print(f"Radio groups: {radio_groups}")
    return session, action, csrf, radio_groups


def get_models(session: requests.Session, phonecata: str, csrf: str) -> list[tuple[str, str]]:
    """呼叫 AJAX API 取得機型清單。"""
    url = f"{AJAX_URL}?phonecata={phonecata}&type=data&csrf_test_name={csrf}"
    resp = session.get(url, timeout=20)
    parser = OptionParser()
    parser.feed(resp.text)
    return parser.options


def scrape_model(
    session: requests.Session,
    action: str,
    csrf: str,
    radio_groups: dict,
    phonecata: str,
    phonename_value: str,
    model_text: str,
    series_name: str,
) -> dict:
    result = {
        "series": series_name,
        "model": model_text,
        "storage": "",
        "price": None,
        "scraped_at": SCRAPED_AT,
    }

    # 組合表單欄位：最佳條件 + 覆寫 iOS 必要欄位
    form_data = dict(radio_groups)          # 所有 radio 預設最佳值
    form_data["csrf_test_name"] = csrf
    form_data["u_system"] = "u_ios"        # 強制 iOS
    form_data["phonecata"] = phonecata
    form_data["phonename"] = phonename_value

    for attempt in range(3):
        try:
            resp = session.post(action, data=form_data, timeout=30, allow_redirects=True)
            price = parse_price_from_url(resp.url)

            # 若 total 不在 URL，嘗試從 response body 找
            if price is None:
                m = re.search(r"total=(\d+)", resp.text)
                price = int(m.group(1)) if m else None

            result["price"] = price
            status = f"NT${price:,}" if price else "不予回收"
            print(f"    {model_text}: {status}  (url={resp.url[:80]})")
            break
        except Exception as e:
            print(f"    [ERROR attempt {attempt+1}] {model_text}: {e}")
            time.sleep(2)

    return result


def main():
    results = []

    session, action, csrf, radio_groups = setup_session()

    for series in IPAD_SERIES:
        series_name = series["text"]
        phonecata = series["value"]
        print(f"\n── {series_name} (phonecata={phonecata}) ──")

        models = get_models(session, phonecata, csrf)
        print(f"  找到 {len(models)} 個機型")

        for phonename_value, model_text in models:
            data = scrape_model(
                session, action, csrf, radio_groups,
                phonecata, phonename_value, model_text, series_name,
            )
            results.append(data)
            time.sleep(0.4)   # 避免過於頻繁

    with open("results_ipad.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共 {len(results)} 筆，已存入 results_ipad.json")
    return results


if __name__ == "__main__":
    main()
