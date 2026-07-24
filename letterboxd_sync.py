#!/usr/bin/env python3
"""
Sync mubifinder_turkey_available.csv to a Letterboxd list.

HOW TO RUN:
1. Kill Chrome and relaunch with remote debugging:
   pkill -x "Google Chrome" && sleep 2
   /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
     --remote-debugging-port=9222
     --user-data-dir="/Users/ardacildan/ChromeDebug" &
2. Log into Letterboxd in that Chrome window.
3. Run: python3 letterboxd_sync.py
"""

import csv
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote, urljoin

from playwright.sync_api import Error as PlaywrightError

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

load_dotenv()

USERNAME = os.getenv("LETTERBOXD_USERNAME")
LIST_NAME = os.getenv("LETTERBOXD_LIST_NAME", "MUBI all Movies TR")
CSV_FILE = os.getenv("LETTERBOXD_CSV_FILE", "mubifinder_turkey_available.csv")
REQUEST_DELAY = float(os.getenv("LETTERBOXD_DELAY_SECONDS", "1.0"))
LOGIN_WAIT_SECONDS = float(os.getenv("LETTERBOXD_LOGIN_WAIT_SECONDS", "45"))


def _default_cdp_url() -> str:
    configured = os.getenv("LETTERBOXD_CDP_URL", "").strip()
    if configured:
        return configured.replace("localhost", "127.0.0.1")
    return "http://127.0.0.1:9222"


CDP_URL = _default_cdp_url()

BASE_URL = "https://letterboxd.com"

CACHE_FILE = Path("letterboxd_film_cache.json")


def _normalize(value: str) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def cache_key(title: str, year: str, original_title: str) -> str:
    return f"{_normalize(title)}|{year}|{_normalize(original_title)}"


