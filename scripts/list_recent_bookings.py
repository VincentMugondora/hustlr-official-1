import sys
import asyncio
from app.db import connect_to_mongo, get_database

async def main(msisdn: str):
    await connect_to_mongo()
    db = get_database()
    cur = db.bookings.find({"user_whatsapp_number": msisdn}).sort("created_at", -1).limit(5)
    items = [doc async for doc in cur]
    print(f"Recent bookings for {msisdn}:")
    for b in items:
        print({
            "booking_id": b.get("booking_id"),
            "service_type": b.get("service_type"),
            "location": b.get("location"),
            "date_time": b.get("date_time"),
            "status": b.get("status"),
            "provider_whatsapp_number": b.get("provider_whatsapp_number"),
        })

if __name__ == "__main__":
    msisdn = sys.argv[1] if len(sys.argv) > 1 else "263771234567"
    asyncio.run(main(msisdn))
