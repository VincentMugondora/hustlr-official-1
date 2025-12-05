import asyncio
import os
import sys
from typing import List

# Ensure project root on sys.path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.db import connect_to_mongo, close_mongo_connection, get_database  # noqa: E402


SPURIOUS_NAMES: List[str] = [
    "Share",
    "share",
    "Website",
    "website",
    "Directions",
    "directions",
    "Results",
    "results",
    "Share",
]


async def main() -> None:
    await connect_to_mongo()
    try:
        db = get_database()
        # Delete documents that are clearly UI artifacts from text import
        query = {
            "$or": [
                {"name": {"$in": SPURIOUS_NAMES}},
                {"whatsapp_number": {"$regex": r"^text:(?:.*)?share", "$options": "i"}},
            ]
        }
        res = await db.providers.delete_many(query)
        print({"deleted": res.deleted_count})
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(main())
