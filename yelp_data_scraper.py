"""Yelp business scraper — run locally on your PC.

Setup:
    pip install playwright
    playwright install chromium

Usage:
    python yelp_scraper.py
"""

from __future__ import annotations

import asyncio
import csv
import json
import random
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, Page


# ============================================================================
# CONFIG — edit these
# ============================================================================

START_URLS = [
    'https://www.yelp.com/biz/wall-the-partition-temporary-wall-nyc-pressurized-wall-nyc-woodside',
    'https://www.yelp.com/biz/lady-liberty-contracting-brooklyn',
    'https://www.yelp.com/biz/superior-modern-designs-new-york',
]

OUTPUT_JSON = 'yelp_results.json'
OUTPUT_CSV = 'yelp_results.csv'
DEBUG_DIR = Path('debug_screenshots')

HEADLESS = False           # False = you SEE the browser. Helps when debugging.
SLOW_MO_MS = 50            # Small delay between Playwright actions, looks more human.
PAGE_TIMEOUT_MS = 60000    # 60s for the page to load.
DELAY_BETWEEN_PAGES = (3, 7)  # Random seconds between pages (min, max).


# ============================================================================
# EXTRACTION
# ============================================================================

async def extract_business_info(page: Page) -> dict:
    """Pick out name, category, address, phone, website from a Yelp biz page.

    Each field is identified by the icon's aria-label, so missing fields just
    stay None instead of breaking everything.
    """
    info = {
        'Name': None,
        'Category': None,
        'Address': None,
        'PhoneNumber': None,
        'Website': None,
    }

    name_el = await page.query_selector('h1')
    if name_el:
        info['Name'] = (await name_el.inner_text()).strip()

    category_el = await page.query_selector('.y-css-1faz0b6')
    if category_el:
        info['Category'] = (await category_el.inner_text()).strip()

    blocks = await page.query_selector_all('div.y-css-sgplyb')

    for block in blocks:
        icon = await block.query_selector('div.y-css-19if2tu span[aria-label]')
        if not icon:
            continue

        aria_label = (await icon.get_attribute('aria-label')) or ''
        value_container = await block.query_selector('div.y-css-1642f8b')
        if not value_container:
            continue

        if aria_label == 'Business website':
            link = await value_container.query_selector('a')
            if link:
                info['Website'] = (await link.inner_text()).strip()
                # For the full URL instead of just the domain, uncomment:
                # href = await link.get_attribute('href') or ''
                # qs = parse_qs(urlparse(href).query)
                # if qs.get('url'):
                #     info['Website'] = qs['url'][0]

        elif aria_label == 'Business phone number':
            phone_el = await value_container.query_selector('p.y-css-1baza3a')
            if phone_el:
                info['PhoneNumber'] = (await phone_el.inner_text()).strip()

        elif aria_label == 'Directions to the business':
            address_el = await value_container.query_selector('p.y-css-tzxvzr')
            if address_el:
                info['Address'] = (await address_el.inner_text()).strip()

    return info


# ============================================================================
# SCRAPE LOOP
# ============================================================================

async def scrape_url(page: Page, url: str) -> dict:
    """Visit one URL and return a result dict (always returns something)."""
    result = {
        'url': url,
        'title': None,
        'Name': None,
        'Category': None,
        'Address': None,
        'PhoneNumber': None,
        'Website': None,
        'status': 'unknown',
    }

    try:
        print(f'  → loading...')
        await page.goto(url, wait_until='domcontentloaded', timeout=PAGE_TIMEOUT_MS)

        # Wait for the info section to actually render.
        try:
            await page.wait_for_selector('div.y-css-sgplyb', timeout=20000)
        except Exception:
            print('  ! info blocks never appeared — saving screenshot')
            DEBUG_DIR.mkdir(exist_ok=True)
            slug = url.split('/biz/')[-1][:50].replace('/', '_').replace('?', '_')
            await page.screenshot(
                path=str(DEBUG_DIR / f'{slug}.png'),
                full_page=True,
            )
            result['status'] = 'no_info_blocks'

        result['title'] = await page.title()
        business = await extract_business_info(page)
        result.update(business)

        if result['Name']:
            result['status'] = 'ok'
        else:
            result['status'] = result['status'] if result['status'] != 'unknown' else 'empty'

    except Exception as e:
        print(f'  ✗ error: {e}')
        result['status'] = f'error: {type(e).__name__}'

    return result


async def main() -> None:
    results: list[dict] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=HEADLESS,
            slow_mo=SLOW_MO_MS,
            args=['--disable-blink-features=AutomationControlled'],
        )
        context = await browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/131.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1920, 'height': 1080},
            locale='en-US',
        )

        # Hide the navigator.webdriver flag (bot detection looks at this).
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        page = await context.new_page()

        for i, url in enumerate(START_URLS, start=1):
            print(f'[{i}/{len(START_URLS)}] {url}')
            result = await scrape_url(page, url)
            results.append(result)
            print(f'  ← {result["status"]}: {result.get("Name") or "—"}')

            # Random delay between pages to look human.
            if i < len(START_URLS):
                delay = random.uniform(*DELAY_BETWEEN_PAGES)
                print(f'  …waiting {delay:.1f}s')
                await asyncio.sleep(delay)

        await context.close()
        await browser.close()

    # Save to JSON
    Path(OUTPUT_JSON).write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f'\nSaved {len(results)} records to {OUTPUT_JSON}')

    # Save to CSV
    if results:
        fieldnames = list(results[0].keys())
        with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)
        print(f'Saved {len(results)} records to {OUTPUT_CSV}')

    # Quick summary
    ok = sum(1 for r in results if r['status'] == 'ok')
    print(f'\nSummary: {ok}/{len(results)} successful')


if __name__ == '__main__':
    asyncio.run(main())