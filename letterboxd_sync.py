#!/usr/bin/env python3
"""
Sync mubifinder_turkey_available.csv to a Letterboxd list.

HOW TO RUN:
1. Kill Chrome and relaunch with remote debugging:
   pkill -x "Google Chrome" && sleep 2
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
     --remote-debugging-port=9222 \
     --user-data-dir="/Users/ardacildan/ChromeDebug" &
2. Log into Letterboxd in that Chrome window.
3. Run: python3 letterboxd_sync.py
"""

import csv
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, urljoin

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()

USERNAME = os.getenv("LETTERBOXD_USERNAME")
LIST_NAME = os.getenv("LETTERBOXD_LIST_NAME", "MUBI all Movies TR")
CSV_FILE = os.getenv("LETTERBOXD_CSV_FILE", "mubifinder_turkey_available.csv")
REQUEST_DELAY = float(os.getenv("LETTERBOXD_DELAY_SECONDS", "1.0"))
CDP_URL = os.getenv("LETTERBOXD_CDP_URL", "http://localhost:9222")

BASE_URL = "https://letterboxd.com"


def normalize_title(value: str) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def titles_match(film_title: str, display_title: str, original_title: str) -> bool:
    film_norm = normalize_title(film_title)
    candidates = [normalize_title(display_title)]
    if original_title:
        candidates.append(normalize_title(original_title))
    candidates = [c for c in candidates if c]
    if not candidates or not film_norm:
        return False
    return any(
        film_norm == candidate or film_norm in candidate or candidate in film_norm
        for candidate in candidates
    )


def load_csv_movies(csv_path: str) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path.resolve()}")

    movies = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            movies.append(
                {
                    "title": row["title"].strip(),
                    "original_title": row.get("original_title", "").strip(),
                    "year": row["year"].strip(),
                }
            )
    print(f"Loaded {len(movies)} movies from {path}")
    return movies


def pause(seconds: float | None = None) -> None:
    time.sleep(seconds if seconds is not None else REQUEST_DELAY)


def check_logged_in_on_page(page) -> bool:
    try:
        return page.locator("a:has-text('Sign Out'), a[href*='sign-out']").count() > 0
    except Exception:
        return False


def navigate_and_check_logged_in(page) -> bool:
    try:
        page.goto(f"{BASE_URL}/", timeout=60_000, wait_until="domcontentloaded")
        pause(1)
        return page.locator("a:has-text('Sign Out'), a[href*='sign-out']").count() > 0
    except Exception:
        return False


def get_username(page) -> str | None:
    if USERNAME:
        return USERNAME.strip()

    page.goto(f"{BASE_URL}/settings/", timeout=60_000, wait_until="domcontentloaded")
    pause(0.3)

    try:
        nav_links = page.locator("header a[href^='/']").all()
        for link in nav_links:
            href = link.get_attribute("href") or ""
            parts = href.strip("/").split("/")
            if len(parts) == 1 and parts[0] and parts[0] not in (
                "films", "lists", "members", "journal", "signin",
                "sign-in", "sign-out", "settings", "search", "pro"
            ):
                return parts[0]
    except Exception:
        pass

    return None


def letterboxd_login(page) -> bool:
    print("\n[1/5] Checking Letterboxd login...")

    if check_logged_in_on_page(page):
        print("  [ok] Already logged in (detected on current page)")
        return True

    print("  Navigating to letterboxd.com to check login status...")
    if navigate_and_check_logged_in(page):
        print("  [ok] Already logged in")
        return True

    print("  [!] Not logged in.")
    print("      Please log into Letterboxd in the Chrome window.")
    print("      Complete any Cloudflare check first, then sign in.")
    print("  Waiting up to 10 minutes for you to log in...")

    for second in range(600):
        if second > 0 and second % 15 == 0:
            print(f"  ...still waiting ({second // 60}m {second % 60}s)")
        pause(1)
        if check_logged_in_on_page(page):
            print("  [ok] Logged in!")
            return True

    print("  [!] Login timed out.")
    return False


