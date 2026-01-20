import sys
import json
import asyncio
from app.db import connect_to_mongo, get_database


async def main(msisdn: str | None = None):
    await connect_to_mongo()
    db = get_database()
    query = {}
    if msisdn and msisdn.lower() != "all":
        query = {"user_whatsapp_number": msisdn}
    cur = db.bookings.find(query).sort("created_at", -1).limit(50)
    items = [doc async for doc in cur]
    for d in items:
        if "_id" in d:
            d["_id"] = str(d["_id"])
    print(f"Count: {len(items)}")
    print(json.dumps(items, default=str, indent=2))


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(arg))
