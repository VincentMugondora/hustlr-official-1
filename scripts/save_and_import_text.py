import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

# Ensure project root is on sys.path so 'app' imports work when running this script directly
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.db import connect_to_mongo, close_mongo_connection, get_database
from app.utils.places_importer import (
    parse_text_providers,
    import_text_to_db,
    _normalize_phone_to_whatsapp,
)


def build_provider_docs(items: List[Dict[str, Any]], service_type: str, default_status: str) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for it in items:
        name = (it.get("name") or "").strip()
        if not name:
            continue
        phone = (it.get("phone") or "").strip()
        whatsapp = _normalize_phone_to_whatsapp(phone)
        address = (it.get("address") or "").strip()
        status = default_status if whatsapp else "pending"
        docs.append({
            "whatsapp_number": whatsapp or f"text:{name.lower()}:{hash(name) & 0xffff}",
            "name": name,
            "service_type": service_type,
            "location": address,
            "business_name": name,
            "contact": phone or None,
            "status": status,
            "registered_at": datetime.utcnow().isoformat(),
        })
    return docs


async def main() -> None:
    parser = argparse.ArgumentParser(description="Save parsed providers JSON and import into MongoDB.")
    parser.add_argument("--input", required=True, help="Path to the text file with pasted Google Maps results")
    parser.add_argument("--json-out", required=True, help="Path to write the parsed providers JSON")
    parser.add_argument("--service-type", default="doctor", help="Service type to assign (default: doctor)")
    parser.add_argument("--status", default="active", help="Default status for providers with WhatsApp numbers (default: active)")

    args = parser.parse_args()

    input_path = Path(args.input)
    json_out_path = Path(args.json_out)

    text = input_path.read_text(encoding="utf-8")
    items = parse_text_providers(text)

    # Save parsed provider docs JSON (normalized fields)
    provider_docs = build_provider_docs(items, args.service_type, args.status)
    json_out_path.parent.mkdir(parents=True, exist_ok=True)
    json_out_path.write_text(json.dumps(provider_docs, ensure_ascii=False, indent=2), encoding="utf-8")

    # Import into MongoDB
    await connect_to_mongo()
    try:
        db = get_database()
        stats = await import_text_to_db(db, text=text, service_type_override=args.service_type, status=args.status)
    finally:
        await close_mongo_connection()

    print(json.dumps({
        "saved_json": str(json_out_path),
        "count_json": len(provider_docs),
        "db_stats": stats,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
