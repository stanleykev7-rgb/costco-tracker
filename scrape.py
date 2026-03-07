import json
import asyncio
import re
import os
import random
from datetime import datetime, timezone
from playwright.async_api import async_playwright

PRICES_FILE = "prices.json"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121 Safari/537",
]

PRICE_SELECTORS = {
    "costco": [
        ".product-price",
        "[automation-id='productPriceOutput']",
        ".price"
    ],
    "amazon": [
        ".a-price-whole",
        ".a-offscreen",
        "#priceblock_ourprice",
        "#price_inside_buybox"
    ],
    "walmart": [
        "[itemprop='price']",
        ".price-characteristic"
    ],
    "bestbuy": [
        ".priceUpdate",
        "[data-automation='product-price']"
    ]
}


def load_prices():
    try:
        with open(PRICES_FILE) as f:
            return json.load(f)
    except:
        return {"last_updated": None, "products": []}


def save_prices(data):
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Saved prices.json")


async def scrape_price(page, url, site):

    try:
        await page.goto(url, timeout=30000, wait_until="domcontentloaded")

        await page.wait_for_timeout(random.randint(2000, 3500))

        price = None

        for selector in PRICE_SELECTORS.get(site, []):

            try:
                el = page.locator(selector).first

                if await el.count() > 0:

                    text = (await el.inner_text()).strip()

                    match = re.search(r'(\d{1,4}(?:\.\d{2})?)', text)

                    if match:
                        price = float(match.group(1))
                        break

            except:
                pass

        if not price:
            content = await page.content()

            match = re.search(r'\$\s*(\d+(?:\.\d{2})?)', content)

            if match:
                price = float(match.group(1))

        content = await page.content()

        in_stock = not any(
            p in content.lower()
            for p in [
                "out of stock",
                "sold out",
                "unavailable",
                "temporarily unavailable"
            ]
        )

        return price, in_stock

    except Exception as e:
        print("Scrape error:", e)
        return None, None


async def main():

    data = load_prices()

    products = data.get("products", [])

    if not products:
        print("No products configured")
        return

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox"]
        )

        for product in products:

            print("Processing:", product["name"])

            sites = product.get("sites_to_track", [])

            for site in sites:

                site_data = product.setdefault("site_data", {}).setdefault(site, {})

                url = site_data.get("url")

                if not url:
                    site_data["error"] = "No URL configured"
                    continue

                context = await browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 1280, "height": 800}
                )

                page = await context.new_page()

                price, in_stock = await scrape_price(page, url, site)

                history = site_data.get("history", [])

                if price:

                    today = datetime.now(timezone.utc).strftime("%b %d")

                    if history and history[-1]["d"] == today:
                        history[-1]["p"] = price
                    else:
                        history.append({"d": today, "p": price})

                    history = history[-60:]

                was = site_data.get("price")

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

        await browser.close()

    data["last_updated"] = datetime.now(timezone.utc).isoformat()

    save_prices(data)


asyncio.run(main())
