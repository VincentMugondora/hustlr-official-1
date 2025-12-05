import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
from urllib.parse import urlparse, parse_qs, unquote
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import settings

logger = logging.getLogger(__name__)

GOOGLE_TEXTSEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
GOOGLE_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"


def _normalize_phone_to_whatsapp(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    # Keep digits only
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    # Normalize Zimbabwe numbers
    # If starts with 00<country>, trim leading zeros
    if digits.startswith("00"):
        digits = digits[2:]
    # If starts with 0 and length >= 9, assume local and convert to +263
    if digits.startswith("0") and not digits.startswith("00"):
        digits = "263" + digits[1:]
    # If not starts with country code and length seems too short, just return as-is
    return digits


def _infer_service_type_from_types(types: List[str]) -> Optional[str]:
    tset = set(types or [])
    # Direct mappings
    if "doctor" in tset or "hospital" in tset or "physiotherapist" in tset or "dentist" in tset:
        return "doctor"
    if "plumber" in tset:
        return "plumber"
    if "electrician" in tset:
        return "electrician"
    if "painter" in tset:
        return "painter"
    if "carpenter" in tset or "furniture_store" in tset:
        return "carpenter"
    if "laundry" in tset or "cleaners" in tset:
        return "cleaner"
    return None


async def _places_text_search(client: httpx.AsyncClient, query: str, limit: int = 20) -> List[Dict[str, Any]]:
    params = {"query": query, "key": settings.GOOGLE_PLACES_API_KEY}
    results: List[Dict[str, Any]] = []
    r = await client.get(GOOGLE_TEXTSEARCH_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    results.extend(data.get("results", []))
    # For simplicity, do not follow next_page_token here (rate-limit sensitive)
    return results[:limit]


async def _place_details(client: httpx.AsyncClient, place_id: str) -> Dict[str, Any]:
    fields = (
        "name,formatted_address,formatted_phone_number,international_phone_number,types,website"
    )
    params = {"place_id": place_id, "fields": fields, "key": settings.GOOGLE_PLACES_API_KEY}
    r = await client.get(GOOGLE_DETAILS_URL, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    return data.get("result", {})


async def import_places_to_db(
    db: AsyncIOMotorDatabase,
    query: str,
    service_type_override: Optional[str] = None,
    status: str = "active",
    limit: int = 20,
) -> Dict[str, Any]:
    if not settings.GOOGLE_PLACES_API_KEY:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not configured in environment.")

    inserted = 0
    updated = 0
    skipped = 0
    items: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        search_results = await _places_text_search(client, query, limit=limit)
        for item in search_results:
            place_id = item.get("place_id")
            if not place_id:
                skipped += 1
                continue

            details = await _place_details(client, place_id)
            name = details.get("name") or item.get("name")
            address = details.get("formatted_address") or item.get("formatted_address")
            types = details.get("types") or item.get("types") or []
            intl_phone = details.get("international_phone_number")
            local_phone = details.get("formatted_phone_number")
            phone = intl_phone or local_phone
            whatsapp_number = _normalize_phone_to_whatsapp(phone)

            service_type = service_type_override or _infer_service_type_from_types(types) or "service"

            # If WhatsApp number not resolvable, mark as pending so it's not booked accidentally
            effective_status = status if whatsapp_number else "pending"

            provider_doc = {
                "whatsapp_number": whatsapp_number or f"place:{place_id}",
                "name": name or "Unknown",
                "service_type": service_type,
                "location": address or "",
                "business_name": name,
                "contact": phone,
                "status": effective_status,
                "registered_at": __import__("datetime").datetime.utcnow(),
            }

            # Upsert by whatsapp_number (or place_id fallback) + name
            unique_key = {"whatsapp_number": provider_doc["whatsapp_number"]}
            existing = await db.providers.find_one(unique_key)
            if existing:
                await db.providers.update_one({"_id": existing["_id"]}, {"$set": provider_doc})
                updated += 1
            else:
                await db.providers.insert_one(provider_doc)
                inserted += 1

            items.append({"name": provider_doc["name"], "whatsapp_number": provider_doc["whatsapp_number"], "service_type": service_type})

    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "sample": items[:5],
        "total_processed": len(items) + skipped,
    }


async def extract_query_from_maps_link(maps_url: str) -> Optional[str]:
    """Resolve a Google Maps short link and extract a text query suitable for Places Text Search.

    Supports patterns like:
    - https://maps.app.goo.gl/...
    - https://goo.gl/maps/...
    - https://www.google.com/maps/search/<query>
    - https://www.google.com/maps?q=<query>
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
            resp = await client.get(maps_url)
            resp.raise_for_status()
            final_url = str(resp.url)

        parsed = urlparse(final_url)
        # If query param 'q' present, prefer it
        q = parse_qs(parsed.query).get('q')
        if q and q[0]:
            return unquote(q[0]).replace('+', ' ').strip()

        # If path contains /maps/search/<query>
        # Example: /maps/search/doctors+in+zimbabwe/@-17.87,30.90,13z
        if '/maps/search/' in parsed.path:
            after = parsed.path.split('/maps/search/', 1)[1]
            # Stop at next slash or use full remainder
            query_part = after.split('/', 1)[0]
            return unquote(query_part).replace('+', ' ').strip()

        # If path is /maps and query contains 'query=' param (some variants)
        qp = parse_qs(parsed.query).get('query')
        if qp and qp[0]:
            return unquote(qp[0]).replace('+', ' ').strip()

        # Fallback: try to use the whole path part as best-effort
        return None
    except Exception as e:
        logger.warning(f"Failed to extract query from maps link: {e}")
        return None


def _safe_strip(s: Optional[str]) -> str:
    return (s or "").strip()


def parse_text_providers(text: str) -> List[Dict[str, Any]]:
    """Parse a pasted Google Maps results text dump into provider dicts.

    Expected patterns per entry (flexible):
      Name
      4.8(10)
      Category · address
      Open · Closes 5 pm · <phone>    OR   Open 24 hours · <phone>
      [Website]
      [Directions]
      ["Quoted review"]

    Returns list of dicts with keys: name, category, address, phone, rating, review_count, note
    """
    lines = [l.strip() for l in (text or "").splitlines()]
    items: List[Dict[str, Any]] = []
    buf: Dict[str, Any] = {}

    def flush():
        nonlocal buf
        if buf.get("name"):
            items.append(buf)
        buf = {}

    # UI artifacts to skip from pasted Google Maps pages
    skip_markers_text = {"results", "share", "website", "directions"}
    skip_markers_icons = {"", "", ""}

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        low = line.lower()
        if not line:
            # blank line as separator
            if buf.get("name"):
                flush()
            i += 1
            continue
        # skip obvious UI markers (icons and their text forms)
        normalized = re.sub(r"[^a-z]+", "", low)
        if (
            line in skip_markers_icons
            or low in skip_markers_icons
            or low in skip_markers_text
            or normalized in skip_markers_text
        ):
            i += 1
            continue
        # quoted review
        if (low.startswith("\"") and low.endswith("\"")) or (low.startswith("“") and low.endswith("”")):
            buf["note"] = line.strip('"“”')
            i += 1
            continue

        # rating pattern like 4.5(10)
        m = re.match(r"^(?P<rating>[0-9]+(?:\.[0-9]+)?)\((?P<count>\d+)\)$", line)
        if m:
            buf["rating"] = float(m.group("rating"))
            buf["review_count"] = int(m.group("count"))
            i += 1
            continue

        # open/hours + phone line e.g. "Open · Closes 5 pm · 078 307 2110" or "Open 24 hours · 08677 ..."
        if low.startswith("open ") or low.startswith("open"):
            # extract phone by digits
            phone_digits = re.findall(r"[+]?\d[\d\s\-()]{5,}\d", line)
            if phone_digits:
                buf["phone"] = phone_digits[-1]
            buf["hours"] = line
            i += 1
            continue

        # category and address: often "Medical clinic · 20 Lanark Rd" with star marker variants removed
        if "·" in line and not buf.get("category"):
            parts = [p.strip(" ·\u272e\u2605\u273f\u2730") for p in line.split("·") if p.strip()]
            if parts:
                buf["category"] = parts[0]
                if len(parts) >= 2:
                    buf["address"] = parts[-1]
            i += 1
            continue

        # assume any other non-empty line that is not captured is a new Name
        # if there's already a name and we hit another strong name, flush
        if buf.get("name"):
            # start of a new entry
            flush()
        # guard against UI artifacts being treated as names
        name_norm = re.sub(r"[^a-z]+", "", low)
        if name_norm in skip_markers_text or line in skip_markers_icons:
            i += 1
            continue
        buf["name"] = line
        i += 1

    # final flush
    if buf.get("name"):
        flush()

    return items


async def import_text_to_db(
    db: AsyncIOMotorDatabase,
    text: str,
    service_type_override: Optional[str] = None,
    status: str = "active",
) -> Dict[str, Any]:
    """Parse provided text and upsert into providers collection.

    If phone cannot be normalized to WhatsApp number, mark status 'pending'.
    """
    parsed = parse_text_providers(text)
    inserted = 0
    updated = 0
    skipped = 0
    sample: List[Dict[str, Any]] = []

    for it in parsed:
        name = _safe_strip(it.get("name"))
        if not name:
            skipped += 1
            continue
        phone = _safe_strip(it.get("phone"))
        whatsapp = _normalize_phone_to_whatsapp(phone)
        address = _safe_strip(it.get("address"))
        category = (_safe_strip(it.get("category")) or "").lower()
        # choose service type
        s_type = service_type_override or ("doctor" if category else "service")
        eff_status = status if whatsapp else "pending"
        doc = {
            "whatsapp_number": whatsapp or f"text:{name.lower()}:{hash(name) & 0xffff}",
            "name": name,
            "service_type": s_type,
            "location": address,
            "business_name": name,
            "contact": phone or None,
            "status": eff_status,
            "registered_at": __import__("datetime").datetime.utcnow(),
            "meta": {
                "category": it.get("category"),
                "rating": it.get("rating"),
                "review_count": it.get("review_count"),
                "hours": it.get("hours"),
                "note": it.get("note"),
                "source": "text_import",
            },
        }

        unique = {"whatsapp_number": doc["whatsapp_number"]}
        existing = await db.providers.find_one(unique)
        if existing:
            await db.providers.update_one({"_id": existing["_id"]}, {"$set": doc})
            updated += 1
        else:
            await db.providers.insert_one(doc)
            inserted += 1

        if len(sample) < 5:
            sample.append({"name": doc["name"], "whatsapp_number": doc["whatsapp_number"], "service_type": doc["service_type"]})

    return {"inserted": inserted, "updated": updated, "skipped": skipped, "sample": sample, "total_processed": len(parsed)}
