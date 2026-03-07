import json
import asyncio
import re
import os
import random
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ── Proxy config ──────────────────────────────────────────────────────────────
PROXY_HOST     = os.environ.get("PROXY_HOST", "")
PROXY_PORT     = os.environ.get("PROXY_PORT", "")
PROXY_USERNAME = os.environ.get("PROXY_USERNAME", "")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
USE_PROXY      = all([PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD])

PRICES_FILE = "prices.json"

# ── Per-site CSS selectors (in priority order) ────────────────────────────────
PRICE_SELECTORS = {
    "costco": [
        "[automation-id='itemPrice']",
        ".your-price .value",
        ".product-price .value",
        "[class*='your-price'] .value",
        "[class*='itemPrice']",
        "#product-price",
        "[itemprop='price']",
        ".e-price-display",
    ],
    "amazon": [
        ".a-price .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-offscreen",
        "#price_inside_buybox",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#apex_offerDisplay_desktop .a-offscreen",
        ".a-price-whole",
    ],
    "walmart": [
        "[itemprop='price']",
        "[data-automation='buybox-price']",
        ".price-characteristic",
        ".prod-PriceHero [class*='price']",
        "[data-testid='price-wrap'] span",
        ".price-group",
    ],
    "bestbuy": [
        "[data-automation='product-price']",
        ".priceUpdate .screenReaderOnly",
        ".price_FHDfG",
        ".productPrice",
        "[class*='price'][class*='value']",
        ".price-display",
    ],
    "canadiantire": [
        "[data-testid='product-price']",
        ".price-display__content",
        ".price__reg",
        "[class*='price__value']",
        ".price-display .price",
    ],
    "staples": [
        ".price-box .price",
        "[data-price]",
        ".our-price",
        ".regular-price",
        "[itemprop='price']",
    ],
    "thesource": [
        "[data-testid='product-price']",
        ".price-display",
        ".productPrice",
        "[itemprop='price']",
    ],
    "londondrugs": [
        ".price",
        ".product-price",
        "[itemprop='price']",
    ],
    "sportchek": [
        "[data-testid='price']",
        ".price-label",
        ".product-price",
        "[itemprop='price']",
    ],
    "winners": [
        ".price",
        ".product-price__value",
        "[itemprop='price']",
    ],
    "homedepot": [
        "[data-testid='product-price']",
        ".price-format__main-price",
        "#standard-price",
        "[itemprop='price']",
    ],
}

# Extra JS wait time per site (ms) — JS-heavy sites need more time
SITE_WAIT = {
    "costco":       6000,
    "amazon":       3000,
    "walmart":      3000,
    "bestbuy":      4000,
    "canadiantire": 3000,
    "staples":      2000,
    "thesource":    3000,
    "londondrugs":  2000,
    "sportchek":    3000,
    "winners":      2000,
    "homedepot":    3000,
}

