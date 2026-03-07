# Costco Canada Price Tracker

Automatically scrapes Costco Canada product prices every 3 hours using GitHub Actions and stores them in `prices.json`.

## Setup (One-time, ~5 minutes)

### 1. Create a GitHub account
Go to https://github.com/signup and create a free account.

### 2. Create a new repository
- Go to https://github.com/new
- Name it: `costco-tracker`
- Set it to **Public** (required for free Actions minutes + raw file access)
- Click **Create repository**

### 3. Upload these files
Upload all files from this zip into your new repository:
- `scrape.py`
- `prices.json`
- `.github/workflows/scrape.yml`

### 4. Enable GitHub Actions
- Go to your repo → **Actions** tab
- Click **"I understand my workflows, go ahead and enable them"**

### 5. Run it manually first
- Go to **Actions** → **Costco Price Tracker** → **Run workflow**
- This confirms everything works

### 6. Get your raw JSON URL
Your prices will be publicly available at:
```
https://raw.githubusercontent.com/YOUR_USERNAME/costco-tracker/main/prices.json
```
Replace `YOUR_USERNAME` with your GitHub username.

### 7. Share URL with Claude
Paste that URL into Claude chat — the tracker artifact will read live prices from it every time you open the chat!

## How it works
- GitHub Actions runs `scrape.py` every 3 hours
- Playwright launches a real Chromium browser and visits each Costco CA product page
- Prices are saved to `prices.json` and committed back to the repo
- The Claude artifact fetches the raw JSON and displays your watchlist
