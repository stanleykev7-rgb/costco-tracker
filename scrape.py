import json
import asyncio
import re
import os
import random
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ── Proxy config from GitHub Secrets ─────────────────────────────────────────
PROXY_HOST     = os.environ.get("PROXY_HOST", "")
PROXY_PORT     = os.environ.get("PROXY_PORT", "")
PROXY_USERNAME = os.environ.get("PROXY_USERNAME", "")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
USE_PROXY      = all([PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD])

PRICES_FILE = "prices.json"

PRICE_SELECTORS = {
    "costco":  [".your-price .value", "[automation-id='itemPrice']", ".product-price .value", ".price-current"],
    "amazon":  [".a-price .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen", "#price_inside_buybox", "#priceblock_ourprice", "#priceblock_dealprice"],
    "walmart": ["[itemprop='price']", ".price-characteristic", "[data-automation='buybox-price']"],
    "bestbuy": ["[data-automation='product-price']", ".priceUpdate", ".price_FHDfG", ".productPrice"],
}

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

def load_prices():
    try:
        with open(PRICES_FILE) as f:
            return json.load(f)
    except:
        return {"last_updated": None, "products": []}

def save_prices(data):
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print(f"✅ Saved {PRICES_FILE}")

async def scrape_price(page, url, site):
    print(f"  🌐 Visiting: {url[:70]}...")
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(2000, 3500))
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(800)

        price = None

        for sel in PRICE_SELECTORS.get(site, []):
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    match = re.search(r'\d[\d,]*\.\d{2}', text)
                    if match:
                        price = float(match.group(0).replace(",", ""))
                        print(f"  💰 Price via selector: ${price}")
                        break
            except:
                continue

        if not price:
            content = await page.content()
            jld_matches = re.findall(r'"price"\s*:\s*"?(\d+\.?\d*)"?', content)
            for m in jld_matches:
                val = float(m)
                if 1 < val < 10000:
                    price = val
                    print(f"  💰 Price via JSON-LD: ${price}")
                    break

        if not price:
            content = await page.content()
            dollar_matches = re.findall(r'\$\s*(\d{2,4}\.\d{2})', content)
            for m in dollar_matches:
                val = float(m)
                if 5 < val < 5000:
                    price = val
                    print(f"  💰 Price via regex: ${price}")
                    break

        content = await page.content()
        in_stock = not any(p in content.lower() for p in ["out of stock", "sold out", "unavailable", "currently unavailable"])

        print(f"  {'✅' if price else '❌'} ${price} — {'in stock' if in_stock else 'out of stock'}")
        return price, in_stock

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None, None

async def scrape_rfd(page, product_name):
    print(f"  🔍 RFD check: {product_name}")
    try:
        query = product_name.replace(" ", "+")
        await page.goto(f"https://forums.redflagdeals.com/search/?q={query}&forums=9", timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        first = page.locator(".thread_title a, h3 a").first
        if await first.count() == 0:
            return None
        thread_url = await first.get_attribute("href")
        thread_title = (await first.inner_text()).strip()
        if not thread_url.startswith("http"):
            thread_url = "https://forums.redflagdeals.com" + thread_url
        await page.goto(thread_url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        content = await page.inner_text("body")
        prices = re.findall(r'\$\s*(\d{2,4}\.\d{2})', content)
        if not prices:
            return None
        price = float(prices[0])
        print(f"  🔥 Deal: ${price} — {thread_title[:60]}")
        return {"found": True, "price": price, "title": thread_title[:120], "url": thread_url, "found_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        print(f"  ❌ RFD error: {e}")
        return None

async def main():
    data = load_prices()
    products = data.get("products", [])

    if not products:
        print("⚠️  No products configured yet.")
        return

    proxy_config = None
    if USE_PROXY:
        proxy_config = {"server": f"http://{PROXY_HOST}:{PROXY_PORT}", "username": PROXY_USERNAME, "password": PROXY_PASSWORD}
        print(f"🔒 Using proxy: {PROXY_HOST}:{PROXY_PORT}")
    else:
        print("⚠️  No proxy — some sites may block")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )

        for product in products:
            print(f"\n📦 {product['name']}")
            site_data = product.setdefault("site_data", {})

            for site_key, site_info in site_data.items():
                url = site_info.get("url")
                if not url:
                    print(f"  ⏭️  No URL for {site_key} — skipping")
                    continue

                context = await browser.new_context(
                    proxy=proxy_config,
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                    locale="en-CA",
                    timezone_id="America/Toronto",
                    extra_http_headers={"Accept-Language": "en-CA,en;q=0.9"}
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

                price, in_stock = await scrape_price(page, url, site_key)

                history = site_info.get("history", [])
                if price:
                    today = datetime.now(timezone.utc).strftime("%b %-d")
                    if history and history[-1]["d"] == today:
                        history[-1]["p"] = price
                    else:
                        history.append({"d": today, "p": price})
                    history = history[-60:]

                was = site_info.get("price")
                site_info.update({
                    "price": price,
                    "was": was if was and was != price else site_info.get("was"),
                    "in_stock": in_stock,
                    "history": history,
                    "last_checked": datetime.now(timezone.utc).isoformat(),
                    "error": None if price else "Price not found"
                })

                await context.close()
                await asyncio.sleep(random.randint(2, 4))

            # RFD check
            context = await browser.new_context(user_agent=random.choice(USER_AGENTS))
            page = await context.new_page()
            product["rfd_deal"] = await scrape_rfd(page, product["name"])
            await context.close()
            await asyncio.sleep(random.randint(1, 3))

        await browser.close()

    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_prices(data)
    print("\n✅ All done!")

asyncio.run(main())
