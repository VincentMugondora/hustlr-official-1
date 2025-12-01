import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List
import os
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pymongo import MongoClient

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from config import settings


def create_provider(
    whatsapp_number: str,
    name: str,
    service_type: str,
    location: str,
    business_name: str | None = None,
    contact: str | None = None,
    short_description: str | None = None,
) -> Dict[str, Any]:
    registered_at = datetime.now(timezone.utc).isoformat()
    return {
        "whatsapp_number": whatsapp_number,
        "name": name,
        "service_type": service_type,
        "location": location,
        "business_name": business_name,
        "contact": contact,
        "short_description": short_description,
        "status": "active",
        "registered_at": registered_at,
    }


def _extract_phone(text: str) -> str:
    match = re.search(r"\+?\d[\d\s\-]{7,}", text)
    return match.group(0).strip() if match else ""


def _extract_email(text: str) -> str:
    match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
    return match.group(0).strip() if match else ""


def _extract_website(text: str) -> str:
    match = re.search(r"https?://[\w./\-]+", text)
    return match.group(0).strip() if match else ""


def scrape_classifieds(service_type: str, location_hint: str, url: str) -> List[Dict[str, Any]]:
    providers: List[Dict[str, Any]] = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        response = requests.get(url, headers=headers, timeout=15)
    except Exception as exc:
        print(f"Error fetching {url}: {exc}")
        return providers

    if response.status_code == 403:
        print(f"Skipping classifieds scrape; got 403 Forbidden for {url}")
        return providers

    if not response.ok:
        print(f"Skipping classifieds scrape; HTTP {response.status_code} for {url}")
        return providers

    soup = BeautifulSoup(response.text, "html.parser")

    cards = soup.select(".listing, .card, article, .item")
    for card in cards:
        title_el = card.select_one("h3, h2, .listing-title, .card-title")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)

        body_text = card.get_text(" ", strip=True)
        location_el = card.select_one(".location, .listing-location")
        location = location_el.get_text(strip=True) if location_el else location_hint

        phone = _extract_phone(body_text)
        email = _extract_email(body_text)
        website = _extract_website(body_text)

        pieces = []
        if phone:
            pieces.append(f"Phone: {phone}")
        if email:
            pieces.append(f"Email: {email}")
        if website:
            pieces.append(f"Website: {website}")
        contact = " | ".join(pieces) if pieces else ""

        description_el = card.select_one(".description, .listing-description, p")
        description = description_el.get_text(strip=True) if description_el else ""

        whatsapp_number = phone or ""
        if not whatsapp_number:
            continue

        provider = create_provider(
            whatsapp_number=whatsapp_number,
            name=title,
            service_type=service_type,
            location=location,
            business_name=title,
            contact=contact or None,
            short_description=description or None,
        )
        providers.append(provider)

    return providers


def scrape_pindula(service_type: str, location_hint: str, url: str) -> List[Dict[str, Any]]:
    providers: List[Dict[str, Any]] = []
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    cards = soup.select(".service, .card, article, li")
    for card in cards:
        title_el = card.select_one("h3, h2, .title, a")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)

        body_text = card.get_text(" ", strip=True)
        phone = _extract_phone(body_text)
        email = _extract_email(body_text)
        website = _extract_website(body_text)

        pieces = []
        if phone:
            pieces.append(f"Phone: {phone}")
        if email:
            pieces.append(f"Email: {email}")
        if website:
            pieces.append(f"Website: {website}")
        contact = " | ".join(pieces) if pieces else ""

        description_el = card.select_one("p")
        description = description_el.get_text(strip=True) if description_el else ""

        whatsapp_number = phone or ""
        if not whatsapp_number:
            continue

        location = location_hint

        provider = create_provider(
            whatsapp_number=whatsapp_number,
            name=title,
            service_type=service_type,
            location=location,
            business_name=title,
            contact=contact or None,
            short_description=description or None,
        )
        providers.append(provider)

    return providers


def save_to_mongo(providers: List[Dict[str, Any]]) -> None:
    if not providers:
        return
    client = MongoClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]
    collection = db["providers"]
    collection.insert_many(providers)


def main() -> None:
    service_type = "plumber"
    location_hint = "Harare"
    classifieds_url = "https://www.classifieds.co.zw/"
    pindula_url = "https://www.pindula.co.zw/services"

    all_providers: List[Dict[str, Any]] = []
    # all_providers.extend(scrape_classifieds(service_type, location_hint, classifieds_url))
    all_providers.extend(scrape_pindula(service_type, location_hint, pindula_url))

    print(json.dumps(all_providers, indent=2))
    save_to_mongo(all_providers)


# Example usage
if __name__ == "__main__":
    main()
