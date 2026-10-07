#!/usr/bin/env python3
"""
RedBus ratings scraper for KPN Fleet (operator 30995).
Requires REDBUS_COOKIE env var (session cookie string from browser).

API body format discovered via network interception:
  POST /win/api/ratingsReviews/getRatingAndReviews/{pageNum}
  Body: {"filterBy":{"operatorId":"30995","startDateInLong":YYYYMMDD,"endDateInLong":YYYYMMDD,"country":"IND","status":"all"},"sortBy":{"doj":true},"orderBy":{"order":"DESC"},"countValue":true}
  Returns: {"count": N, "report": [...]}
"""

import json
import os
import sys
import time
import datetime
import urllib.request
import urllib.error
import ssl

OPERATOR_ID = "30995"
INGEST_URL = "https://kpnfleet.com/api/ratings/ingest"
INGEST_KEY = "kpn_scraper_2024"
REDBUS_COOKIE = os.environ.get("REDBUS_COOKIE", "")
REDBUS_BASE = "https://www.redbus.pro"


def today_int() -> int:
    return int(datetime.date.today().strftime("%Y%m%d"))


def fetch_page(page_num: int, start_date: int, end_date: int, get_count: bool = False) -> dict:
    url = f"{REDBUS_BASE}/win/api/ratingsReviews/getRatingAndReviews/{page_num}"
    body = json.dumps({
        "filterBy": {
            "operatorId": OPERATOR_ID,
            "startDateInLong": start_date,
            "endDateInLong": end_date,
            "country": "IND",
            "status": "all",
        },
        "sortBy": {"doj": True},
        "orderBy": {"order": "DESC"},
        "countValue": get_count,
    }).encode()

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": REDBUS_BASE,
        "Referer": f"{REDBUS_BASE}/win/rnr/globalRatingsAndReviews",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    }
    if REDBUS_COOKIE:
        headers["Cookie"] = REDBUS_COOKIE

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        if e.code in (401, 403):
            print(f"  AUTH ERROR ({e.code}) — REDBUS_COOKIE has expired. Update the GitHub secret.")
            sys.exit(2)
        print(f"  HTTP {e.code} on page {page_num}: {err[:200]}")
        return {}
    except Exception as e:
        print(f"  Error on page {page_num}: {e}")
        return {}


def scrape_all(start_date: int = 20230101) -> list:
    end_date = today_int()
    all_ratings = []

    # Page 0 with count
    data = fetch_page(0, start_date, end_date, get_count=True)
    if not data:
        print("  Failed to fetch first page")
        return []

    # Detect auth failure via empty/unexpected response
    if "count" not in data and "report" not in data:
        print(f"  AUTH ERROR — unexpected response (cookie likely expired): {str(data)[:200]}")
        sys.exit(2)

    total = data.get("count", 0)
    report = data.get("report", [])
    all_ratings.extend(report)
    print(f"  Total reported: {total}, page 0: {len(report)} records")

    if not total:
        return all_ratings

    page = 1
    while len(all_ratings) < total:
        data = fetch_page(page, start_date, end_date, get_count=False)
        report = data.get("report", [])
        if not report:
            break
        all_ratings.extend(report)
        print(f"  Page {page}: {len(report)} records (total so far: {len(all_ratings)})")
        page += 1
        time.sleep(0.3)

    return all_ratings


def map_rating(r: dict) -> dict:
    return {
        "pnr": r.get("pnr") or "",
        "passenger_name": r.get("name") or "",
        "phone": str(r.get("mobileNo") or ""),
        "source": r.get("sourceLocation") or "",
        "destination": r.get("destinationLocation") or "",
        "travel_date": r.get("dojInLocal") or "",
        "rating": (r.get("ratingData") or {}).get("value") or 0,
        "review_text": ((r.get("reviewData") or {}).get("description") or
                        (r.get("reviewData") or {}).get("comment") or ""),
        "status": r.get("reviewStatus") or "",
    }


def post_to_vps(ratings: list) -> dict:
    body = json.dumps({"key": INGEST_KEY, "ratings": ratings}).encode()
    req = urllib.request.Request(
        INGEST_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read())


def main():
    print("=== RedBus Ratings Scraper ===")
    print(f"Cookie present: {'yes (' + str(len(REDBUS_COOKIE)) + ' chars)' if REDBUS_COOKIE else 'NO - will likely get 401/403'}")

    if not REDBUS_COOKIE:
        print("REDBUS_COOKIE secret is not set — update it in GitHub repo secrets.")
        sys.exit(2)

    raw = scrape_all(start_date=20230101)
    print(f"\nTotal scraped: {len(raw)}")

    if not raw:
        print("Nothing to ingest — check REDBUS_COOKIE secret.")
        sys.exit(1)

    ratings = [map_rating(r) for r in raw]

    total_new = total_updated = 0
    batch_size = 200
    for i in range(0, len(ratings), batch_size):
        batch = ratings[i:i + batch_size]
        print(f"Posting batch {i // batch_size + 1} ({len(batch)} records)...")
        try:
            result = post_to_vps(batch)
            total_new += result.get("rows_new", 0)
            total_updated += result.get("rows_updated", 0)
        except Exception as e:
            print(f"  Ingest error: {e}")

    print(f"\n✓ Done — {total_new} new, {total_updated} updated")


if __name__ == "__main__":
    main()
