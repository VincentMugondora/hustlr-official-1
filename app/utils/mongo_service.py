from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import get_database


class MongoService:
    """MongoDB-backed service mirroring DynamoDBService interface.

    This lets the WhatsApp MessageHandler use MongoDB for users,
    providers, and bookings instead of DynamoDB.
    """

    # User operations
    async def get_user(self, whatsapp_number: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        return await db.users.find_one({"whatsapp_number": whatsapp_number})

    async def create_user(self, user_data: Dict[str, Any]) -> bool:
        db = get_database()
        user_data = dict(user_data)
        user_data.setdefault("registered_at", datetime.utcnow())
        user_data.setdefault("onboarding_completed", True)
        await db.users.insert_one(user_data)
        return True

    async def update_user(self, whatsapp_number: str, update_data: Dict[str, Any]) -> bool:
        db = get_database()
        result = await db.users.update_one(
            {"whatsapp_number": whatsapp_number},
            {"$set": update_data},
        )
        return result.matched_count > 0

    # Provider operations
    async def get_providers_by_service(self, service_type: str, location: Optional[str] = None) -> List[Dict[str, Any]]:
        db = get_database()
        query: Dict[str, Any] = {"service_type": service_type}
        if location:
            query["location"] = {"$regex": location, "$options": "i"}
        cursor = db.providers.find(query)
        return [doc async for doc in cursor]

    async def create_provider(self, provider_data: Dict[str, Any]) -> bool:
        db = get_database()
        provider_data = dict(provider_data)
        provider_data.setdefault("registered_at", datetime.utcnow())
        provider_data.setdefault("status", "pending")
        await db.providers.insert_one(provider_data)
        return True

    # Booking operations
    async def create_booking(self, booking_data: Dict[str, Any]) -> bool:
        db = get_database()
        booking_data = dict(booking_data)
        booking_data.setdefault("created_at", datetime.utcnow())
        booking_data.setdefault("status", "pending")
        await db.bookings.insert_one(booking_data)
        return True

    async def get_user_bookings(self, user_whatsapp_number: str) -> List[Dict[str, Any]]:
        db = get_database()
        cursor = db.bookings.find({"user_whatsapp_number": user_whatsapp_number})
        return [doc async for doc in cursor]

    async def update_booking_status(self, booking_id: str, status: str) -> bool:
        db = get_database()
        result = await db.bookings.update_one(
            {"booking_id": booking_id},
            {"$set": {"status": status}},
        )
        return result.matched_count > 0

    # Session operations
    async def get_session(self, whatsapp_number: str) -> Optional[Dict[str, Any]]:
        """Get user session"""
        db = get_database()
        return await db.sessions.find_one({"whatsapp_number": whatsapp_number})

    async def save_session(self, whatsapp_number: str, session_data: Dict[str, Any]) -> bool:
        """Save user session"""
        db = get_database()
        session_data = dict(session_data)
        session_data["whatsapp_number"] = whatsapp_number
        session_data["updated_at"] = datetime.utcnow()
        result = await db.sessions.update_one(
            {"whatsapp_number": whatsapp_number},
            {"$set": session_data},
            upsert=True,
        )
        return result.matched_count > 0 or result.upserted_id is not None

    async def delete_session(self, whatsapp_number: str) -> bool:
        """Delete user session"""
        db = get_database()
        result = await db.sessions.delete_one({"whatsapp_number": whatsapp_number})
        return result.deleted_count > 0
