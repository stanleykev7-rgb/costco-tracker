import json
import asyncio
import re
import os
import random
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ── Proxy config from environment (GitHub Secrets) ───────────────────────────
PROXY_HOST     = os.environ.get("PROXY_HOST", "")
PROXY_PORT     = os.environ.get("PROXY_PORT", "")
PROXY_USERNAME = os.environ.get("PROXY_USERNAME", "")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
USE_PROXY      = all([PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD])

PRICES_FILE = "prices.json"

SITE_SEARCH_TEMPLATES = {
    "costco":  "site:costco.ca {}",
    "amazon":  "site:amazon.ca {}",
    "walmart": "site:walmart.ca {}",
    "bestbuy": "site:bestbuy.ca {}",
}

SITE_DOMAINS = {
    "costco":  "costco.ca",
    "amazon":  "amazon.ca",
    "walmart": "walmart.ca",
    "bestbuy": "bestbuy.ca",
}

PRICE_SELECTORS = {
    "costco":  [".your-price .value", "[automation-id='itemPrice']", ".product-price"],
    "amazon":  [".a-price-whole", "#priceblock_ourprice", ".a-offscreen", "#price_inside_buybox"],
    "walmart": [".price-characteristic", "[itemprop='price']", ".prod-PriceHero"],
    "bestbuy": [".priceUpdate", ".sr-only", "[data-automation='product-price']"],
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

async def discover_url(page, product_name, site):
    """Search Google to find the product URL on a given site."""
    query = SITE_SEARCH_TEMPLATES[site].format(product_name)
    search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}&num=5"
    print(f"  🔍 Discovering URL for {site}: {product_name}")
    try:
        await page.goto(search_url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(1500, 2500))
        # Extract first matching result link
        links = await page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        domain = SITE_DOMAINS[site]
        for link in links:
            if domain in link and "/search" not in link and "google" not in link:
                # Clean up Google redirect wrapper
                clean = re.search(r'https?://[^&"]+' + domain + r'[^&"]+', link)
                if clean:
                    url = clean.group(0)
                    print(f"  ✅ Found: {url[:80]}")
                    return url
    except Exception as e:
        print(f"  ❌ URL discovery failed for {site}: {e}")
    return None

async def scrape_price(page, url, site):
    """Visit a product URL and extract the price."""
    if not url:
        return None, None
    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(2000, 3500))
        await page.evaluate("window.scrollBy(0, 300)")
        await page.wait_for_timeout(500)

        price = None
        for sel in PRICE_SELECTORS.get(site, []):
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    match = re.search(r'\d+[\.,]\d{2}', text)
                    if match:
                        price = float(match.group(0).replace(",", ""))
                        print(f"  💰 Price via selector: ${price}")
                        break
            except:
                continue

        if not price:
            content = await page.content()
            # Try JSON-LD structured data
            jld = re.search(r'"price"\s*:\s*"?(\d+\.?\d*)"?', content)
            if jld:
                price = float(jld.group(1))
                print(f"  💰 Price via JSON-LD: ${price}")
            else:
                match = re.search(r'\$\s*(\d{2,4}\.\d{2})', content)
                if match:
                    price = float(match.group(1))
                    print(f"  💰 Price via regex: ${price}")

        content = await page.content()
        in_stock = not any(p in content.lower() for p in ["out of stock", "sold out", "unavailable"])
        return price, in_stock

    except Exception as e:
        print(f"  ❌ Scrape error: {e}")
        return None, None

async def scrape_rfd(page, product_name):
    """Search RedFlagDeals for a deal on this product."""
    print(f"  🔍 Checking RFD for: {product_name}")
    try:
        query = product_name.replace(" ", "+")
        url = f"https://forums.redflagdeals.com/search/?q={query}&forums=9"
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        first = page.locator(".thread_title a, h3 a").first
        if await first.count() == 0:
            return None

        thread_url = await first.get_attribute("href")
        thread_title = (await first.inner_text()).strip()
        if not thread_url.startswith("http"):
            thread_url = "https://forums.redflagdeals.com" + thread_url

        await page.goto(thread_url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        content = await page.inner_text("body")
        prices = re.findall(r'\$\s*(\d{2,4}\.\d{2})', content)
        if not prices:
            return None

        price = float(prices[0])
        print(f"  🔥 RFD deal found: ${price} — {thread_title[:60]}")
        return {
            "found": True,
            "price": price,
            "title": thread_title[:120],
            "url": thread_url,
            "found_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"  ❌ RFD error: {e}")
        return None

async def main():
    data = load_prices()
    products = data.get("products", [])

    if not products:
        print("⚠️  No products configured yet. Add products via the tracker UI.")
        return

    proxy_config = None
    if USE_PROXY:
        proxy_config = {
            "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
            "username": PROXY_USERNAME,
            "password": PROXY_PASSWORD,
        }
        print(f"🔒 Using residential proxy: {PROXY_HOST}:{PROXY_PORT}")
    else:
        print("⚠️  No proxy configured — some sites may block requests")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
            ]
        )

        for product in products:
            print(f"\n📦 Processing: {product['name']}")
            sites_to_track = product.get("sites_to_track", list(SITE_DOMAINS.keys()))

            for site in sites_to_track:
                context = await browser.new_context(
                    proxy=proxy_config,
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": random.randint(1280,1920), "height": random.randint(768,1080)},
                    locale="en-CA",
                    timezone_id="America/Toronto",
                    extra_http_headers={
                        "Accept-Language": "en-CA,en;q=0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    }
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")

                # Discover URL if not already stored
                site_data = product.setdefault("site_data", {}).setdefault(site, {})
                if not site_data.get("url"):
                    url = await discover_url(page, product["name"], site)
                    site_data["url"] = url
                else:
                    url = site_data["url"]

                # Scrape price
                price, in_stock = await scrape_price(page, url, site)

                # Update history
                history = site_data.get("history", [])
                if price:
                    today = datetime.now(timezone.utc).strftime("%b %-d")
                    # Update today's entry or append
                    if history and history[-1]["d"] == today:
                        history[-1]["p"] = price
                    else:
                        history.append({"d": today, "p": price})
                    # Keep last 60 data points
                    history = history[-60:]

                was = site_data.get("price")  # previous price
                site_data.update({
                    "price": price,
                    "was": was if was and was != price else site_data.get("was"),
                    "in_stock": in_stock,
                    "url": url,
                    "history": history,
                    "last_checked": datetime.now(timezone.utc).isoformat(),
                    "error": None if price else "Price not found"
                })

                await context.close()
                await asyncio.sleep(random.randint(2, 4))

            # Check RFD
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": 1280, "height": 800},
                locale="en-CA",
            )
            page = await context.new_page()
            rfd_result = await scrape_rfd(page, product["name"])
            product["rfd_deal"] = rfd_result
            await context.close()
            await asyncio.sleep(random.randint(1, 3))

        await browser.close()

    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    save_prices(data)
    print("\n✅ All done!")

asyncio.run(main())
