import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx
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
