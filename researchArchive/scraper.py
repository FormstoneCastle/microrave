"""
SSB Productions Archive Scraper
Scrapes http://ssbproductions.com/archive/ and all linked gallery pages.
Saves event metadata + full-res photo URLs + captions to data.json.

User-Agent attribution per Mike B's request:
  MikeB-ResearchScraper/1.0 - Scraping for personal viewing and enjoyment.
  Thank you SideShow Bob! - Mike B, mike@formstonecastleart.com
"""

import requests
from bs4 import BeautifulSoup
import json
import time
import os
import sys
from datetime import datetime

ARCHIVE_URL = "http://ssbproductions.com/archive/"
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "data.json")

HEADERS = {
    "User-Agent": (
        "MikeB-ResearchScraper/1.0 - Scraping for personal viewing and enjoyment. "
        "Thank you SideShow Bob! - Mike B, mike@formstonecastleart.com"
    )
}

SKIP_URLS = {
    "http://ssbproductions.com/",
    "http://ssbproductions.com/index.html",
    "http://ssbproductions.com/action/",
    "http://ssbproductions.com/action/index.html",
}


def fetch(url):
    """Fetch a URL and return (status_code, content_length, html). Never raises."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        return r.status_code, len(r.content), r.text
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return 0, 0, ""


def scrape_index():
    """Return list of {name, date, url} from the archive index page."""
    print("Fetching archive index...")
    _, _, html = fetch(ARCHIVE_URL)
    soup = BeautifulSoup(html, "html.parser")

    events = []
    seen_urls = set()

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        link = cells[0].find("a", href=True)
        if not link:
            continue
        href = link["href"]
        # Make absolute
        if not href.startswith("http"):
            href = "http://ssbproductions.com" + href
        # Skip nav and anchor links
        if href in SKIP_URLS or "#" in href or "/archive" in href:
            continue
        if href in seen_urls:
            continue
        seen_urls.add(href)

        name = link.get_text(strip=True)
        date = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        events.append({"name": name, "date": date, "url": href})

    print(f"Found {len(events)} events in index.")
    return events


def scrape_gallery_page(url):
    """
    Fetch one gallery HTML page. Return list of {thumb_url, full_url, caption}.
    Handles the tn-prefix thumbnail convention.
    """
    _, size, html = fetch(url)
    if size == 0:
        return None  # broken page

    soup = BeautifulSoup(html, "html.parser")
    photos = []

    for img in soup.find_all("img", alt="Picture"):
        src = img.get("src", "")
        if not src:
            continue
        # Make absolute
        if not src.startswith("http"):
            base = url.rstrip("/")
            src = base + "/" + src.lstrip("/")

        # Derive full-res URL by stripping 'tn' prefix from filename
        parts = src.rsplit("/", 1)
        filename = parts[-1]
        if filename.lower().startswith("tn"):
            full_filename = filename[2:]
            full_url = parts[0] + "/" + full_filename
        else:
            full_url = src  # no thumbnail pattern, use as-is

        # Caption: sibling td text in same row
        row = img.find_parent("tr")
        caption = ""
        if row:
            tds = row.find_all("td")
            # Caption is usually in the second td
            for td in tds:
                text = td.get_text(strip=True)
                if text:
                    caption = text
                    break

        photos.append({
            "thumb": src,
            "full": full_url,
            "caption": caption,
        })

    return photos


def find_pagination(url, html):
    """
    Return list of additional page URLs (index2.html, index3.html, etc.)
    found in the gallery HTML.
    """
    soup = BeautifulSoup(html, "html.parser")
    base = url.rstrip("/")
    extras = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Look for index2.html, index3.html pattern
        if href.startswith("index") and href.endswith(".html") and href != "index.html":
            page_url = base + "/" + href
            if page_url not in extras:
                extras.append(page_url)
    return extras


def scrape_event(event):
    """
    Given an event dict with a url, determine if it's alive and scrape all photos.
    Returns updated event dict with status + photos added.
    """
    url = event["url"]
    print(f"  Probing: {url}")

    status_code, size, html = fetch(url)

    if size == 0:
        print(f"    -> BROKEN (empty response)")
        return {**event, "status": "broken", "photos": []}

    # Find pagination links in the first page
    extra_pages = find_pagination(url, html)

    # Scrape first page
    photos = []
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img", alt="Picture"):
        src = img.get("src", "")
        if not src:
            continue
        if not src.startswith("http"):
            base = url.rstrip("/")
            src = base + "/" + src.lstrip("/")
        parts = src.rsplit("/", 1)
        filename = parts[-1]
        if filename.lower().startswith("tn"):
            full_url = parts[0] + "/" + filename[2:]
        else:
            full_url = src
        row = img.find_parent("tr")
        caption = ""
        if row:
            for td in row.find_all("td"):
                text = td.get_text(strip=True)
                if text:
                    caption = text
                    break
        photos.append({"thumb": src, "full": full_url, "caption": caption})

    # Scrape additional pages
    for page_url in extra_pages:
        print(f"    + pagination page: {page_url}")
        time.sleep(1)
        page_photos = scrape_gallery_page(page_url)
        if page_photos:
            photos.extend(page_photos)

    print(f"    -> ALIVE — {len(photos)} photos")
    return {**event, "status": "alive", "photos": photos}


def load_existing():
    """Load existing data.json if present (for resume support)."""
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"scraped_at": None, "events": []}


def save(data):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def main():
    data = load_existing()
    already_done = {e["url"] for e in data.get("events", [])}

    if already_done:
        print(f"Resuming — {len(already_done)} events already scraped.")

    events_index = scrape_index()
    # Filter to events not yet scraped
    todo = [e for e in events_index if e["url"] not in already_done]
    print(f"{len(todo)} events remaining to scrape.\n")

    for i, event in enumerate(todo):
        print(f"[{i+1}/{len(todo)}] {event['name']} ({event['date']})")
        result = scrape_event(event)
        data["events"].append(result)
        data["scraped_at"] = datetime.utcnow().isoformat()
        save(data)
        # Brief pause between events — polite but not slow
        time.sleep(1.5)

    print(f"\nDone. {len(data['events'])} total events saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
