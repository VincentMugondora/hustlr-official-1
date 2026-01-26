import sys
import json
import asyncio
import os

# Ensure project root on path
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from app.db import connect_to_mongo, get_database  # noqa: E402


async def main(msisdn: str | None = None):
    await connect_to_mongo()
    db = get_database()
    if msisdn:
        doc = await db.sessions.find_one({"whatsapp_number": msisdn})
        if doc and "_id" in doc:
            doc["_id"] = str(doc["_id"])
        print(json.dumps(doc or {}, default=str, indent=2))
    else:
        cur = db.sessions.find({}).sort("updated_at", -1).limit(50)
        items = [doc async for doc in cur]
        for d in items:
            if "_id" in d:
                d["_id"] = str(d["_id"])
        print(json.dumps(items, default=str, indent=2))


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(main(arg))
