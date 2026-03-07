import json
import asyncio
import random
from datetime import datetime, timezone
from playwright.async_api import async_playwright

PRODUCTS = [
    {
        "id": "4000077852",
        "name": "ON Gold Standard Whey 2.56kg",
        "short": "ON Whey Chocolate",
        "url": "https://www.costco.ca/optimum-nutrition-gold-standard-100-whey-protein-extreme-chocolate-shake-256-kg.product.4000077852.html"
    },
    {
        "id": "100417020",
        "name": "Kirkland Signature Protein Bars 20-count",
        "short": "KS Protein Bars",
        "url": "https://www.costco.ca/kirkland-signature-protein-bars-20-count.product.100417020.html"
    }
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

async def scrape_product(page, product):
    print(f"Scraping: {product['name']}")
    try:
        # Visit homepage first to get cookies (mimics real user)
        print("  → Visiting homepage first...")
        await page.goto("https://www.costco.ca", timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(2000, 4000))

        # Now visit product page
        print(f"  → Navigating to product...")
        await page.goto(product["url"], timeout=40000, wait_until="domcontentloaded")
        await page.wait_for_timeout(random.randint(3000, 5000))

        # Simulate human scroll
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(1000)

        price = None
        import re

        # Try multiple price selectors
        selectors = [
            ".your-price .value",
            "[automation-id='itemPrice']",
            ".price-current",
            ".product-price",
            'span[itemprop="price"]',
            ".costcoPrice",
            ".product-price-container",
            "[class*='your-price']",
            "[class*='Price']",
        ]

        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = (await el.inner_text()).strip()
                    if "$" in text or re.search(r'\d+\.\d{2}', text):
                        price = text.split("\n")[0].strip()
                        print(f"  → Found price via selector '{sel}': {price}")
                        break
            except:
                continue

        # Fallback: scan full page HTML for price patterns
        if not price:
            content = await page.content()
            # Look for JSON-LD structured data first (most reliable)
            json_match = re.search(r'"price"\s*:\s*"?(\d+\.?\d*)"?', content)
            if json_match:
                price = f"${json_match.group(1)}"
                print(f"  → Found price via JSON-LD: {price}")
            else:
                # Last resort: regex on page content
                match = re.search(r'\$\s*(\d{2,3}\.\d{2})', content)
                if match:
                    price = f"${match.group(1)}"
                    print(f"  → Found price via regex: {price}")

        # Check stock status
        content = await page.content()
        in_stock = not any(phrase in content.lower() for phrase in [
            "out of stock", "sold out", "unavailable", "not available"
        ])

        return {
            **product,
            "price": price or "N/A",
            "in_stock": in_stock,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "error": None
        }

    except Exception as e:
        print(f"  ❌ Error: {e}")
        return {
            **product,
            "price": "Error",
            "in_stock": None,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "error": str(e)
        }

async def main():
    results = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-http2",           # Force HTTP/1.1 — fixes ERR_HTTP2_PROTOCOL_ERROR
                "--disable-web-security",
                "--lang=en-CA",
            ]
        )

        for product in PRODUCTS:
            # Fresh context per product (new cookies, new fingerprint)
            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": random.randint(1280, 1920), "height": random.randint(768, 1080)},
                locale="en-CA",
                timezone_id="America/Toronto",
                extra_http_headers={
                    "Accept-Language": "en-CA,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Upgrade-Insecure-Requests": "1",
                }
            )

            # Hide webdriver fingerprint
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                Object.defineProperty(navigator, 'languages', { get: () => ['en-CA', 'en'] });
                window.chrome = { runtime: {} };
            """)

            result = await scrape_product(page, product)
            results.append(result)
            print(f"  ✅ {result['name']}: {result['price']}")

            await context.close()
            await asyncio.sleep(random.randint(3, 6))  # Delay between products

        await browser.close()

    output = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "products": results
    }

    with open("prices.json", "w") as f:
        json.dump(output, f, indent=2)

    print("\n✅ Saved to prices.json")
    print(json.dumps(output, indent=2))

asyncio.run(main())