def find_list(page, username: str, list_name: str) -> str | None:
    print(f"\n[2/5] Finding list '{list_name}'...")
    page.goto(f"{BASE_URL}/{username}/lists/", timeout=60_000, wait_until="domcontentloaded")
    pause()

    for link in page.locator(f"a[href*='/{username}/list/']").all():
        href = link.get_attribute("href") or ""
        text = link.inner_text().strip()
        if "/edit/" in href:
            continue
        if normalize_title(text) == normalize_title(list_name):
            list_url = urljoin(BASE_URL, href.split("/edit/")[0].rstrip("/") + "/")
            print(f"  [ok] Found existing list: {list_url}")
            return list_url

    print("  [*] List not found yet")
    return None


def open_list_selection_modal(page) -> bool:
    """Click the menu button then 'Add to lists' to open the list-selection panel."""
    # First click the dropdown arrow next to LOG button
    menu_btn = page.locator("button.button-add-menu").first
    if menu_btn.count() > 0:
        try:
            menu_btn.click(timeout=5_000)
            pause(0.5)
        except PlaywrightTimeoutError:
            pass

    # Now click 'Add to lists...'
    add_btn = page.locator("button:has-text('Add to lists')").first
    if add_btn.count() > 0:
        try:
            add_btn.click(timeout=5_000)
            pause(0.8)
            # Confirm the list-selection panel opened
            if page.locator("div.list-selection").count() > 0:
                return True
        except PlaywrightTimeoutError:
            pass

    return False


def create_list(page, film_slug: str, list_name: str) -> bool:
    """Navigate to a film, open the list modal, click 'New list...' and create it."""
    print(f"  [*] Creating list '{list_name}'...")
    page.goto("about:blank")
    pause(0.3)
    page.goto(f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="networkidle")
    pause(1)

    if not open_list_selection_modal(page):
        print("  [!] Could not open list selection modal")
        return False

    # Click 'New list...'
    new_list_link = page.locator("div.list-selection a:has-text('New list'), div.list-selection button:has-text('New list')").first
    if new_list_link.count() == 0:
        print("  [!] Could not find 'New list' link in modal")
        # Close modal
        page.keyboard.press("Escape")
        return False

    new_list_link.click()
    pause(1)

    # We get redirected to the new list creation page
    # Fill in the list name
    name_input = page.locator("input[name='name'], input#list-name").first
    if name_input.count() == 0:
        # Maybe it opened inline — try inline input
        name_input = page.locator("input[placeholder*='list' i], input[placeholder*='name' i]").first

    if name_input.count() > 0:
        name_input.fill(list_name)
        pause(0.5)

        # Try pressing Enter to submit (works for both inline form and page redirect)
        name_input.press("Enter")
        pause(2)

        # If still on the creation page, try clicking the visible Create button
        if page.locator("input[name='name'], input#list-name").count() > 0:
            create_btn = page.locator("button:has-text('Create'):not([form*=poster])").first
            if create_btn.count() == 0:
                create_btn = page.locator("button[type='submit']:not([form*=poster])").first
            if create_btn.count() > 0:
                create_btn.click()
                pause(2)

        # Close any open modal/overlay so the page is in a clean state
        page.keyboard.press("Escape")
        pause(0.5)
        print(f"  [ok] Created list '{list_name}'")
        return True

    print("  [!] Could not fill list name — you may need to create the list manually on Letterboxd first.")
    page.keyboard.press("Escape")
    return False


def add_film_to_list_via_modal(page, film_slug: str, list_name: str) -> bool:
    """Open list modal and check the checkbox for our list."""
    page.goto("about:blank")
    pause(0.3)
    page.goto(f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="networkidle")
    pause(1)

    if not open_list_selection_modal(page):
        return False

    modal = page.locator("div.list-selection")

    # Search for our list in the search box
    search_input = modal.locator("input[placeholder='Type to search']").first
    if search_input.count() > 0:
        search_input.fill(list_name)
        pause(0.5)

    # Find the label containing our list name
    labels = modal.locator("label").all()
    for label in labels:
        text = label.inner_text().strip()
        if normalize_title(list_name) in normalize_title(text):
            checkbox = label.locator("input[type='checkbox']").first
            if checkbox.count() > 0 and not checkbox.is_checked():
                checkbox.click(force=True)
                pause(0.3)
            # Save
            modal_container = page.locator("div.list-selection").locator("xpath=ancestor::*[contains(@class, 'dialog-panel') or contains(@class, 'modal') or contains(@class, 'overlay')][1]")
            save_btn = modal_container.locator("button:has-text('Add'), button:has-text('Save'), button:has-text('Done')").first
            if save_btn.count() > 0:
                save_btn.click()
                pause(0.5)
            else:
                page.keyboard.press("Escape")
                pause(0.3)
            return True

    # List not found in modal — close
    page.keyboard.press("Escape")
    return False


