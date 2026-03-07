import json
import asyncio
import re
import os
import random
import requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright

# ── Proxy config ──────────────────────────────────────────────────────────────
PROXY_HOST     = os.environ.get("PROXY_HOST", "")
PROXY_PORT     = os.environ.get("PROXY_PORT", "")
PROXY_USERNAME = os.environ.get("PROXY_USERNAME", "")
PROXY_PASSWORD = os.environ.get("PROXY_PASSWORD", "")
USE_PROXY      = all([PROXY_HOST, PROXY_PORT, PROXY_USERNAME, PROXY_PASSWORD])
PROXIES        = {"http": f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}",
                  "https": f"http://{PROXY_USERNAME}:{PROXY_PASSWORD}@{PROXY_HOST}:{PROXY_PORT}"} if USE_PROXY else {}

PRICES_FILE = "prices.json"

PRICE_SELECTORS = {
    "amazon":       [".a-price .a-offscreen", "#corePriceDisplay_desktop_feature_div .a-offscreen", "#price_inside_buybox", "#priceblock_ourprice", "#priceblock_dealprice"],
    "walmart":      ["[itemprop='price']", "[data-automation='buybox-price']", ".price-characteristic", ".prod-PriceHero [class*='price']"],
    "bestbuy":      ["[data-automation='product-price']", ".priceUpdate", ".price_FHDfG", ".productPrice"],
    "canadiantire": ["[data-testid='product-price']", ".price-display__content", ".price__reg", "[class*='price__value']"],
    "staples":      [".price-box .price", "[data-price]", ".our-price", "[itemprop='price']"],
    "thesource":    ["[data-testid='product-price']", ".price-display", ".productPrice", "[itemprop='price']"],
    "londondrugs":  [".price", ".product-price", "[itemprop='price']"],
    "sportchek":    ["[data-testid='price']", ".price-label", ".product-price", "[itemprop='price']"],
    "homedepot":    ["[data-testid='product-price']", ".price-format__main-price", "#standard-price", "[itemprop='price']"],
}

SITE_WAIT = {
    "amazon": 3000, "walmart": 3000, "bestbuy": 4000,
    "canadiantire": 3000, "staples": 2000, "thesource": 3000,
    "londondrugs": 2000, "sportchek": 3000, "homedepot": 3000,
}

OOS_PHRASES = ["out of stock", "sold out", "unavailable", "currently unavailable", "out of stock online", "temporarily out of stock"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

def detect_site(url):
    mapping = {
        "costco.ca": "costco", "amazon.ca": "amazon", "walmart.ca": "walmart",
        "bestbuy.ca": "bestbuy", "canadiantire.ca": "canadiantire", "staples.ca": "staples",
        "thesource.ca": "thesource", "londondrugs.ca": "londondrugs",
        "sportchek.ca": "sportchek", "homedepot.ca": "homedepot",
    }
    for domain, key in mapping.items():
        if domain in url:
            return key
    return "unknown"

def extract_product_id(url):
    """Extract Costco product ID from URL."""
    match = re.search(r'\.product\.(\d+)\.html', url)
    return match.group(1) if match else None

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
    text = str(text).replace(",", "").strip()
    match = re.search(r'\d[\d]*\.\d{2}', text)
    if match:
        val = float(match.group(0))
        if 1 < val < 50000:
            return val
    return None

# ── COSTCO: Direct API (no browser needed) ───────────────────────────────────
def scrape_costco_api(url):
    """Call Costco's internal product API directly — bypasses Akamai entirely."""
    product_id = extract_product_id(url)
    if not product_id:
        print(f"  ❌ Could not extract Costco product ID from URL")
        return None, None

    print(f"  🔑 Costco API: product ID {product_id}")

    api_url = f"https://www.costco.ca/AjaxGetProductDetailView?productId={product_id}"
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-CA,en;q=0.9",
        "Referer": url,
        "X-Requested-With": "XMLHttpRequest",
    }

    try:
        resp = requests.get(api_url, headers=headers, proxies=PROXIES, timeout=20)
        print(f"  📡 API status: {resp.status_code}")
        if resp.status_code != 200:
            raise Exception(f"API returned {resp.status_code}")

        data = resp.json()

        # Navigate the response structure
        price = None
        in_stock = True

        # Try common price paths in the response
        for path in [
            ["productDetailVO", "finalPrice"],
            ["productDetailVO", "yourPrice", "price"],
            ["productDetailVO", "salePrice"],
            ["productDetailVO", "price"],
            ["finalPrice"],
            ["yourPrice", "price"],
        ]:
            try:
                val = data
                for key in path:
                    val = val[key]
                if val:
                    price = extract_price_from_text(str(val))
                    if price:
                        print(f"  💰 Costco API [{'.'.join(path)}]: ${price}")
                        break
            except (KeyError, TypeError):
                continue

        # Stock status
        try:
            availability = str(data.get("productDetailVO", {}).get("availability", "")).lower()
            in_stock = "out" not in availability and "unavailable" not in availability
        except:
            in_stock = True

        if not price:
            # Dump top-level keys for debugging
            print(f"  ⚠️  Price not found in API. Top keys: {list(data.keys())[:10]}")

        return price, in_stock

    except Exception as e:
        print(f"  ❌ Costco API error: {e}")
        # Fall back to regex on raw response text
        try:
            matches = re.findall(r'"(?:finalPrice|yourPrice|salePrice|price)"\s*:\s*"?(\d+\.?\d*)"?', resp.text)
            for m in matches:
                val = float(m)
                if 2 < val < 5000:
                    print(f"  💰 Costco API regex fallback: ${val}")
                    return val, True
        except:
            pass
        return None, None

