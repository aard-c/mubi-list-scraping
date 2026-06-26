from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    for pg in context.pages:
        if "letterboxd.com" in pg.url:
            page = pg
            break

    from urllib.parse import quote
    page.goto(f"https://letterboxd.com/search/{quote('Taxi Driver')}/")
    import time; time.sleep(2)
    
    print("URL:", page.url)
    print("Title:", page.title())
    
    # Try different selectors
    print("ul.results li count:", page.locator("ul.results li").count())
    print(".results li count:", page.locator(".results li").count())
    print("a[href*='/film/'] count:", page.locator("a[href*='/film/']").count())
    
    # Print first few film links
    links = page.locator("a[href*='/film/']").all()[:5]
    for l in links:
        print("  film link:", l.get_attribute("href"), "|", l.inner_text().strip()[:40])