from datetime import datetime
from typing import Any, Dict, List, Optional
from bson import ObjectId

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

    async def get_provider_by_whatsapp(self, whatsapp_number: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        return await db.providers.find_one({"whatsapp_number": whatsapp_number})

    async def get_provider_by_id(self, provider_id: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        try:
            oid = ObjectId(provider_id)
        except Exception:
            return None
        return await db.providers.find_one({"_id": oid})

    async def get_provider_by_phone(self, phone: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        return await db.providers.find_one({"whatsapp_number": phone})

    async def update_provider_status(self, provider_id: str, status: str) -> bool:
        db = get_database()
        try:
            oid = ObjectId(provider_id)
        except Exception:
            return False
        result = await db.providers.update_one(
            {"_id": oid},
            {"$set": {"status": status, "updated_at": datetime.utcnow()}},
        )
        return result.matched_count > 0

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

    async def get_booking_by_id(self, booking_id: str) -> Optional[Dict[str, Any]]:
        db = get_database()
        return await db.bookings.find_one({"booking_id": booking_id})

    async def update_booking_time(self, booking_id: str, new_time_text: str, set_status: Optional[str] = None) -> bool:
        db = get_database()
        update: Dict[str, Any] = {"date_time": new_time_text, "updated_at": datetime.utcnow()}
        if set_status:
            update["status"] = set_status
        result = await db.bookings.update_one(
            {"booking_id": booking_id},
            {"$set": update},
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

    # Conversation history operations
    async def store_message(self, whatsapp_number: str, role: str, text: str) -> bool:
        """Store a single message in conversation history.
        
        Args:
            whatsapp_number: User's WhatsApp number
            role: "user" or "assistant"
            text: Message text
        """
        db = get_database()
        message = {
            "whatsapp_number": whatsapp_number,
            "role": role,
            "text": text,
            "timestamp": datetime.utcnow(),
        }
        await db.conversation_history.insert_one(message)
        return True

    async def get_conversation_history(self, whatsapp_number: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve recent conversation history for a user.
        
        Args:
            whatsapp_number: User's WhatsApp number
            limit: Maximum number of messages to retrieve
            
        Returns:
            List of messages in format [{"role": "user"/"assistant", "text": "..."}]
        """
        db = get_database()
        cursor = db.conversation_history.find(
            {"whatsapp_number": whatsapp_number}
        ).sort("timestamp", -1).limit(limit)
        
        messages = [doc async for doc in cursor]
        # Reverse to get chronological order (oldest first)
        messages.reverse()
        
        # Return in the format expected by GeminiService
        return [{"role": msg["role"], "text": msg["text"]} for msg in messages]

    async def store_incoming_message(self, message_data: Dict[str, Any]) -> Any:
        db = get_database()
        data = dict(message_data)
        now = datetime.utcnow()
        data.setdefault("created_at", now)
        data.setdefault("timestamp", now)
        if "processed" not in data:
            data["processed"] = False
        result = await db.incoming_messages.insert_one(data)
        return result.inserted_id

    async def mark_incoming_message_processed(self, document_id: Any) -> bool:
        db = get_database()
        result = await db.incoming_messages.update_one(
            {"_id": document_id},
            {"$set": {"processed": True, "processed_at": datetime.utcnow()}},
        )
        return result.matched_count > 0

    async def get_unprocessed_incoming_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        db = get_database()
        cursor = db.incoming_messages.find({"processed": False}).sort("created_at", 1).limit(limit)
        return [doc async for doc in cursor]
