import json
import asyncio
import re
import os
import requests
from datetime import datetime, timezone
from playwright.async_api import async_playwright

PRICES_FILE = "prices.json"
GITHUB_TOKEN = os.environ.get("GH_PAT", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPOSITORY", "")  # e.g. stanleykev7-rgb/costco-tracker

def load_prices():
    try:
        with open(PRICES_FILE) as f:
            return json.load(f)
    except:
        return {"products": []}

def trigger_scraper():
    """Trigger the price scraper workflow via GitHub API."""
    if not GITHUB_TOKEN or not GITHUB_REPO:
        print("⚠️  Cannot trigger scraper — GH_PAT or GITHUB_REPOSITORY not set")
        return
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/scrape.yml/dispatches"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    resp = requests.post(url, json={"ref": "main"}, headers=headers)
    if resp.status_code == 204:
        print("🚀 Triggered price scraper workflow!")
    else:
        print(f"❌ Failed to trigger scraper: {resp.status_code} {resp.text}")

async def check_rfd(product_name, page):
    print(f"  🔍 RFD check: {product_name}")
    try:
        query = product_name.replace(" ", "+")
        url = f"https://forums.redflagdeals.com/search/?q={query}&forums=9"
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)

        first = page.locator(".thread_title a, h3 a").first
        if await first.count() == 0:
            print(f"  — No thread found")
            return None

        thread_url = await first.get_attribute("href")
        thread_title = (await first.inner_text()).strip()
        if not thread_url.startswith("http"):
            thread_url = "https://forums.redflagdeals.com" + thread_url

        # Check if this is a new/recent deal (not already recorded)
        await page.goto(thread_url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        content = await page.inner_text("body")
        prices = re.findall(r'\$\s*(\d{2,4}\.\d{2})', content)
        if not prices:
            return None

        price = float(prices[0])
        print(f"  🔥 Deal: ${price} — {thread_title[:60]}")
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
        print("⚠️  No products to monitor yet.")
        return

    deal_found = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox","--disable-setuid-sandbox","--disable-dev-shm-usage"])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="en-CA",
        )
        page = await context.new_page()

        for product in products:
            result = await check_rfd(product["name"], page)
            prev_deal = product.get("rfd_deal")

            # Only flag as new deal if URL changed (genuinely new thread)
            if result:
                prev_url = prev_deal.get("url") if prev_deal else None
                if result["url"] != prev_url:
                    print(f"  🆕 NEW deal for {product['name']}!")
                    product["rfd_deal"] = result
                    deal_found = True
                else:
                    print(f"  — Same deal as before, no change")
            else:
                product["rfd_deal"] = None

            await asyncio.sleep(2)

        await browser.close()

    # Save updated deal info
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(PRICES_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("✅ RFD check complete")

    # Trigger full scrape immediately if new deal found
    if deal_found:
        print("🔥 New deal found — triggering immediate price scrape!")
        trigger_scraper()
    else:
        print("— No new deals found")

asyncio.run(main())
