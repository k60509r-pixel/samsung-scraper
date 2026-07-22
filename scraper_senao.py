import asyncio
import json
import re
from datetime import datetime
from playwright.async_api import async_playwright

SCRAPED_AT = datetime.now().strftime("%Y-%m-%d %H:%M")
BASE_URL = "https://helpcenter.senao.com.tw/SecondHand_Evaluate.php"

BRANDS = [
    "APPLE", "ASUS", "GOOGLE", "HTC", "OPPO",
    "REALME", "SAMSUNG", "SONY MOBILE", "VIVO", "小米",
]

# ─────────────────────────── 解析邏輯 ───────────────────────────

def _normalize_cap(num: int, unit: str) -> str:
    """
    把 (num, unit) 正規化成容量字串。
    unit 接受 G / GB / T / TB（大小寫均可）。
    回傳空字串代表「不是有效容量」。
    """
    u = re.sub(r"[Bb]$", "", unit).upper()   # GB→G、TB→T
    if u == "T":
        # 1T / 2T → 1TB / 2TB；num > 4 通常是手機型號（10T、12T…）
        return f"{num}TB" if 1 <= num <= 4 else ""
    if u == "G":
        if num >= 1024 and num % 1024 == 0:
            return f"{num // 1024}TB"          # 1024G → 1TB
        return f"{num}GB" if num >= 16 else ""  # < 16 視為 4G/5G 網路規格
    return ""


def parse_model_string(raw: str) -> dict:
    """
    拆解神腦原始機型字串，回傳 {raw, model, capacity, year}。

    處理順序：
    1. 去除 (神腦) 前綴
    2. 擷取年份（括號格式 (2024)、無括號末尾 2020）
    3. 擷取容量
       A. 末尾 RAM/Storage 斜線格式：8G/128G
       B. 括號內 RAM/Storage（Samsung）：(A076B 4G/128G)
       C. 一般末尾格式：128GB / 256G / 1T / 2TB
    4. 清理機型字串
    """
    working = re.sub(r"^\(神腦\)\s*", "", raw.strip()).strip()
    year = capacity = ""

    # ── 步驟 2：年份 ──────────────────────────────────────────────
    # 優先：括號格式 (YYYY) 或 (YYYY))（含多餘右括號）
    m = re.search(r"\(\s*(\d{4})\s*\)\)?\s*$", working)
    if m:
        year = m.group(1)
        working = working[: m.start()].strip()
    else:
        # 次要：末尾孤立 4 位年份（前面不是數字 / . /）
        m = re.search(r"(?<![./\d])(\d{4})\s*$", working)
        if m and 2015 <= int(m.group(1)) <= 2030:
            year = m.group(1)
            working = working[: m.start()].strip()

    # ── 步驟 3A：末尾 RAM/Storage 斜線格式 ──────────────────────
    # 例：8G/128G  |  12G/ 256G  |  16G/1024G
    m = re.search(
        r"\b\d+\s*G(?:B)?\s*/\s*(\d+)\s*(G(?:B)?|T(?:B)?)\s*$",
        working, re.IGNORECASE,
    )
    if m:
        cap = _normalize_cap(int(m.group(1)), m.group(2))
        if cap:
            capacity = cap
            working = working[: m.start()].strip()

    # ── 步驟 3B：括號內 RAM/Storage（Samsung 格式）──────────────
    # 例：(A076B 4G/128G)  |  (A136-4G/64G)
    if not capacity:
        pm = re.search(r"\(([^)]+)\)\s*$", working)
        if pm:
            inner = pm.group(1)
            ms = re.search(r"/\s*(\d+)\s*(G(?:B)?|T(?:B)?)\s*$", inner, re.IGNORECASE)
            if ms:
                cap = _normalize_cap(int(ms.group(1)), ms.group(2))
                if cap:
                    capacity = cap
            # 括號不論是否找到容量，一律剝除（含純代號括號 (A307)）
            working = working[: pm.start()].strip()

    # ── 步驟 3C：末尾一般格式 ────────────────────────────────────
    # 例：128GB / 256G / 1T / 2TB（從右掃描，取第一個有效值）
    if not capacity:
        for cm in reversed(
            list(re.finditer(r"(\d+)\s*(T(?:B)?|G(?:B)?)\b", working, re.IGNORECASE))
        ):
            cap = _normalize_cap(int(cm.group(1)), cm.group(2))
            if cap:
                capacity = cap
                working = (working[: cm.start()] + working[cm.end() :]).strip()
                working = re.sub(r"\s+", " ", working)
                break

    # ── 步驟 4：清理機型字串 ─────────────────────────────────────
    model = re.sub(r"\s+", " ", working).strip(" /,()")

    return {
        "raw": raw.strip(),
        "model": model,
        "capacity": capacity,
        "year": year,
    }