# Out-of-stock phrases to check
OOS_PHRASES = [
    "out of stock", "sold out", "unavailable",
    "currently unavailable", "not available",
    "out of stock online", "temporarily out of stock",
    "en rupture de stock",  # French
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def detect_site(url):
    """Detect retailer from URL."""
    mapping = {
        "costco.ca":        "costco",
        "amazon.ca":        "amazon",
        "walmart.ca":       "walmart",
        "bestbuy.ca":       "bestbuy",
        "canadiantire.ca":  "canadiantire",
        "staples.ca":       "staples",
        "thesource.ca":     "thesource",
        "londondrugs.ca":   "londondrugs",
        "sportchek.ca":     "sportchek",
        "winners.ca":       "winners",
        "homedepot.ca":     "homedepot",
    }
    for domain, key in mapping.items():
        if domain in url:
            return key
    return "unknown"

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

def extract_price_from_text(text):
    """Extract a price float from a string."""
    text = text.replace(",", "").strip()
    match = re.search(r'\d[\d]*\.\d{2}', text)
    if match:
        val = float(match.group(0))
        if 1 < val < 50000:
            return val
    return None

async def scrape_price(page, url, site):
    """Visit a product page and extract price + stock status."""
    print(f"  🌐 [{site}] {url[:70]}...")
    try:
        await page.goto(url, timeout=40000, wait_until="networkidle")
        extra_wait = SITE_WAIT.get(site, 3000) + random.randint(500, 1500)
        await page.wait_for_timeout(extra_wait)
        await page.evaluate("window.scrollBy(0, 500)")
        await page.wait_for_timeout(1000)

        price = None

        # ── STEP 1: CSS selectors ────────────────────────────────────────────
        for sel in PRICE_SELECTORS.get(site, []):
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    p = extract_price_from_text(text)
                    if p:
                        price = p
                        print(f"  💰 CSS selector '{sel}': ${price}")
                        break
                    # Also try the 'content' attribute (for meta tags / itemprop)
                    attr = await el.get_attribute("content")
                    if attr:
                        p = extract_price_from_text(attr)
                        if p:
                            price = p
                            print(f"  💰 CSS attr '{sel}': ${price}")
                            break
            except:
                continue

        # ── STEP 2: JSON-LD structured data ──────────────────────────────────
        if not price:
            try:
                jld_blocks = await page.eval_on_selector_all(
                    'script[type="application/ld+json"]',
                    "els => els.map(e => e.textContent)"
                )
                for block in jld_blocks:
                    try:
                        obj = json.loads(block)
                        # Handle array of objects
                        if isinstance(obj, list):
                            obj = obj[0]
                        # Direct price field
                        offers = obj.get("offers", obj)
                        if isinstance(offers, list):
                            offers = offers[0]
                        raw = offers.get("price") or obj.get("price")
                        if raw:
                            p = extract_price_from_text(str(raw))
                            if p:
                                price = p
                                print(f"  💰 JSON-LD: ${price}")
                                break
                    except:
                        continue
            except:
                pass

        # ── STEP 3: Open Graph / meta tags ───────────────────────────────────
        if not price:
            try:
                og = await page.eval_on_selector_all(
                    'meta[property="product:price:amount"], meta[name="price"]',
                    "els => els.map(e => e.getAttribute('content'))"
                )
                for val in og:
                    if val:
                        p = extract_price_from_text(val)
                        if p:
                            price = p
                            print(f"  💰 Meta tag: ${price}")
                            break
            except:
                pass

        # ── STEP 4: Regex scan on page source ────────────────────────────────
        if not price:
            content = await page.content()
            # Look for "price": 49.99 patterns in scripts
            for pattern in [
                r'"price"\s*:\s*"?(\d+\.?\d*)"?',
                r'"salePrice"\s*:\s*"?(\d+\.?\d*)"?',
                r'"currentPrice"\s*:\s*"?(\d+\.?\d*)"?',
                r'\$\s*(\d{1,4}\.\d{2})',
            ]:
                matches = re.findall(pattern, content)
                for m in matches:
                    try:
                        val = float(m)
                        if 2 < val < 10000:
                            price = val
                            print(f"  💰 Regex fallback: ${price}")
                            break
                    except:
                        continue
                if price:
                    break

        # ── Stock status ──────────────────────────────────────────────────────
        content = await page.content()
        in_stock = not any(phrase in content.lower() for phrase in OOS_PHRASES)

        if price:
            print(f"  ✅ ${price} | {'In stock' if in_stock else 'OUT OF STOCK'}")
        else:
            print(f"  ❌ Price not found — dumping price-related HTML for debugging:")
            try:
                # Dump any element that contains a $ sign and looks like a price
                snippets = await page.eval_on_selector_all("*", """els => els
                    .filter(e => e.children.length === 0 && /\$\s*\d+/.test(e.textContent))
                    .slice(0, 15)
                    .map(e => e.tagName + ' class=' + e.className + ' => ' + e.textContent.trim().slice(0,80))
                """)
                for s in snippets:
                    print(f"    📄 {s}")
            except Exception as de:
                print(f"    ⚠️ Debug dump failed: {de}")

        return price, in_stock

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None, None

async def scrape_rfd(page, product_name):
    """Check RedFlagDeals for a deal on this product."""
    print(f"  🔍 RFD: {product_name}")
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
        body = await page.inner_text("body")
        prices = re.findall(r'\$\s*(\d{2,4}\.\d{2})', body)
        if not prices:
            return None
        price = float(prices[0])
        print(f"  🔥 Deal: ${price} — {thread_title[:60]}")
        return {
            "found": True, "price": price,
            "title": thread_title[:120], "url": thread_url,
            "found_at": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        print(f"  ❌ RFD error: {e}")
        return None

async def main():
    data = load_prices()
    products = data.get("products", [])
    if not products:
        print("⚠️  No products configured.")
        return

    proxy_config = None
    if USE_PROXY:
        proxy_config = {
            "server": f"http://{PROXY_HOST}:{PROXY_PORT}",
            "username": PROXY_USERNAME,
            "password": PROXY_PASSWORD,
        }
        print(f"🔒 Proxy: {PROXY_HOST}:{PROXY_PORT}")
    else:
        print("⚠️  No proxy — some sites may block")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
            ]
        )

        for product in products:
            print(f"\n📦 {product['name']}")
            site_data = product.setdefault("site_data", {})

            for site_key, site_info in site_data.items():
                url = site_info.get("url")
                if not url:
                    print(f"  ⏭️  No URL for {site_key}")
                    continue

                # Auto-detect site from URL if key is "unknown"
                detected = detect_site(url)
                effective_site = detected if detected != "unknown" else site_key

                context = await browser.new_context(
                    proxy=proxy_config,
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 1920, "height": 1080},
                    locale="en-CA",
                    timezone_id="America/Toronto",
                    extra_http_headers={
                        "Accept-Language": "en-CA,en;q=0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"',
                    }
                )
                page = await context.new_page()
                # Mask automation flags
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    window.chrome = {runtime: {}};
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                """)

                price, in_stock = await scrape_price(page, url, effective_site)

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
                await asyncio.sleep(random.randint(3, 6))

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
