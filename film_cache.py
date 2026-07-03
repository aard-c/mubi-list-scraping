#!/usr/bin/env python3
"""
Simple JSON cache mapping (title, year, original_title) -> letterboxd film slug (or None if not found).

Speeds up re-runs of letterboxd_sync.py by skipping Letterboxd search for movies
we've already resolved in a previous run.

Cache file: letterboxd_film_cache.json
Format: { "normalized_key": {"slug": "taxi-driver" or null, "title": "...", "year": "..."} }
"""

import json
import re
import unicodedata
from pathlib import Path

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
                with self.path.open("r", encoding="utf-8") as f:
                    self.data = json.load(f)
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
            with self.path.open("w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            self.dirty = False
        except OSError as e:
            print(f"  [cache] Warning: could not save cache: {e}")

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
        return entry.get("slug")  # may be None (cached not-found) or a slug string

    def set(self, title: str, year: str, original_title: str, slug: str | None) -> None:
        key = cache_key(title, year, original_title)
        self.data[key] = {"slug": slug, "title": title, "year": year}
        self.dirty = True

    def stats(self) -> tuple[int, int]:
        found = sum(1 for v in self.data.values() if v.get("slug"))
        not_found = sum(1 for v in self.data.values() if not v.get("slug"))
        return found, not_found