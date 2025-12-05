import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.db import connect_to_mongo, close_mongo_connection, get_database  # noqa: E402
from app.utils.places_importer import _normalize_phone_to_whatsapp  # noqa: E402


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            v = value.replace("Z", "+00:00")
            return datetime.fromisoformat(v)
        except Exception:
            pass
    return datetime.now(timezone.utc)


async def import_file(path: Path, service_type_override: str | None, default_status: str) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON must be an array of provider objects")

    await connect_to_mongo()
    try:
        db = get_database()
        inserted = 0
        updated = 0
        skipped = 0
        sample: List[Dict[str, Any]] = []

        for it in data:
            if not isinstance(it, dict):
                skipped += 1
                continue
            name = (it.get("name") or "").strip()
            if not name:
                skipped += 1
                continue
            contact = (it.get("contact") or "").strip()
            whatsapp = (it.get("whatsapp_number") or "").strip()
            if not whatsapp:
                whatsapp = _normalize_phone_to_whatsapp(contact)

            if not whatsapp:
                whatsapp = f"json:{name.lower()}:{hash(name) & 0xffff}"
            s_type = service_type_override or (it.get("service_type") or "service")
            status = default_status if whatsapp and not whatsapp.startswith("json:") else "pending"

            doc = {
                "whatsapp_number": whatsapp,
                "name": name,
                "service_type": s_type,
                "location": (it.get("location") or "").strip(),
                "business_name": (it.get("business_name") or name).strip(),
                "contact": contact or None,
                "status": (it.get("status") or status),
                "registered_at": parse_dt(it.get("registered_at")),
            }

            unique = {"whatsapp_number": whatsapp}
            existing = await db.providers.find_one(unique)
            if existing:
                await db.providers.update_one({"_id": existing["_id"]}, {"$set": doc})
                updated += 1
            else:
                await db.providers.insert_one(doc)
                inserted += 1

            if len(sample) < 5:
                sample.append({"name": doc["name"], "whatsapp_number": doc["whatsapp_number"], "service_type": doc["service_type"]})

        return {
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "sample": sample,
            "total_processed": inserted + updated + skipped,
        }
    finally:
        await close_mongo_connection()


async def main() -> None:
    p = argparse.ArgumentParser(description="Import providers from a JSON file into MongoDB.")
    p.add_argument("--file", required=True, help="Path to providers JSON array")
    p.add_argument("--service-type", default=None, help="Override service type for all records")
    p.add_argument("--status", default="active", help="Default status for records with WhatsApp numbers")
    args = p.parse_args()

    stats = await import_file(Path(args.file), args.service_type, args.status)
    print(json.dumps({"file": args.file, "db_stats": stats}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
