import asyncio
import json
import re
from datetime import datetime
from playwright.async_api import async_playwright, Page

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_URL = "https://www.onion-net.com.tw/used_recycle"


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


async def select_os_brand(page: Page):
    """設定品牌為 VIVO，等到 select#phonename 有選項為止。"""
    for attempt in range(5):
        brand_value = await page.evaluate("""
            (() => {
                const radios = Array.from(document.querySelectorAll('input[type=radio]'));
                const androidRadio = radios.find(r =>
                    r.value.toLowerCase() === 'android' ||
                    (r.nextSibling && r.nextSibling.textContent &&
                     r.nextSibling.textContent.includes('Android'))
                );
                if (androidRadio) {
                    androidRadio.checked = true;
                    androidRadio.dispatchEvent(new Event('change', {bubbles: true}));
                    androidRadio.dispatchEvent(new Event('click', {bubbles: true}));
                }
                const brandSel = document.querySelector('select#phonecata, select[name=phonecata]');
                if (!brandSel) return null;
                const vivoOpt = Array.from(brandSel.options).find(o =>
                    o.text.toUpperCase().includes('VIVO')
                );
                if (!vivoOpt) return null;
                brandSel.value = vivoOpt.value;
                brandSel.dispatchEvent(new Event('change', {bubbles: true}));
                return vivoOpt.value;
            })()
        """)
        await page.wait_for_timeout(1000)

        count = await page.evaluate("""
            (() => {
                const sel = document.querySelector('select#phonename');
                return sel ? sel.options.length : 0;
            })()
        """)
        if count > 1:
            print(f"  VIVO 品牌 value={brand_value}，找到 {count} 個機型")
            return
        await page.wait_for_timeout(1000)

    raise RuntimeError("select#phonename 無法載入 VIVO 機型，請檢查網站結構")


async def get_vivo_models(page: Page):
    await page.goto(BASE_URL, wait_until="commit", timeout=60000)
    await page.wait_for_load_state("domcontentloaded", timeout=60000)
    await page.wait_for_timeout(2000)

    await select_os_brand(page)
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
    print(f"找到 {len(models)} 個 VIVO 機型")
    return models


async def scrape_one_model(page: Page, model_text: str, select_value: str):
    result = {
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

        await select_os_brand(page)

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
        print(f"  {model_text}: {status}")

    except Exception as e:
        print(f"  [ERROR] {model_text}: {e}")

    return result


async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"

        list_ctx = await browser.new_context(user_agent=ua)
        list_page = await list_ctx.new_page()
        print("取得 VIVO 機型清單...")
        models = await get_vivo_models(list_page)
        await list_ctx.close()

        if not models:
            print("ERROR: 找不到任何 VIVO 機型")
            with open("results_vivo.json", "w", encoding="utf-8") as f:
                json.dump([], f)
            await browser.close()
            return []

        print(f"\n開始爬取 {len(models)} 個機型...\n")
        for model_text, select_value in models:
            ctx = await browser.new_context(user_agent=ua)
            page = await ctx.new_page()
            try:
                data = await scrape_one_model(page, model_text, select_value)
            finally:
                await ctx.close()
            results.append(data)
            await asyncio.sleep(0.3)

        await browser.close()

    with open("results_vivo.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n完成！共 {len(results)} 筆，已存入 results_vivo.json")
    return results


if __name__ == "__main__":
    asyncio.run(main())
