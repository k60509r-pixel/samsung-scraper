import asyncio
import json
import re
from datetime import datetime
from playwright.async_api import async_playwright, Page

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_URL = "https://www.onion-net.com.tw/used_recycle"

# 洋蔥網 iPad 四個系列對應的品牌文字（用於比對 select#phonecata）
IPAD_SERIES_KEYWORDS = ["iPad系列", "iPad Mini系列", "iPad Air系列", "iPad Pro系列"]


def parse_price_from_url(url: str):
    m = re.search(r"total=(\d+)", url)
    return int(m.group(1)) if m else None


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


async def select_ios_and_brand(page: Page, brand_value: str):
    """選 iOS radio，再選指定 brand_value，等到 phonename 有選項。"""
    for attempt in range(5):
        await page.evaluate(f"""
            (() => {{
                const radios = Array.from(document.querySelectorAll('input[type=radio]'));
                const iosRadio = radios.find(r =>
                    r.value.toLowerCase() === 'ios' ||
                    (r.nextSibling && r.nextSibling.textContent &&
                     r.nextSibling.textContent.toLowerCase().includes('ios'))
                );
                if (iosRadio) {{
                    iosRadio.checked = true;
                    iosRadio.dispatchEvent(new Event('change', {{bubbles: true}}));
                    iosRadio.dispatchEvent(new Event('click', {{bubbles: true}}));
                }}
                const brandSel = document.querySelector('select#phonecata, select[name=phonecata]');
                if (brandSel) {{
                    brandSel.value = '{brand_value}';
                    brandSel.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            }})()
        """)
        await page.wait_for_timeout(1000)
        count = await page.evaluate("""
            (() => {
                const sel = document.querySelector('select#phonename');
                return sel ? sel.options.length : 0;
            })()
        """)
        if count > 1:
            return
        await page.wait_for_timeout(1000)
    raise RuntimeError(f"select#phonename 無法載入（brand_value={brand_value}）")


async def get_ipad_series_list(page: Page):
    """載入頁面，取得所有 iPad 系列的 brand_value 和顯示名稱。"""
    await page.goto(BASE_URL, wait_until="commit", timeout=60000)
    await page.wait_for_load_state("domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    # 先點 iOS radio，讓品牌下拉更新
    await page.evaluate("""
        (() => {
            const radios = Array.from(document.querySelectorAll('input[type=radio]'));
            const iosRadio = radios.find(r =>
                r.value.toLowerCase() === 'ios' ||
                (r.nextSibling && r.nextSibling.textContent &&
                 r.nextSibling.textContent.toLowerCase().includes('ios'))
            );
            if (iosRadio) {
                iosRadio.checked = true;
                iosRadio.dispatchEvent(new Event('change', {bubbles: true}));
                iosRadio.dispatchEvent(new Event('click', {bubbles: true}));
            }
        })()
    """)
    await page.wait_for_timeout(1500)

    # 找所有含 "iPad" 的品牌選項
    brand_options = await page.evaluate("""
        (() => {
            const sel = document.querySelector('select#phonecata, select[name=phonecata]');
            if (!sel) return [];
            return Array.from(sel.options)
                .filter(o => o.value && o.text.toLowerCase().includes('ipad'))
                .map(o => ({value: o.value, text: o.text.trim()}));
        })()
    """)
    return brand_options


async def get_models_for_series(page: Page, brand_value: str):
    await select_ios_and_brand(page, brand_value)
    await page.wait_for_timeout(500)
    raw = await page.evaluate("""
        (() => {
            const sel = document.querySelector('select#phonename');
            if (!sel) return [];
            return Array.from(sel.options)
                .filter(o => o.value && o.value !== '0')
                .map(o => ({text: o.text.trim(), value: o.value}));
        })()
    """)
    return [(m["text"], m["value"]) for m in raw]


async def scrape_one_model(page: Page, series_name: str, model_text: str, select_value: str, brand_value: str):
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

        await select_ios_and_brand(page, brand_value)

        await page.evaluate(f"""
            (() => {{
                const sel = document.querySelector('select#phonename');
                if (sel) {{
                    sel.value = '{select_value}';
                    sel.dispatchEvent(new Event('change', {{bubbles: true}}));
                    const form = sel.closest('form');
                    if (form) form.submit();
                }}
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

        # 取得所有 iPad 系列的 brand_value
        list_ctx = await browser.new_context(user_agent=ua)
        list_page = await list_ctx.new_page()
        print("取得 iPad 系列清單...")
        brand_options = await get_ipad_series_list(list_page)
        await list_ctx.close()

        if not brand_options:
            print("ERROR: 找不到任何 iPad 品牌選項，請確認網站結構")
            await browser.close()
            return []

        print(f"找到 {len(brand_options)} 個 iPad 系列：")
        for b in brand_options:
            print(f"  value={b['value']}  text={b['text']}")

        # 逐系列爬取機型
        for brand in brand_options:
            series_name = brand["text"]
            brand_value = brand["value"]
            print(f"\n── {series_name} ──")

            ctx = await browser.new_context(user_agent=ua)
            page = await ctx.new_page()
            await page.goto(BASE_URL, wait_until="commit", timeout=60000)
            await page.wait_for_load_state("domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)
            models = await get_models_for_series(page, brand_value)
            await ctx.close()

            print(f"  找到 {len(models)} 個機型，開始爬取...")
            for model_text, select_value in models:
                ctx2 = await browser.new_context(user_agent=ua)
                page2 = await ctx2.new_page()
                try:
                    data = await scrape_one_model(page2, series_name, model_text, select_value, brand_value)
                finally:
                    await ctx2.close()
                results.append(data)
                await asyncio.sleep(0.3)

        await browser.close()

    with open("results_ipad.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共 {len(results)} 筆，已存入 results_ipad.json")
    return results


if __name__ == "__main__":
    asyncio.run(main())
