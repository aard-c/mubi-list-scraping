from playwright.sync_api import sync_playwright
from urllib.parse import quote

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    for pg in context.pages:
        if "letterboxd.com" in pg.url:
            page = pg
            break

    page.goto(f"https://letterboxd.com/search/{quote('Taxi Driver')}/", wait_until="networkidle")
    import time; time.sleep(2)

    results = page.locator("ul.results li")
    print(f"Results count: {results.count()}")
    
    for i in range(min(results.count(), 5)):
        item = results.nth(i)
        link = item.locator("a[href*='/film/']").first
        if link.count() == 0:
            print(f"  [{i}] no film link")
            continue
        href = link.get_attribute("href")
        title_attr1 = link.get_attribute("data-original-title")
        title_attr2 = link.get_attribute("data-film-name")
        title_attr3 = link.get_attribute("title")
        inner = link.inner_text().strip()
        meta = item.inner_text().strip()[:100]
        print(f"  [{i}] href={href}")
        print(f"       data-original-title={title_attr1}")
        print(f"       data-film-name={title_attr2}")
        print(f"       title={title_attr3}")
        print(f"       inner_text={inner}")
        print(f"       item_text={meta}")