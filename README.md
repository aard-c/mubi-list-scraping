# MUBI List Scraping

Tools to scrape Turkey-available MUBI films from MubiFinder and sync them to a Letterboxd list.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

Copy `.env.example` to `.env` and fill in your Letterboxd credentials (only needed for the sync script):

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

The script logs into Letterboxd, finds or creates the list **MUBI all Movies TR**, then:

1. Walks each CSV row in order
2. Searches Letterboxd and matches by year + title / original title
3. Skips films already in the list, adds missing ones
4. Removes films from the Letterboxd list that are no longer in the CSV

```bash
python letterboxd_sync.py
```

Run with the browser visible the first time (`LETTERBOXD_HEADLESS=false` in `.env`) so you can confirm login and list actions work.

### Environment variables

| Variable | Description | Default |
| --- | --- | --- |
| `LETTERBOXD_EMAIL` | Letterboxd email or username | *(required)* |
| `LETTERBOXD_PASSWORD` | Letterboxd password | *(required)* |
| `LETTERBOXD_LIST_NAME` | Target list name | `MUBI all Movies TR` |
| `LETTERBOXD_CSV_FILE` | CSV path to sync | `mubifinder_turkey_available.csv` |
| `LETTERBOXD_HEADLESS` | Run browser without UI | `false` |
| `LETTERBOXD_DELAY_SECONDS` | Pause between page actions | `1.0` |

See `.env.example` for a copy-paste template.


-- make faster checking movies after first run, make a separete script if its necesarry.