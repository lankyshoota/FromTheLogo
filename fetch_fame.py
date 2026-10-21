"""
FromTheLogo — how recognizable is each player? Wikipedia page views, last 12 months.

For every player in players.json:
  1. Find his English Wikipedia article. Wikidata links NBA.com player ids to articles
     (property P3647), so one query matches thousands of players exactly, with no
     guessing between two Charles Joneses. Players Wikidata doesn't link get a Wikipedia
     search ("<name> basketball"); a result only counts if its title is his name and it's
     about basketball.
  2. Get the article's page views (people, not bots) for the last 12 full months.
  3. Write them into players.json as "fame" (views per year; 0 = no article found).

Polite and resumable: a proper User-Agent, ~5 requests a second, retries with backoff on
429/5xx, and every answer is cached in cache/fame/, so a rerun only fetches what's missing.
Views older than 30 days are refreshed on the next run. Takes ~15-20 minutes the first time.

  python3 fetch_fame.py            # all players
  python3 fetch_fame.py --refresh  # refetch all views (e.g. monthly)

build_players_bulk.py and fill_details.py keep the "fame" field when they rewrite
players.json (they read it from cache/fame/).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import requests

USER_AGENT = "FromTheLogo/1.0 (https://github.com/lankyshoota/FromTheLogo; NBA trivia game, page-view lookups)"
WIKIDATA = os.environ.get("FTL_WIKIDATA", "https://query.wikidata.org/sparql")
WIKI_API = os.environ.get("FTL_WIKI_API", "https://en.wikipedia.org/w/api.php")
PAGEVIEWS = os.environ.get("FTL_PAGEVIEWS", "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article")
DELAY = 0.2                  # seconds between requests (~5/s; Wikimedia allows far more)
REFRESH_DAYS = 30
CACHE = Path("cache") / "fame"
PLAYERS = Path("players.json")

session = requests.Session()
session.headers["User-Agent"] = USER_AGENT


def get(url: str, params=None) -> dict | None:
    """GET JSON politely. None for 404 (e.g. no page views recorded). Retries 429/5xx/timeouts."""
    for attempt in range(8):
        try:
            r = session.get(url, params=params, timeout=60)
        except requests.exceptions.RequestException as e:
            wait = min(300, 5 * 2 ** attempt)
            print(f"   {e.__class__.__name__}, retrying in {wait}s")
            time.sleep(wait)
            continue
        if r.status_code == 404:
            time.sleep(DELAY)
            return None
        if r.status_code == 429 or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After") or 0) or min(300, 5 * 2 ** attempt)
            print(f"   HTTP {r.status_code}, waiting {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        time.sleep(DELAY + random.random() * 0.1)
        return r.json()
    sys.exit("Wikipedia isn't answering. Everything so far is cached; run again later.")


def plain(name: str) -> str:
    """lowercase, no accents, no punctuation: 'Nikola Jokić' -> 'nikola jokic'"""
    s = unicodedata.normalize("NFD", name).encode("ascii", "ignore").decode().lower()
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


def wikidata_titles() -> dict[int, str]:
    """NBA.com player id -> English Wikipedia title, for every player Wikidata links."""
    f = CACHE / "wikidata.json"
    if f.exists() and time.time() - f.stat().st_mtime < REFRESH_DAYS * 86400:
        return {int(k): v for k, v in json.loads(f.read_text()).items()}
    print("Matching NBA.com ids to Wikipedia articles (Wikidata)...")
    query = """
      SELECT ?nba ?article WHERE {
        ?item wdt:P3647 ?nba .
        ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> .
      }"""
    data = get(WIKIDATA, {"query": query, "format": "json"})
    out = {}
    for b in (data or {}).get("results", {}).get("bindings", []):
        nba, url = b["nba"]["value"], b["article"]["value"]
        if nba.isdigit():
            out[int(nba)] = requests.utils.unquote(url.rsplit("/wiki/", 1)[1]).replace("_", " ")
    f.write_text(json.dumps(out))
    print(f"  Wikidata links {len(out)} NBA.com ids to articles.")
    return out


def search_title(pid: int, name: str) -> str | None:
    """Wikipedia search for players Wikidata doesn't link. Cached (also when nothing's found)."""
    f = CACHE / f"search_{pid}.json"
    if f.exists():
        return json.loads(f.read_text()).get("title")
    data = get(WIKI_API, {"action": "query", "list": "search", "srsearch": f"{name} basketball",
                          "srlimit": 5, "format": "json", "formatversion": 2}) or {}
    title = None
    for hit in data.get("query", {}).get("search", []):
        base = hit["title"].split(" (")[0]
        about = (hit["title"] + " " + hit.get("snippet", "")).lower()
        if plain(base) == plain(name) and ("basketball" in about or "nba" in about):
            title = hit["title"]
            break
    f.write_text(json.dumps({"title": title}))
    return title


def views_last_year(title: str) -> int:
    end = date.today().replace(day=1) - timedelta(days=1)                  # last day of last month
    start = (end.replace(day=1) - timedelta(days=330)).replace(day=1)      # 12 full months
    url = (f"{PAGEVIEWS}/en.wikipedia/all-access/user/{quote(title.replace(' ', '_'), safe='')}"
           f"/monthly/{start:%Y%m%d}00/{end:%Y%m%d}00")
    data = get(url)
    return sum(item.get("views", 0) for item in (data or {}).get("items", []))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="refetch every player's views")
    args = ap.parse_args()
    if not PLAYERS.exists():
        sys.exit("players.json not found. Run this in the FromTheLogo folder.")
    CACHE.mkdir(parents=True, exist_ok=True)
    players = json.loads(PLAYERS.read_text())
    linked = wikidata_titles()

    todo = []
    for p in players:
        f = CACHE / f"views_{p['id']}.json"
        fresh = f.exists() and time.time() - f.stat().st_mtime < REFRESH_DAYS * 86400
        if args.refresh or not fresh:
            todo.append(p)
    print(f"{len(players)} players, {len(todo)} to look up.")

    found = {"wikidata": 0, "search": 0, "none": 0}
    t0 = time.time()
    try:
        for k, p in enumerate(todo, 1):
            pid = p["id"]
            title, source = linked.get(pid), "wikidata"
            if not title:
                title, source = search_title(pid, p["n"]), "search"
            views = views_last_year(title) if title else 0
            source = source if title else "none"
            found[source] += 1
            (CACHE / f"views_{pid}.json").write_text(json.dumps(
                {"title": title, "views": views, "source": source, "fetched": str(date.today())}))
            if k % 100 == 0 or k == len(todo):
                print(f"  {k}/{len(todo)}  ({p['n']}: {views:,} views)  {(time.time() - t0) / 60:.0f} min")
    except KeyboardInterrupt:
        print("\nStopped; merging what's done so far. Run again to continue.")
    merge(players)
    print(f"Matched by Wikidata {found['wikidata']}, by search {found['search']}, no article {found['none']} (this run).")


def merge(players):
    """Write "fame" (views in the last 12 months) into players.json for everyone with a cached lookup."""
    have = 0
    for p in players:
        f = CACHE / f"views_{p['id']}.json"
        if f.exists():
            p["fame"] = int(json.loads(f.read_text()).get("views") or 0)
            have += 1
    tmp = PLAYERS.with_name(PLAYERS.name + ".tmp")
    tmp.write_text(json.dumps(players, separators=(",", ":"), ensure_ascii=False))
    tmp.replace(PLAYERS)
    top = sorted(players, key=lambda p: -p.get("fame", 0))[:5]
    names = ", ".join("%s (%s)" % (p["n"], format(p.get("fame", 0), ",")) for p in top)
    print(f"\nplayers.json updated: fame for {have} of {len(players)} players. Most viewed: {names}")


if __name__ == "__main__":
    main()
