import csv
import re
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://mubifinder.com/movies/country/tr/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
OUTPUT_FILE = "mubifinder_turkey_available.csv"
DELAY_SECONDS = 1.2

def fetch_page(page):
    if page == 1:
        url = BASE_URL + "?availability=available"
    else:
        url = BASE_URL + f"?availability=available&page={page}"
    print(f"  Fetching: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.content, "html.parser")
    except requests.RequestException as e:
        print(f"  [!] Error fetching page {page}: {e}")
        return None

def parse_movies(soup):
    movies = []
    for a in soup.find_all("a", href=re.compile(r"^/movie/")):
        href = a["href"].strip()
        raw = a.get_text(" ", strip=True)
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", raw)
        year = year_match.group(1) if year_match else ""
        dur_match = re.search(r"(\d+)\s*mins", raw)
        duration_mins = dur_match.group(1) if dur_match else ""
        rating_match = re.search(r"([\d.]+)/10\s*\((\d+)\)", raw)
        rating = rating_match.group(1) if rating_match else ""
        votes = rating_match.group(2) if rating_match else ""
        country_match = re.search(r"(\d+)\s*countr", raw, re.IGNORECASE)
        country_count = country_match.group(1) if country_match else ""
        title_raw = raw[:raw.find(year)].strip() if year else raw
        badge_pattern = re.compile(r"\b(new|just seen|coming soon|expiring soon|expired)\b", re.IGNORECASE)
        title_clean = badge_pattern.sub("", title_raw).strip()
        parts = [p.strip() for p in re.split(r"\s{2,}", title_clean) if p.strip()]
        seen = []
        for p in parts:
            if not seen or p.lower() != seen[-1].lower():
                seen.append(p)
        title = seen[0] if seen else ""
        original_title = seen[1] if len(seen) > 1 else ""
        movies.append({"title": title, "original_title": original_title, "year": year, "duration_mins": duration_mins, "rating": rating, "votes": votes, "countries_available": country_count, "url": "https://mubifinder.com" + href})
    return movies

def is_feature_film(title, original_title=""):
    """Check if entry is likely a feature film, not a series/episode/chapter."""
    import re
    combined_text = (title + " " + original_title).lower()
    
    # Patterns that indicate series/episodes
    patterns = [
        r'chapters?\s+[\d\-]+',     # Chapter/Chapters 1, 1-3, 1-5
        r'episode\s+\d+',           # Episode 1, Episode 13
        r'part\s+\d+',              # Part 1, Part 18
        r'season\s+\d+.*episode',   # Season 2 Episode 13
        r'\bs\d{2}e\d{2}\b',        # S01E01, S02E13
        r'\bpart\s+\d+:',           # Part 18: (colon suggests continuation)
    ]
    
    return not any(re.search(pattern, combined_text) for pattern in patterns)


def get_total_pages(soup):
    page_nums = []

    # Look for pagination urls like ?page=N
    for a in soup.find_all("a", href=True):
        m = re.search(r"page=(\d+)", a["href"])
        if m:
            page_nums.append(int(m.group(1)))

    # The page uses JS buttons like onclick="changePage(2, '...')"
    for button in soup.find_all("button", onclick=True):
        m = re.search(r"changePage\((\d+),", button["onclick"])
        if m:
            page_nums.append(int(m.group(1)))

    # Fallback: scan all page-change calls in the raw HTML text
    if not page_nums:
        for m in re.finditer(r"changePage\((\d+),", soup.decode()):
            page_nums.append(int(m.group(1)))

    return max(page_nums) if page_nums else 1


def fetch_soup(url):
    print(f"  Fetching: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return BeautifulSoup(r.content, "html.parser")
    except requests.RequestException as e:
        print(f"  [!] Error fetching {url}: {e}")
        return None


def fetch_movie_details(url):
    soup = fetch_soup(url)
    if not soup:
        return {}

    title = ""
    title_el = soup.select_one("h1.movie__title")
    if title_el:
        title = title_el.get_text(strip=True)

    original_title = ""
    original_el = soup.select_one(".movie__subtitle")
    if original_el:
        original_title = original_el.get_text(strip=True)

    mubi_url = ""
    for a in soup.select("a[href]"):
        href = a["href"].strip()
        if "mubi.com/films" in href.lower():
            mubi_url = href if href.startswith("http") else "https://" + href.lstrip("/")
            break
        if a.get_text(strip=True).lower() == "watch on mubi":
            mubi_url = href if href.startswith("http") else "https://" + href.lstrip("/")
            break

    year = ""
    duration_mins = ""
    rating = ""
    votes = ""
    about_el = soup.select_one(".movie__about")
    if about_el:
        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", about_el.get_text())
        if year_match:
            year = year_match.group(1)

    metadata_items = [item.get_text(" ", strip=True) for item in soup.select(".movie__metadata-item") if item.get_text(strip=True)]
    if metadata_items:
        dur_match = re.search(r"(\d+)\s*mins", metadata_items[0])
        if dur_match:
            duration_mins = dur_match.group(1)
    if len(metadata_items) > 1:
        rating_match = re.search(r"([\d.]+)/10\s*\((\d+)\)", metadata_items[1])
        if rating_match:
            rating = rating_match.group(1)
            votes = rating_match.group(2)

    return {
        "title": title,
        "original_title": original_title,
        "year": year,
        "duration_mins": duration_mins,
        "rating": rating,
        "votes": votes,
        "mubi_url": mubi_url,
    }


def main():
    print("=== MubiFinder Turkey Scraper ===")
    first_page = fetch_page(1)
    if not first_page:
        print("Failed. Exiting.")
        return
    total_pages = get_total_pages(first_page)
    print(f"Total pages detected: {total_pages}")
    all_movies = parse_movies(first_page)
    print(f"  Page 1 → {len(all_movies)} movies")
    for page in range(2, total_pages + 1):
        time.sleep(DELAY_SECONDS)
        print(f"  Page {page}/{total_pages} …", end=" ")
        soup = fetch_page(page)
        if not soup:
            print("SKIPPED")
            continue
        batch = parse_movies(soup)
        all_movies.extend(batch)
        print(f"{len(batch)} movies")
    seen_urls = set()
    unique = [m for m in all_movies if not (m["url"] in seen_urls or seen_urls.add(m["url"]))]
    print(f"\nTotal unique movies: {len(unique)}")

    print("\nFetching movie detail pages for accurate titles and MUBI links...")
    for index, movie in enumerate(unique, start=1):
        time.sleep(DELAY_SECONDS)
        print(f"  [{index}/{len(unique)}] {movie['url']}")
        details = fetch_movie_details(movie["url"])
        if details:
            movie["title"] = details.get("title") or movie["title"]
            movie["original_title"] = details.get("original_title") or movie["original_title"]
            movie["year"] = details.get("year") or movie["year"]
            movie["duration_mins"] = details.get("duration_mins") or movie["duration_mins"]
            movie["rating"] = details.get("rating") or movie["rating"]
            movie["votes"] = details.get("votes") or movie["votes"]
            movie["mubi_url"] = details.get("mubi_url") or movie.get("mubi_url", "")
        else:
            print(f"    [!] Failed to fetch details for {movie['url']}")

    # Filter out series/episodes/chapters
    feature_films = [m for m in unique if is_feature_film(m["title"], m["original_title"])]
    print(f"\nFeature films (after filtering series/episodes): {len(feature_films)}")
    if len(feature_films) < len(unique):
        print(f"  (Removed {len(unique) - len(feature_films)} series/episodes)")

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["title","original_title","year","duration_mins","rating","votes","countries_available","url","mubi_url"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(feature_films)
    print(f"Saved → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()