# ── BROWSER: All other sites ──────────────────────────────────────────────────
async def scrape_price_browser(page, url, site):
    """Scrape price using headless browser for non-Costco sites."""
    print(f"  🌐 [{site}] {url[:70]}...")
    try:
        await page.goto(url, timeout=40000, wait_until="domcontentloaded")
        extra_wait = SITE_WAIT.get(site, 3000) + random.randint(500, 1500)
        await page.wait_for_timeout(extra_wait)
        await page.evaluate("window.scrollBy(0, 500)")
        await page.wait_for_timeout(1000)

        price = None

        # Step 1: CSS selectors
        for sel in PRICE_SELECTORS.get(site, []):
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    p = extract_price_from_text(text)
                    if p:
                        price = p
                        print(f"  💰 CSS '{sel}': ${price}")
                        break
                    attr = await el.get_attribute("content")
                    if attr:
                        p = extract_price_from_text(attr)
                        if p:
                            price = p
                            print(f"  💰 CSS attr '{sel}': ${price}")
                            break
            except:
                continue

        # Step 2: JSON-LD
        if not price:
            try:
                blocks = await page.eval_on_selector_all('script[type="application/ld+json"]', "els => els.map(e => e.textContent)")
                for block in blocks:
                    try:
                        obj = json.loads(block)
                        if isinstance(obj, list): obj = obj[0]
                        offers = obj.get("offers", obj)
                        if isinstance(offers, list): offers = offers[0]
                        raw = offers.get("price") or obj.get("price")
                        if raw:
                            p = extract_price_from_text(str(raw))
                            if p:
                                price = p
                                print(f"  💰 JSON-LD: ${price}")
                                break
                    except: continue
            except: pass

        # Step 3: Meta tags
        if not price:
            try:
                og = await page.eval_on_selector_all('meta[property="product:price:amount"], meta[name="price"]', "els => els.map(e => e.getAttribute('content'))")
                for val in og:
                    if val:
                        p = extract_price_from_text(val)
                        if p:
                            price = p
                            print(f"  💰 Meta: ${price}")
                            break
            except: pass

        # Step 4: Regex scan
        if not price:
            content = await page.content()
            for pattern in [r'"price"\s*:\s*"?(\d+\.?\d*)"?', r'"salePrice"\s*:\s*"?(\d+\.?\d*)"?', r'\$\s*(\d{1,4}\.\d{2})']:
                for m in re.findall(pattern, content):
                    try:
                        val = float(m)
                        if 2 < val < 10000:
                            price = val
                            print(f"  💰 Regex: ${price}")
                            break
                    except: continue
                if price: break

        # Step 5: Debug dump if still nothing
        if not price:
            print(f"  ❌ Price not found — page elements with $:")
            try:
                snippets = await page.eval_on_selector_all("*", """els => els
                    .filter(e => e.children.length===0 && /\\$\\s*\\d+/.test(e.textContent))
                    .slice(0,10)
                    .map(e => e.tagName+' class='+e.className+' => '+e.textContent.trim().slice(0,60))
                """)
                for s in snippets:
                    print(f"    📄 {s}")
            except: pass

        content = await page.content()
        in_stock = not any(p in content.lower() for p in OOS_PHRASES)

        if price:
            print(f"  ✅ ${price} | {'In stock' if in_stock else 'OUT OF STOCK'}")

        return price, in_stock

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None, None

async def scrape_rfd(page, product_name):
    print(f"  🔍 RFD: {product_name}")
    try:
        query = product_name.replace(" ", "+")
        await page.goto(f"https://forums.redflagdeals.com/search/?q={query}&forums=9", timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        first = page.locator(".thread_title a, h3 a").first
        if await first.count() == 0: return None
        thread_url = await first.get_attribute("href")
        thread_title = (await first.inner_text()).strip()
        if not thread_url.startswith("http"):
            thread_url = "https://forums.redflagdeals.com" + thread_url
        await page.goto(thread_url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        body = await page.inner_text("body")
        prices = re.findall(r'\$\s*(\d{2,4}\.\d{2})', body)
        if not prices: return None
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
        print("⚠️  No products configured.")
        return

    proxy_config = None
    if USE_PROXY:
        proxy_config = {"server": f"http://{PROXY_HOST}:{PROXY_PORT}", "username": PROXY_USERNAME, "password": PROXY_PASSWORD}
        print(f"🔒 Proxy: {PROXY_HOST}:{PROXY_PORT}")
    else:
        print("⚠️  No proxy configured")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled", "--window-size=1920,1080"]
        )

        for product in products:
            print(f"\n📦 {product['name']}")
            site_data = product.setdefault("site_data", {})

            for site_key, site_info in site_data.items():
                url = site_info.get("url")
                if url and not url.startswith("http"):
                    url = "https://" + url
                    site_info["url"] = url
                if not url:
                    print(f"  ⏭️  No URL for {site_key}")
                    continue

                detected = detect_site(url)
                effective_site = detected if detected != "unknown" else site_key

                # ── Costco: use API directly, no browser ──────────────────
                if effective_site == "costco":
                    print(f"  🏪 Costco — using direct API (no browser)")
                    price, in_stock = scrape_costco_api(url)
                else:
                    # ── All other sites: use browser ──────────────────────
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
                    await page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        window.chrome = {runtime: {}};
                        Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                    """)
                    price, in_stock = await scrape_price_browser(page, url, effective_site)
                    await context.close()

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
