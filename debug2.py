from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    for page in context.pages:
        if "letterboxd.com" in page.url:
            print("URL:", page.url)
            print("Title:", page.title())
            # Print all links in the header area
            links = page.locator("header a").all()
            for link in links:
                print("  link:", link.get_attribute("href"), "|", link.inner_text().strip()[:30])