# ─────────────────────────── 爬蟲邏輯 ───────────────────────────

async def scrape_brand(page, brand: str) -> tuple[list[dict], list[str]]:
    results: list[dict] = []
    exceptions: list[str] = []

    for attempt in range(3):
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # 選廠牌
            try:
                await page.select_option("#sBrand", label=brand)
            except Exception:
                await page.select_option("#sBrand", value=brand)

            # 等 #sModel 載入（最多 20 秒）
            loaded = False
            for _ in range(20):
                count = await page.evaluate(
                    "document.querySelector('#sModel')?.options.length ?? 0"
                )
                if count > 1:
                    loaded = True
                    break
                await page.wait_for_timeout(1000)

            if not loaded:
                raise TimeoutError(f"#sModel 未在時限內載入（目前 {count} 個選項）")

            # 讀取所有機型選項文字
            model_texts: list[str] = await page.evaluate("""
                () => Array.from(document.querySelector('#sModel').options)
                    .map(o => o.text.trim())
                    .filter(t => t && t !== '請選擇手機機型')
            """)

            print(f"  [{brand}] 找到 {len(model_texts)} 筆")

            for raw in model_texts:
                parsed = parse_model_string(raw)
                parsed["brand"] = brand
                parsed["scraped_at"] = SCRAPED_AT
                results.append(parsed)
                # 無法擷取容量 → 列為例外（year 空白很常見，不單獨列）
                if not parsed["capacity"]:
                    exceptions.append(raw)

            return results, exceptions

        except Exception as e:
            print(f"  [{brand}] 第 {attempt + 1} 次失敗：{e}")
            if attempt == 2:
                exceptions.append(f"[LOAD_ERROR] {e}")
                return results, exceptions
            await page.wait_for_timeout(3000)

    return results, exceptions


async def main():
    all_results: list[dict] = []
    all_exceptions: dict[str, list[str]] = {}

    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        for brand in BRANDS:
            print(f"\n── {brand} ──")
            ctx = await browser.new_context(user_agent=ua)
            page = await ctx.new_page()
            try:
                results, exceptions = await scrape_brand(page, brand)
                all_results.extend(results)
                if exceptions:
                    all_exceptions[brand] = exceptions
                    show = exceptions[:5]
                    for ex in show:
                        print(f"    例外: {ex!r}")
                    if len(exceptions) > 5:
                        print(f"    ...（共 {len(exceptions)} 筆例外）")
            finally:
                await ctx.close()

            await asyncio.sleep(1.5)   # 品牌切換間隔，降低被擋風險

        await browser.close()

    with open("results_senao.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)

    # ── 彙總報告 ────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"完成！共 {len(all_results)} 筆，已存入 results_senao.json")
    if all_exceptions:
        print("\n【例外字串彙總（容量無法解析）】")
        for brand, exs in all_exceptions.items():
            print(f"  {brand}：{len(exs)} 筆")
            for ex in exs:
                print(f"    - {ex!r}")
    else:
        print("無例外字串（所有機型均成功解析容量）")

    return all_results


if __name__ == "__main__":
    asyncio.run(main())
