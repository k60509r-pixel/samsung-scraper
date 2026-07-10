import asyncio
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from playwright.async_api import async_playwright, Page

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_URL = "https://www.onion-net.com.tw/used_recycle"
AJAX_URL = "https://www.onion-net.com.tw/ajax/phonename"

# 已確認的 phonecata 值
IPAD_SERIES = [
    {"value": "17", "text": "iPad系列"},
    {"value": "18", "text": "iPad Mini系列"},
    {"value": "19", "text": "iPad Air系列"},
    {"value": "20", "text": "iPad Pro系列"},
]


def parse_price_from_url(url: str):
    m = re.search(r"total=(\d+)", url)
    return int(m.group(1)) if m else None


class OptionParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.options = []

    def handle_starttag(self, tag, attrs):
        if tag == "option":
            attrs_dict = dict(attrs)
            self._current_value = attrs_dict.get("value", "")

    def handle_data(self, data):
        if hasattr(self, "_current_value") and self._current_value and self._current_value != "0":
            self.options.append((self._current_value, data.strip()))
            del self._current_value


async def get_models_via_api(page: Page, phonecata: str, csrf: str) -> list[tuple[str, str]]:
    """直接呼叫 AJAX API 取得機型清單，回傳 [(option_value, option_text), ...]。"""
    url = f"{AJAX_URL}?phonecata={phonecata}&type=data&csrf_test_name={csrf}"
    html = await page.evaluate(f"fetch({url!r}).then(r => r.text())")
    parser = OptionParser()
    parser.feed(html)
    return parser.options


async def get_csrf(page: Page) -> str:
    return await page.evaluate("""
        (() => {
            const inp = document.querySelector('input[name="csrf_test_name"]');
            if (inp) return inp.value;
            const m = document.cookie.match(/csrf_test_name=([^;]+)/);
            return m ? m[1] : '';
        })()
    """)


async def select_full_new_conditions_and_resubmit(page: Page):
    result_url = await page.evaluate("""
        () => new Promise((resolve) => {
            const groups = {};
            document.querySelectorAll('input[type=radio]').forEach(r => {
                if (!groups[r.name]) groups[r.name] = [];
                groups[r.name].push(r);
            });
            Object.values(groups).forEach(radios => {
                radios[0].checked = true;
                radios[0].dispatchEvent(new Event('change', {bubbles: true}));
            });
            const form = document.querySelector('form');
            if (!form) { resolve(null); return; }
            resolve('submitted');
            form.submit();
        })
    """)
    if result_url == 'submitted':
        try:
            await page.wait_for_url("**/used_recycle?**total=**", timeout=12000)
            return page.url
        except Exception:
            pass
    return None


async def scrape_one_model(page: Page, series_name: str, model_text: str,
                           phonename_value: str, phonecata_value: str):
    result = {
        "series": series_name,
        "model": model_text,
        "storage": "",
        "price": None,
        "scraped_at": SCRAPED_AT,
    }
    try:
        for attempt in range(3):
            try:
                await page.goto(BASE_URL, wait_until="commit", timeout=60000)
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
                break
            except Exception:
                if attempt == 2:
                    raise
                await page.wait_for_timeout(2000)
        await page.wait_for_timeout(800)

        # 直接用 JS 設定隱藏欄位並提交（跳過 UI 互動）
        await page.evaluate(f"""
            (() => {{
                // 點 iOS label（觸發網站 JS 狀態切換）
                const iosRadio = document.querySelector('input[name="u_system"][value="u_ios"]');
                if (iosRadio) {{
                    const label = iosRadio.closest('label')
                        || document.querySelector(`label[for="${{iosRadio.id}}"]`)
                        || iosRadio.parentElement;
                    if (label) label.click();
                }}

                // 設 phonecata（強制加入 option，因為 iOS 品牌不在預設清單）
                const cataSel = document.querySelector('select#phonecata');
                if (cataSel) {{
                    let cataOpt = cataSel.querySelector('option[value="{phonecata_value}"]');
                    if (!cataOpt) {{
                        cataOpt = document.createElement('option');
                        cataOpt.value = '{phonecata_value}';
                        cataSel.appendChild(cataOpt);
                    }}
                    cataSel.value = '{phonecata_value}';
                }}

                // 強制加入 phonename option 並選取
                const nameSel = document.querySelector('select#phonename');
                if (nameSel) {{
                    let opt = nameSel.querySelector('option[value="{phonename_value}"]');
                    if (!opt) {{
                        opt = document.createElement('option');
                        opt.value = '{phonename_value}';
                        nameSel.appendChild(opt);
                    }}
                    nameSel.value = '{phonename_value}';
                }}

                // 提交表單
                const form = document.querySelector('form');
                if (form) form.submit();
            }})()
        """)

        try:
            await page.wait_for_url("**/used_recycle?**total=**", timeout=15000)
        except Exception:
            await page.wait_for_timeout(3000)

        initial_price = parse_price_from_url(page.url)
        new_url = await select_full_new_conditions_and_resubmit(page)
        final_price = parse_price_from_url(new_url) if new_url else initial_price
        price = final_price if final_price else initial_price

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(500)
        result["price"] = price
        status = f"NT${price:,}" if price else "不予回收"
        print(f"    {model_text}: {status}")

    except Exception as e:
        print(f"    [ERROR] {model_text}: {e}")

    return result


async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

        # 取得 CSRF token
        ctx0 = await browser.new_context(user_agent=ua)
        page0 = await ctx0.new_page()
        await page0.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        csrf = await get_csrf(page0)
        print(f"CSRF token: {csrf!r}")

        # 逐系列取機型清單並爬取
        for series in IPAD_SERIES:
            series_name = series["text"]
            phonecata = series["value"]
            print(f"\n── {series_name} (phonecata={phonecata}) ──")

            models = await get_models_via_api(page0, phonecata, csrf)
            print(f"  找到 {len(models)} 個機型")

            for phonename_value, model_text in models:
                ctx = await browser.new_context(user_agent=ua)
                page = await ctx.new_page()
                try:
                    data = await scrape_one_model(page, series_name, model_text,
                                                  phonename_value, phonecata)
                finally:
                    await ctx.close()
                results.append(data)
                await asyncio.sleep(0.3)

        await ctx0.close()
        await browser.close()

    with open("results_ipad.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共 {len(results)} 筆，已存入 results_ipad.json")
    return results


if __name__ == "__main__":
    asyncio.run(main())
