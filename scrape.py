import json
import asyncio
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

async def scrape_product(page, product):
    print(f"Scraping: {product['name']}")
    try:
        await page.goto(product["url"], timeout=30000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Try multiple price selectors Costco uses
        price = None
        selectors = [
            ".your-price .value",
            "[automation-id='itemPrice']",
            ".price-current",
            ".product-price",
            'span[itemprop="price"]',
            ".costcoPrice",
            "[class*='price']"
        ]

        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    text = await el.inner_text()
                    text = text.strip()
                    if "$" in text or any(c.isdigit() for c in text):
                        price = text
                        break
            except:
                continue

        # Fallback: search full page text for price pattern
        if not price:
            content = await page.content()
            import re
            match = re.search(r'\$\s*(\d+\.\d{2})', content)
            if match:
                price = f"${match.group(1)}"

        in_stock = True
        try:
            content = await page.content()
            if "out of stock" in content.lower() or "sold out" in content.lower():
                in_stock = False
        except:
            pass

        return {
            **product,
            "price": price or "N/A",
            "in_stock": in_stock,
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "error": None
        }

    except Exception as e:
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
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-CA",
            extra_http_headers={
                "Accept-Language": "en-CA,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            }
        )
        page = await context.new_page()

        for product in PRODUCTS:
            result = await scrape_product(page, product)
            results.append(result)
            print(f"  → {result['name']}: {result['price']}")
            await page.wait_for_timeout(2000)

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