def remove_film_from_list_via_modal(page, film_slug: str, list_name: str) -> bool:
    """Open list modal and uncheck the checkbox for our list."""
    page.goto("about:blank")
    pause(0.3)
    page.goto(f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="networkidle")
    pause(1)

    if not open_list_selection_modal(page):
        return False

    modal = page.locator("div.list-selection")

    search_input = modal.locator("input[placeholder='Type to search']").first
    if search_input.count() > 0:
        search_input.fill(list_name)
        pause(0.5)

    labels = modal.locator("label").all()
    for label in labels:
        text = label.inner_text().strip()
        if normalize_title(list_name) in normalize_title(text):
            checkbox = label.locator("input[type='checkbox']").first
            if checkbox.count() > 0 and checkbox.is_checked():
                checkbox.click(force=True)
                pause(0.3)
            modal_container = page.locator("div.list-selection").locator("xpath=ancestor::*[contains(@class, 'dialog-panel') or contains(@class, 'modal') or contains(@class, 'overlay')][1]")
            save_btn = modal_container.locator("button:has-text('Add'), button:has-text('Save'), button:has-text('Done')").first
            if save_btn.count() > 0:
                save_btn.click()
                pause(0.5)
            else:
                page.keyboard.press("Escape")
                pause(0.3)
            return True

    page.keyboard.press("Escape")
    return False


def extract_year(text: str) -> str | None:
    match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text or "")
    return match.group(1) if match else None


def search_film_slug(page, display_title: str, year: str, original_title: str) -> str | None:
    queries = []
    for value in (display_title, original_title, f"{display_title} {year}", f"{original_title} {year}"):
        if value and value not in queries:
            queries.append(value)

    for query in queries:
        url = f"{BASE_URL}/search/{quote(query)}/"
        page.goto("about:blank")
        pause(0.3)
        page.goto(url, timeout=60_000, wait_until="networkidle")
        pause(1.0)

        results = page.locator("ul.results li")
        count = results.count()
        for index in range(min(count, 20)):
            item = results.nth(index)
            link = item.locator("a[href*='/film/']").first
            if link.count() == 0:
                continue

            href = link.get_attribute("href") or ""
            if "/film/" not in href:
                continue

            slug = href.strip("/").split("/")[-1]

            # Extract title from first line of item text
            meta_text = item.inner_text().strip()
            first_line = meta_text.split("\n")[0].strip()
            film_title = re.sub(r"\s*\b(18|19|20)\d{2}\b.*$", "", first_line).strip()

            result_year = extract_year(first_line)
            if result_year and result_year != year:
                continue
            if not film_title:
                continue
            if not titles_match(film_title, display_title, original_title):
                continue

            return slug

    return None


def get_list_film_slugs(page, list_url: str) -> list[str]:
    print("\n[3/5] Reading current films in the Letterboxd list...")
    slugs: list[str] = []
    seen: set[str] = set()
    page_num = 1

    while True:
        url = list_url if page_num == 1 else f"{list_url.rstrip('/')}/page/{page_num}/"
        page.goto(url, timeout=60_000)
        pause(0.5)

        links = page.locator("a[href*='/film/']").all()
        page_slugs = []
        for link in links:
            href = link.get_attribute("href") or ""
            if "/film/" not in href:
                continue
            slug = href.strip("/").split("/")[-1]
            if slug and slug not in seen:
                seen.add(slug)
                page_slugs.append(slug)

        if not page_slugs:
            break

        slugs.extend(page_slugs)
        next_page = page.locator("a.next").first
        if next_page.count() == 0 or not next_page.is_visible():
            break
        page_num += 1

    print(f"  [ok] Found {len(slugs)} films in list")
    return slugs


