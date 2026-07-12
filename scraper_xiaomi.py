import asyncio
import json
import re
from datetime import datetime
from playwright.async_api import async_playwright, Page

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_URL = "https://www.onion-net.com.tw/used_recycle"

# 小米 + 紅米 都歸入 Xiaomi
BRAND_SEARCHES = ["小米", "紅米"]


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


async def select_os_brand(page: Page, brand_search: str):
    for attempt in range(5):
        brand_value = await page.evaluate(f"""
            (() => {{
                const radios = Array.from(document.querySelectorAll('input[type=radio]'));
                const androidRadio = radios.find(r =>
                    r.value.toLowerCase() === 'android' ||
                    (r.nextSibling && r.nextSibling.textContent &&
                     r.nextSibling.textContent.includes('Android'))
                );
                if (androidRadio) {{
                    androidRadio.checked = true;
                    androidRadio.dispatchEvent(new Event('change', {{bubbles: true}}));
                    androidRadio.dispatchEvent(new Event('click', {{bubbles: true}}));
                }}
                const brandSel = document.querySelector('select#phonecata, select[name=phonecata]');
                if (!brandSel) return null;
                const opt = Array.from(brandSel.options).find(o =>
                    o.text.includes('{brand_search}')
                );
                if (!opt) return null;
                brandSel.value = opt.value;
                brandSel.dispatchEvent(new Event('change', {{bubbles: true}}));
                return opt.value;
            }})()
        """)
        await page.wait_for_timeout(1000)
        count = await page.evaluate("""
            (() => { const sel = document.querySelector('select#phonename'); return sel ? sel.options.length : 0; })()
        """)
        if count > 1:
            print(f"  {brand_search} 品牌 value={brand_value}，找到 {count} 個機型")
            return
        await page.wait_for_timeout(1000)
    raise RuntimeError(f"select#phonename 無法載入 {brand_search} 機型")


async def get_models(page: Page, brand_search: str):
    await page.goto(BASE_URL, wait_until="commit", timeout=60000)
    await page.wait_for_load_state("domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)
    await select_os_brand(page, brand_search)
    await page.wait_for_timeout(1000)
    raw_models = await page.evaluate("""
        (() => {
            const sel = document.querySelector('select#phonename');
            if (!sel) return [];
            return Array.from(sel.options)
                .filter(o => o.value && o.value !== '0')
                .map(o => ({text: o.text.trim(), value: o.value}));
        })()
    """)
    models = [(m["text"], m["value"]) for m in raw_models]
    print(f"找到 {len(models)} 個 {brand_search} 機型")
    return models


async def scrape_one_model(page: Page, model_text: str, select_value: str, brand_search: str):
    result = {"model": model_text, "storage": "", "price": None, "scraped_at": SCRAPED_AT}
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
        await select_os_brand(page, brand_search)
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
        print(f"  {model_text}: {'NT$'+f'{price:,}' if price else '不予回收'}")
    except Exception as e:
        print(f"  [ERROR] {model_text}: {e}")
    return result


async def main():
    all_results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

        for brand_search in BRAND_SEARCHES:
            print(f"\n── {brand_search} ──")
            list_ctx = await browser.new_context(user_agent=ua)
            list_page = await list_ctx.new_page()
            try:
                models = await get_models(list_page, brand_search)
            except Exception as e:
                print(f"  [ERROR] 無法取得 {brand_search} 機型：{e}")
                models = []
            await list_ctx.close()

            for model_text, select_value in models:
                ctx = await browser.new_context(user_agent=ua)
                page = await ctx.new_page()
                try:
                    data = await scrape_one_model(page, model_text, select_value, brand_search)
                finally:
                    await ctx.close()
                all_results.append(data)
                await asyncio.sleep(0.3)

        await browser.close()

    with open("results_xiaomi.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n完成！共 {len(all_results)} 筆（小米+紅米），已存入 results_xiaomi.json")
    return all_results


if __name__ == "__main__":
    asyncio.run(main())
