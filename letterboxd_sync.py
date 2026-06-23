#!/usr/bin/env python3
"""
Sync mubifinder_turkey_available.csv to a Letterboxd list.

Reads the CSV directly (does not run the Mubi scraper).
Requires: pip install python-dotenv playwright && playwright install chromium
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

EMAIL = os.getenv("LETTERBOXD_EMAIL")
PASSWORD = os.getenv("LETTERBOXD_PASSWORD")
USERNAME = os.getenv("LETTERBOXD_USERNAME")
LIST_NAME = os.getenv("LETTERBOXD_LIST_NAME", "MUBI all Movies TR")
CSV_FILE = os.getenv("LETTERBOXD_CSV_FILE", "mubifinder_turkey_available.csv")
HEADLESS = os.getenv("LETTERBOXD_HEADLESS", "false").lower() in {"1", "true", "yes"}
REQUEST_DELAY = float(os.getenv("LETTERBOXD_DELAY_SECONDS", "1.0"))
STORAGE_STATE = os.getenv("LETTERBOXD_STORAGE_STATE", ".letterboxd_auth.json")

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


def is_logged_in(page) -> bool:
    page.goto(f"{BASE_URL}/", timeout=60_000, wait_until="domcontentloaded")
    pause(0.3)
    return page.locator("a[href*='sign-out']").count() > 0


def get_username(page) -> str | None:
    if USERNAME:
        return USERNAME.strip()

    page.goto(f"{BASE_URL}/settings/", timeout=60_000, wait_until="domcontentloaded")
    pause(0.3)

    for selector in ("input[name='login']", "input#login", "input[name='username']"):
        field = page.locator(selector).first
        if field.count() == 0:
            continue
        value = field.input_value().strip()
        if value:
            return value

    profile_link = page.locator("a[href*='sign-out']").locator(
        "xpath=ancestor::header//a[starts-with(@href, '/') and not(contains(@href, 'sign'))][1]"
    ).first
    if profile_link.count() > 0:
        href = profile_link.get_attribute("href") or ""
        parts = href.strip("/").split("/")
        if len(parts) == 1 and parts[0]:
            return parts[0]

    return None


def save_session(context) -> None:
    context.storage_state(path=STORAGE_STATE)
    print(f"  [ok] Saved session to {STORAGE_STATE}")


def letterboxd_login(page, context, email: str, password: str) -> bool:
    print("\n[1/5] Logging in to Letterboxd...")

    if is_logged_in(page):
        print("  [ok] Already logged in")
        return True

    page.goto(f"{BASE_URL}/sign-in/", timeout=120_000, wait_until="domcontentloaded")
    pause(0.5)
    page.locator("#field-username").fill(email)
    page.locator("#field-password").fill(password)
    page.locator("form button.standalone-flow-button").click()

    for _ in range(30):
        pause(1)
        if is_logged_in(page):
            print("  [ok] Logged in")
            save_session(context)
            return True

    if HEADLESS:
        print("  [!] Login failed. Letterboxd often blocks headless browsers.")
        print("      Set LETTERBOXD_HEADLESS=false and run again, or sign in manually once.")
        return False

    print("  [!] Auto login did not complete.")
    print("  A Chromium window should be open. Sign in there (including any Cloudflare check).")
    page.goto(f"{BASE_URL}/sign-in/", timeout=120_000, wait_until="domcontentloaded")
    print("  Waiting up to 10 minutes for manual sign-in...")
    for second in range(600):
        if second > 0 and second % 30 == 0:
            print(f"  ...still waiting ({second // 60}m {second % 60}s)")
        pause(1)
        if is_logged_in(page):
            print("  [ok] Logged in manually")
            save_session(context)
            return True

    print("  [!] Login timed out. Check LETTERBOXD_EMAIL and LETTERBOXD_PASSWORD in .env")
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


def create_list_with_film(page, film_slug: str, list_name: str) -> bool:
    print(f"  [*] Creating list '{list_name}' with first matched film...")
    page.goto(f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="domcontentloaded")
    pause(0.5)

    if not open_list_modal(page):
        print("  [!] Could not open Lists modal on film page")
        return False

    modal = page.locator(".modal, .overlay, [role='dialog']").last
    new_list_button = modal.locator("a:has-text('New list'), button:has-text('New list')").first
    if new_list_button.count() == 0:
        page.keyboard.press("Escape")
        print("  [!] Could not find 'New list' button in modal")
        return False

    new_list_button.click()
    pause(0.5)

    name_input = page.locator(
        "input[name='name'], input[placeholder*='List'], input[placeholder*='list']"
    ).last
    name_input.wait_for(state="visible", timeout=10_000)
    name_input.fill(list_name)
    pause(0.3)

    save_button = page.locator(
        "button:has-text('Save'), button:has-text('Create'), button:has-text('Add')"
    ).last
    save_button.click()
    pause(0.5)
    page.keyboard.press("Escape")
    print(f"  [ok] Created list '{list_name}'")
    return True


def extract_year(text: str) -> str | None:
    match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text or "")
    return match.group(1) if match else None


def search_film_slug(page, display_title: str, year: str, original_title: str) -> str | None:
    queries = []
    for value in (display_title, original_title, f"{display_title} {year}", f"{original_title} {year}"):
        if value and value not in queries:
            queries.append(value)

    for query in queries:
        page.goto(f"{BASE_URL}/search/{quote(query)}/", timeout=60_000)
        pause(0.5)

        results = page.locator("ul.results li, .results .poster-list li, .poster-list li")
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
            film_title = link.get_attribute("data-original-title") or link.get_attribute("data-film-name")
            if not film_title:
                film_title = link.get_attribute("title") or link.inner_text().strip()

            meta_text = item.inner_text()
            result_year = extract_year(meta_text) or extract_year(film_title)
            if result_year and result_year != year:
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


def open_list_modal(page) -> bool:
    selectors = [
        "button:has-text('Lists')",
        "a:has-text('Lists')",
        "button[data-action='list']",
        "a.js-list-action",
        "button.icon-lists",
    ]
    for selector in selectors:
        button = page.locator(selector).first
        if button.count() == 0:
            continue
        try:
            button.click(timeout=5_000)
            pause(0.5)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


def set_film_in_list(page, list_name: str, should_be_in_list: bool) -> bool:
    if not open_list_modal(page):
        return False

    modal = page.locator(".modal, .overlay, [role='dialog']").last
    labels = modal.locator("label")
    for index in range(labels.count()):
        label = labels.nth(index)
        text = label.inner_text().strip()
        if normalize_title(list_name) not in normalize_title(text):
            continue

        checkbox = label.locator("input[type='checkbox']").first
        if checkbox.count() == 0:
            checkbox = page.locator(f"input[type='checkbox']").nth(index)

        checked = checkbox.is_checked()
        if checked != should_be_in_list:
            checkbox.click(force=True)
            pause(0.3)

        save_button = page.locator("button:has-text('Save'), button:has-text('Done')").first
        if save_button.count() > 0:
            save_button.click()
            pause(0.5)
        else:
            page.keyboard.press("Escape")
            pause(0.3)
        return True

    page.keyboard.press("Escape")
    return False


def add_film_to_list(page, film_slug: str, list_name: str) -> bool:
    page.goto(f"{BASE_URL}/film/{film_slug}/", timeout=60_000)
    pause(0.5)
    return set_film_in_list(page, list_name, should_be_in_list=True)


def remove_film_from_list(page, film_slug: str, list_name: str) -> bool:
    page.goto(f"{BASE_URL}/film/{film_slug}/", timeout=60_000)
    pause(0.5)
    return set_film_in_list(page, list_name, should_be_in_list=False)


def sync_letterboxd() -> None:
    if not EMAIL or not PASSWORD:
        print("Error: set LETTERBOXD_EMAIL and LETTERBOXD_PASSWORD in .env")
        raise SystemExit(1)

    print("Configuration:")
    print(f"  Email: {EMAIL}")
    print(f"  List: {LIST_NAME}")
    print(f"  CSV: {CSV_FILE}")
    print(f"  Headless: {HEADLESS}")

    csv_movies = load_csv_movies(CSV_FILE)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)
        context_kwargs = {}
        if Path(STORAGE_STATE).exists():
            context_kwargs["storage_state"] = STORAGE_STATE
            print(f"  Session file: {STORAGE_STATE}")
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        if not letterboxd_login(page, context, EMAIL, PASSWORD):
            browser.close()
            raise SystemExit(1)

        username = get_username(page)
        if not username:
            print("[!] Could not determine Letterboxd username.")
            print("    Set LETTERBOXD_USERNAME in .env and run again.")
            browser.close()
            raise SystemExit(1)
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
                if create_list_with_film(page, film_slug, LIST_NAME):
                    list_url = find_list(page, username, LIST_NAME)
                    list_created = True
                    list_slug_set.add(film_slug)
                else:
                    print(f"    [!] Could not create list using {title}")
                continue

            print(f"  [{index}/{len(csv_movies)}] adding: {title} ({year})")
            if add_film_to_list(page, film_slug, LIST_NAME):
                list_slug_set.add(film_slug)
            else:
                print(f"    [!] Could not add {title} to list")

        if not list_url:
            print("\n[!] No list was created or found. Skipping removals.")
            browser.close()
            return

        print("\n[5/5] Removing list films that are no longer in the CSV...")
        removed = 0
        for slug in list_slugs:
            if slug in csv_slug_set:
                continue
            print(f"  removing: {slug}")
            if remove_film_from_list(page, slug, LIST_NAME):
                removed += 1
            else:
                print(f"    [!] Could not remove {slug} from list")

        print("\nSync complete.")
        print(f"  List URL: {list_url}")
        print(f"  CSV films matched on Letterboxd: {len(csv_slug_set)}")
        print(f"  Removed from list: {removed}")

        browser.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sync_letterboxd()