def sync_letterboxd() -> None:
    print("Configuration:")
    print(f"  List: {LIST_NAME}")
    print(f"  CSV: {CSV_FILE}")
    print(f"  CDP URL: {CDP_URL}")

    csv_movies = load_csv_movies(CSV_FILE)

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"\n[!] Could not connect to Chrome at {CDP_URL}")
            print(f"    Error: {e}")
            print()
            print("    Launch Chrome first with:")
            print('    pkill -x "Google Chrome" && sleep 2')
            print('    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\')
            print('      --remote-debugging-port=9222 \\')
            print('      --user-data-dir="/Users/ardacildan/ChromeDebug" &')
            raise SystemExit(1)

        print(f"  [ok] Connected to Chrome")
        context = browser.contexts[0]

        # Use a fresh tab to avoid stale state (unsaved drafts, popups) from previous runs
        page = context.new_page()
        print("  [*] Opening new tab and navigating to letterboxd.com...")
        page.goto(f"{BASE_URL}/", timeout=60_000, wait_until="domcontentloaded")
        pause(2)

        if not letterboxd_login(page):
            raise SystemExit(1)

        username = get_username(page)
        if not username:
            print("[!] Could not determine Letterboxd username.")
            print("    Set LETTERBOXD_USERNAME in .env and run again.")
            raise SystemExit(1)
        username = username.lower()
        print(f"  Username: {username}")

        list_url = find_list(page, username, LIST_NAME)
        list_slugs = get_list_film_slugs(page, list_url) if list_url else []
        list_slug_set = set(list_slugs)
        csv_slug_set: set[str] = set()
        list_created = bool(list_url)

        print("\n[4/5] Syncing CSV movies to the list (in CSV order)...")
        for index, movie in enumerate(csv_movies, start=1):
            title = movie["title"]
            year = movie["year"]
            original_title = movie["original_title"]

            film_slug = search_film_slug(page, title, year, original_title)
            if not film_slug:
                print(f"  [{index}/{len(csv_movies)}] not found: {title} ({year})")
                continue

            csv_slug_set.add(film_slug)

            if film_slug in list_slug_set:
                print(f"  [{index}/{len(csv_movies)}] already in list: {title} ({year})")
                continue

            if not list_created:
                print(f"  [{index}/{len(csv_movies)}] creating list with: {title} ({year})")
                if create_list(page, film_slug, LIST_NAME):
                    list_url = find_list(page, username, LIST_NAME)
                    list_created = bool(list_url)
                    if not list_created:
                        print("  [!] List created but could not find its URL. Please create the list manually on Letterboxd and restart.")
                        raise SystemExit(1)
                else:
                    print("  [!] Could not create list automatically.")
                    print("      Please create a list named exactly:")
                    print(f"      '{LIST_NAME}'")
                    print("      on Letterboxd, then restart this script.")
                    raise SystemExit(1)

            print(f"  [{index}/{len(csv_movies)}] adding: {title} ({year})")
            if add_film_to_list_via_modal(page, film_slug, LIST_NAME):
                list_slug_set.add(film_slug)
            else:
                print(f"    [!] Could not add {title} to list")

        if not list_url:
            print("\n[!] No list was created or found. Skipping removals.")
            return

        print("\n[5/5] Removing list films that are no longer in the CSV...")
        removed = 0
        for slug in list_slugs:
            if slug in csv_slug_set:
                continue
            print(f"  removing: {slug}")
            if remove_film_from_list_via_modal(page, slug, LIST_NAME):
                removed += 1
            else:
                print(f"    [!] Could not remove {slug} from list")

        print("\nSync complete.")
        print(f"  List URL: {list_url}")
        print(f"  CSV films matched on Letterboxd: {len(csv_slug_set)}")
        print(f"  Removed from list: {removed}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sync_letterboxd()