class FilmCache:
    def __init__(self, path: Path = CACHE_FILE):
        self.path = path
        self.data: dict = {}
        self.dirty = False
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                with self.path.open("r", encoding="utf-8") as handle:
                    self.data = json.load(handle)
                print(f"  [cache] Loaded {len(self.data)} cached lookups from {self.path}")
            except (json.JSONDecodeError, OSError):
                print(f"  [cache] Could not read {self.path}, starting fresh")
                self.data = {}
        else:
            self.data = {}

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            with self.path.open("w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
            self.dirty = False
        except OSError as error:
            print(f"  [cache] Warning: could not save cache: {error}")

    def get(self, title: str, year: str, original_title: str):
        """
        Returns:
          - "MISS" (sentinel) if not in cache -> caller should search
          - None if cached as "not found on Letterboxd"
          - str (slug) if cached as found
        """
        key = cache_key(title, year, original_title)
        entry = self.data.get(key)
        if entry is None:
            return "MISS"
        return entry.get("slug")

    def set(self, title: str, year: str, original_title: str, slug: str | None) -> None:
        key = cache_key(title, year, original_title)
        self.data[key] = {"slug": slug, "title": title, "year": year}
        self.dirty = True

    def stats(self) -> tuple[int, int]:
        found = sum(1 for value in self.data.values() if value.get("slug"))
        not_found = sum(1 for value in self.data.values() if not value.get("slug"))
        return found, not_found


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


def safe_goto(page, url: str, *, timeout: int = 60_000, wait_until: str = "domcontentloaded") -> bool:
    try:
        page.goto(url, timeout=timeout, wait_until=wait_until)
        return True
    except PlaywrightError as error:
        print(f"  [!] Navigation timed out for {url}: {error}")
        return False


def check_logged_in_on_page(page) -> bool:
    try:
        return page.locator("a:has-text('Sign Out'), a[href*='sign-out']").count() > 0
    except Exception:
        return False


def navigate_and_check_logged_in(page) -> bool:
    try:
        if not safe_goto(page, f"{BASE_URL}/", timeout=60_000, wait_until="domcontentloaded"):
            return False
        pause(1)
        return page.locator("a:has-text('Sign Out'), a[href*='sign-out']").count() > 0
    except Exception:
        return False


def get_username(page) -> str | None:
    if USERNAME:
        return USERNAME.strip()

    if not safe_goto(page, f"{BASE_URL}/settings/", timeout=60_000, wait_until="domcontentloaded"):
        return None
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
    if LOGIN_WAIT_SECONDS <= 0:
        print("  [!] Login wait disabled; exiting.")
        return False
    print(f"  Waiting up to {int(LOGIN_WAIT_SECONDS)} seconds for you to log in...")

    deadline = time.monotonic() + LOGIN_WAIT_SECONDS
    while True:
        elapsed = int(deadline - time.monotonic())
        if elapsed <= 0:
            break
        if int(LOGIN_WAIT_SECONDS) - elapsed > 0 and (int(LOGIN_WAIT_SECONDS) - elapsed) % 15 == 0:
            print(f"  ...still waiting ({(int(LOGIN_WAIT_SECONDS) - elapsed) // 60}m {(int(LOGIN_WAIT_SECONDS) - elapsed) % 60}s)")
        pause(1)
        if check_logged_in_on_page(page):
            print("  [ok] Logged in!")
            return True

    print("  [!] Login timed out.")
    return False


def find_list(page, username: str, list_name: str) -> str | None:
    print(f"\n[2/5] Finding list '{list_name}'...")
    if not safe_goto(page, f"{BASE_URL}/{username}/lists/", timeout=60_000, wait_until="domcontentloaded"):
        return None
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
    if not safe_goto(page, f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="networkidle"):
        return False
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
    if not safe_goto(page, f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="networkidle"):
        return False
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


def film_is_on_mubi_tr(page, film_slug: str) -> bool:
    """Check the film detail page for the MUBI TR availability badge."""
    if not safe_goto(page, f"{BASE_URL}/film/{film_slug}/", timeout=60_000, wait_until="networkidle"):
        return False
    pause(0.8)

    watch_panel = page.locator("section.watch-panel.js-watch-panel").first
    if watch_panel.count() == 0:
        return False

    mubi_service = watch_panel.locator("p#source-mubi.service.-mubi").first
    if mubi_service.count() == 0:
        return False

    try:
        text = (mubi_service.inner_text() or "").upper()
    except Exception:
        text = ""

    return "MUBI" in text and "TR" in text


def remove_film_from_list_via_modal(page, film_slug: str, list_name: str) -> bool:
    """Open the list-card overflow menu and remove the film from the list."""
    item = page.locator(f"li.posteritem:has(div[data-item-slug='{film_slug}'])").first
    if item.count() == 0:
        return False

    try:
        item.scroll_into_view_if_needed(timeout=5_000)
        pause(0.2)
        poster = item.locator("div.poster.film-poster").first
        if poster.count() > 0:
            poster.hover(timeout=5_000)
        else:
            item.hover(timeout=5_000)
        pause(0.4)
    except Exception:
        pass

    menu_trigger = item.locator("span.replace.menu-link.icon").first
    if menu_trigger.count() == 0:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return False

    try:
        menu_trigger.click(timeout=5_000, force=True)
        pause(0.7)
    except Exception:
        try:
            menu_trigger.hover(timeout=5_000)
            pause(0.3)
            menu_trigger.click(timeout=5_000)
            pause(0.7)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return False

    remove_candidates = [
        page.locator("div.popmenu:not([hidden])").get_by_text("Remove from this list", exact=True),
        page.locator("div.popmenu:visible").get_by_text("Remove from this list", exact=True),
        page.get_by_text("Remove from this list", exact=True),
    ]

    for candidate in remove_candidates:
        try:
            if candidate.count() > 0:
                candidate.click(timeout=5_000)
                pause(0.8)
                break
        except Exception:
            continue

    confirm_candidates = [
        page.get_by_role("button", name="Remove Film"),
        page.locator("button:has-text('Remove Film')"),
        page.locator("button:has-text('Confirm Removal')"),
    ]

    for candidate in confirm_candidates:
        try:
            if candidate.count() > 0:
                candidate.first.click(timeout=5_000, force=True)
                pause(1.0)
                return True
        except Exception:
            continue

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    return False


def extract_year(text: str) -> str | None:
    match = re.search(r"\b(18\d{2}|19\d{2}|20\d{2})\b", text or "")
    return match.group(1) if match else None


def search_film_slug(page, display_title: str, year: str, original_title: str) -> str | None:
    queries = []
    for value in (f"{original_title} {year}", f"{display_title} {year}"):
        if value and value not in queries:
            queries.append(value)

    for query in queries:
        url = f"{BASE_URL}/search/{quote(query)}/"
        page.goto("about:blank")
        pause(0.3)
        if not safe_goto(page, url, timeout=60_000, wait_until="networkidle"):
            continue
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


def collect_list_film_slugs(page) -> tuple[list[str], dict[str, dict]]:
    """Return (slugs, slug_meta) from the current Letterboxd list state."""
    slugs: list[str] = []
    seen: set[str] = set()
    slug_meta: dict[str, dict] = {}

    while True:

        # --- collect slugs + metadata from poster divs (data-* attributes) ---
        poster_divs = page.locator("div[data-film-slug]").all()
        page_slugs = []
        for div in poster_divs:
            slug = (div.get_attribute("data-film-slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            page_slugs.append(slug)
            title = (div.get_attribute("data-film-name") or "").strip()
            year = (div.get_attribute("data-film-release-year") or "").strip()
            if title:
                slug_meta[slug] = {"title": title, "year": year, "original_title": ""}

        # --- fallback: collect from <a href=/film/…> links for any missed slugs ---
        links = page.locator("a[href*='/film/']").all()
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
        next_href = next_page.get_attribute("href") or ""
        if not next_href:
            break
        if not safe_goto(page, urljoin(BASE_URL, next_href), timeout=60_000, wait_until="domcontentloaded"):
            break
        pause(0.5)

    return slugs, slug_meta


def collect_current_page_film_slugs(page) -> tuple[list[str], dict[str, dict]]:
    """Return (slugs, slug_meta) for only the currently visible list page."""
    slugs: list[str] = []
    slug_meta: dict[str, dict] = {}
    seen: set[str] = set()

    poster_divs = page.locator("div[data-film-slug]").all()
    for div in poster_divs:
        slug = (div.get_attribute("data-film-slug") or "").strip()
        if not slug or slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
        title = (div.get_attribute("data-film-name") or "").strip()
        year = (div.get_attribute("data-film-release-year") or "").strip()
        if title:
            slug_meta[slug] = {"title": title, "year": year, "original_title": ""}

    links = page.locator("a[href*='/film/']").all()
    for link in links:
        href = link.get_attribute("href") or ""
        if "/film/" not in href:
            continue
        slug = href.strip("/").split("/")[-1]
        if slug and slug not in seen:
            seen.add(slug)
            slugs.append(slug)

    return slugs, slug_meta


def get_list_film_slugs(page, list_url: str) -> tuple[list[str], dict[str, dict]]:
    """Read all list films from the unfiltered list page."""
    print("\n[3/5] Reading current films in the Letterboxd list...")
    if not safe_goto(page, list_url, timeout=60_000, wait_until="domcontentloaded"):
        return [], {}
    pause(0.5)
    slugs, slug_meta = collect_list_film_slugs(page)
    print(f"  [ok] Found {len(slugs)} films in list ({len(slug_meta)} with metadata)")
    return slugs, slug_meta


def open_list_mubi_tr_filter(page, list_url: str) -> bool:
    """Open the MUBI TR exclusion filter for this list."""
    service_url = f"{list_url.rstrip('/')}/not/on/mubi-tr/"
    print("\n[6/6] Filtering list to films not on MUBI TR...")
    try:
        if not safe_goto(page, service_url, timeout=60_000, wait_until="domcontentloaded"):
            return False
        pause(1.0)
        current_url = page.url or ""
        if "/not/on/mubi-tr/" in current_url:
            print(f"  [ok] Opened service filter: {service_url}")
            return True
    except Exception as error:
        print(f"  [!] Could not open MUBI TR filter URL: {error}")
        return False

    try:
        if not safe_goto(page, list_url, timeout=60_000, wait_until="domcontentloaded"):
            return False
        pause(0.5)
        exclusion_link = page.locator("a:has-text('Exclude matching films')").first
        mubi_link = page.locator("a[href*='/not/on/mubi-tr/'], a[href*='/on/mubi-tr/']").first
        if exclusion_link.count() > 0:
            exclusion_link.click(timeout=5_000, force=True)
            pause(0.8)
        if mubi_link.count() > 0:
            mubi_link.click(timeout=5_000, force=True)
            pause(1.0)
            if "/not/on/mubi-tr/" in (page.url or ""):
                print("  [ok] Selected Exclude matching films + MUBI TR")
                return True
    except Exception as error:
        print(f"  [!] Could not activate MUBI TR filter in the UI: {error}")
        return False

    print("  [!] Could not open the MUBI TR service filter page")
    return False


def get_next_page_href(page) -> str | None:
    next_page = page.locator("a.next").first
    if next_page.count() == 0 or not next_page.is_visible():
        return None
    return next_page.get_attribute("href") or None


def sync_letterboxd() -> None:
    print("Configuration:")
    print(f"  List: {LIST_NAME}")
    print(f"  CSV: {CSV_FILE}")
    print(f"  CDP URL: {CDP_URL}")

    csv_movies = load_csv_movies(CSV_FILE)

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            browser = playwright.chromium.connect_over_cdp(CDP_URL)
            print(f"  [ok] Connected to Chrome via {CDP_URL}")
            context = browser.contexts[0]
        except Exception as e:
            print(f"\n[!] Could not connect to Chrome at {CDP_URL}")
            print(f"    Error: {e}")
            print("    Falling back to launching a local Chrome instance...")
            try:
                profile_dir = Path("/tmp/letterboxd-playwright-profile")
                profile_dir.mkdir(parents=True, exist_ok=True)
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir),
                    channel="chrome",
                    headless=False,
                    args=["--no-first-run", "--no-default-browser-check"],
                )
                print("  [ok] Launched local Chrome instance")
            except Exception as launch_error:
                print("    [!] Could not launch local Chrome")
                print(f"        Error: {launch_error}")
                raise SystemExit(1)

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
        if list_url:
            list_slugs, list_slug_meta = get_list_film_slugs(page, list_url)
        else:
            list_slugs, list_slug_meta = [], {}
        list_slug_set = set(list_slugs)
        csv_slug_set: set[str] = set()
        list_created = bool(list_url)

        cache = FilmCache()

        # --- Pre-populate cache from list metadata so already-in-list films
        #     are resolved instantly without a Letterboxd search on every run. ---
        pre_cached = 0
        for slug, meta in list_slug_meta.items():
            title = meta["title"]
            year = meta["year"]
            orig = meta["original_title"]
            if cache.get(title, year, orig) != slug:
                cache.set(title, year, orig, slug)
                pre_cached += 1
        if pre_cached:
            cache.save()
            print(f"  [cache] Pre-cached {pre_cached} films from existing list")

        print("\n[4/5] Syncing CSV movies to the list (in CSV order)...")
        for index, movie in enumerate(csv_movies, start=1):
            title = movie["title"]
            year = movie["year"]
            original_title = movie["original_title"]

            # --- slug lookup: cache first, retry stale not-found entries, search with year included ---
            cached = cache.get(title, year, original_title)
            if cached == "MISS":
                film_slug = search_film_slug(page, title, year, original_title)
                cache.set(title, year, original_title, film_slug)
                cache.save()  # persist after every new lookup
            elif cached is None:
                film_slug = search_film_slug(page, title, year, original_title)
                cache.set(title, year, original_title, film_slug)
                cache.save()
            else:
                film_slug = cached  # may be None (previously not found) or a slug

            if not film_slug:
                print(f"  [{index}/{len(csv_movies)}] not found: {title} ({year})")
                continue

            csv_slug_set.add(film_slug)

            if film_slug in list_slug_set:
                print(f"  [{index}/{len(csv_movies)}] already in list: {title} ({year})")
                continue

            if not film_is_on_mubi_tr(page, film_slug):
                print(f"  [{index}/{len(csv_movies)}] not on MUBI TR: {title} ({year})")
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

        removed_no_service = 0
        for cleanup_round in range(1, 3):
            print(f"\n[6/6] MUBI TR cleanup pass {cleanup_round}/2...")
            if not safe_goto(page, list_url, timeout=60_000, wait_until="domcontentloaded"):
                break
            pause(0.5)
            if open_list_mubi_tr_filter(page, list_url):
                page_index = 1
                while True:
                    current_slugs, _ = collect_current_page_film_slugs(page)
                    print(f"  [ok] Page {page_index}: {len(current_slugs)} films not on MUBI TR")
                    next_href = get_next_page_href(page)

                    if not current_slugs:
                        if not next_href:
                            break
                        if not safe_goto(page, urljoin(BASE_URL, next_href), timeout=60_000, wait_until="domcontentloaded"):
                            break
                        pause(0.5)
                        page_index += 1
                        continue

                    for slug in current_slugs:
                        print(f"  removing (not on MUBI TR): {slug}")
                        if remove_film_from_list_via_modal(page, slug, LIST_NAME):
                            removed_no_service += 1
                            pause(0.7)
                        else:
                            print(f"    [!] Could not remove {slug} from list")
                            pause(0.7)

                    if not next_href:
                        break
                    if not safe_goto(page, urljoin(BASE_URL, next_href), timeout=60_000, wait_until="domcontentloaded"):
                        break
                    pause(0.7)
                    page_index += 1
            else:
                print("  [!] Skipping MUBI TR cleanup because the filter could not be opened")

        found, not_found = cache.stats()
        print("\nSync complete.")
        print(f"  List URL: {list_url}")
        print(f"  CSV films matched on Letterboxd: {len(csv_slug_set)}")
        print(f"  Removed from list: {removed}")
        print(f"  Removed not on MUBI TR: {removed_no_service}")
        print(f"  Cache: {found} found, {not_found} not-found entries")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    sync_letterboxd()
