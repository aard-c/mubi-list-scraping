# MUBI List Scraping

Tools to scrape Turkey-available MUBI films from MubiFinder and sync them to a Letterboxd list.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your Letterboxd username/settings (only needed for the sync script):

```bash
cp .env.example .env
```

## 1. Scrape MubiFinder (Turkey, available)

Scrapes all pages from:

https://mubifinder.com/movies/country/tr/?availability=available

**Output:** `mubifinder_turkey_available.csv`

```bash
python mubifinder-tr.py
```

## 2. Sync CSV to Letterboxd

Reads `mubifinder_turkey_available.csv` directly — it does **not** re-run the scraper.

The sync script connects to an already-running Chrome window through the Chrome DevTools Protocol. That means Chrome must be started in remote-debugging mode **before** you run `letterboxd_sync.py`.

### Start Chrome in debug mode

```bash
pkill -x "Google Chrome" && sleep 2
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="/Users/ardacildan/ChromeDebug" &
```

Then log into Letterboxd in that Chrome window, and run:

```bash
python3 letterboxd_sync.py
```

### What the sync does

The script loads `mubifinder_turkey_available.csv`, finds or creates the Letterboxd list **MUBI all Movies TR**, then:

1. Walks each CSV row in order
2. Searches Letterboxd and matches by year plus title/original title
3. Skips films already in the list and adds missing ones
4. Removes films from the Letterboxd list that are no longer in the CSV
5. Caches lookup results in `letterboxd_film_cache.json` so later runs are faster

If you want to force fresh lookups, delete `letterboxd_film_cache.json` and run the sync again.

### Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `LETTERBOXD_USERNAME` | Your Letterboxd username | *(required if the script cannot infer it from the page)* |
| `LETTERBOXD_LIST_NAME` | Target list name | `MUBI all Movies TR` |
| `LETTERBOXD_CSV_FILE` | CSV path to sync | `mubifinder_turkey_available.csv` |
| `LETTERBOXD_DELAY_SECONDS` | Pause between page actions | `1.0` |
| `LETTERBOXD_CDP_URL` | Chrome debugging endpoint | `http://localhost:9222` |

See `.env.example` for the current template.
