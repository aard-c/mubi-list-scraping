from playwright.sync_api import sync_playwright
import time

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    context = browser.contexts[0]
    for pg in context.pages:
        if "letterboxd.com" in pg.url:
            page = pg
            break

    # Go to a film page
    page.goto("https://letterboxd.com/film/taxi-driver/")
    time.sleep(2)

    # Print all buttons on the page
    print("=== BUTTONS ===")
    for btn in page.locator("button").all():
        print(f"  button: text='{btn.inner_text().strip()[:50]}' class='{btn.get_attribute('class')}'")

    print("\n=== Links with 'list' ===")
    for a in page.locator("a").all():
        txt = a.inner_text().strip()
        href = a.get_attribute("href") or ""
        if "list" in txt.lower() or "list" in href.lower():
            print(f"  a: text='{txt[:50]}' href='{href}'")
            
    # Click "Add to lists" and see what's in the modal
    page.locator("button:has-text('Add to lists')").first.click()
    time.sleep(1)
    print("\n=== MODAL BUTTONS after clicking Add to lists ===")
    for btn in page.locator(".modal button, [role='dialog'] button").all():
        print(f"  button: text='{btn.inner_text().strip()[:50]}' class='{btn.get_attribute('class')}'")
    print("\n=== MODAL LINKS ===")
    for a in page.locator(".modal a, [role='dialog'] a").all():
        print(f"  a: text='{a.inner_text().strip()[:50]}' href='{a.get_attribute('href')}'")
        
    # First click the menu button (the arrow next to LOG)
    page.goto("https://letterboxd.com/film/taxi-driver/")
    time.sleep(2)
    
    menu_btn = page.locator("button.button-add-menu").first
    print("menu button found:", menu_btn.count())
    menu_btn.click()
    time.sleep(1)
    
    print("\n=== AFTER clicking menu button ===")
    for btn in page.locator("button").all():
        txt = btn.inner_text().strip()
        if txt:
            print(f"  button: '{txt}' class='{btn.get_attribute('class')}'")
    
    # Now click Add to lists
    add_to_lists = page.locator("button:has-text('Add to lists')").first
    print("\nAdd to lists button found:", add_to_lists.count())
    add_to_lists.click()
    time.sleep(1)
    
    print("\n=== MODAL after Add to lists ===")
    # Print all visible text in any open overlay/modal
    for el in page.locator(".list-modal, .modal.-list, [data-type='list'], .filmlist-modal").all():
        print("modal element:", el.inner_text()[:200])
    
    # Check all inputs
    for inp in page.locator("input").all():
        print(f"input: type={inp.get_attribute('type')} name={inp.get_attribute('name')} placeholder={inp.get_attribute('placeholder')}")
        
    # Find the lists modal container
    time.sleep(1)
    print("\n=== LOOKING FOR LISTS MODAL ===")
    # Find element containing 'Type to search' input
    search_input = page.locator("input[placeholder='Type to search']").first
    print("Type to search input found:", search_input.count())
    if search_input.count() > 0:
        # Get parent containers
        parent = page.locator("input[placeholder='Type to search']").locator("xpath=ancestor::*[contains(@class,'modal') or contains(@class,'overlay') or contains(@class,'list')][1]")
        print("Parent class:", parent.first.get_attribute("class"))
        print("Parent tag:", parent.first.evaluate("el => el.tagName"))
        print("Parent content preview:", parent.first.inner_text()[:300])
        
        # Find new list button near this input
        form = page.locator("input[placeholder='Type to search']").locator("xpath=ancestor::form[1]")
        print("\nForm found:", form.count())
        if form.count():
            for a in form.locator("a, button").all():
                print(f"  element: '{a.inner_text().strip()[:50]}'")