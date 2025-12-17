from datetime import datetime, timedelta
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

    async def delete_user_and_data(self, whatsapp_number: str) -> bool:
        """Delete a user and associated session/history documents.

        Keeps bookings intact for providers, but removes direct user profile
        and chat history to respect a DELETE MY DATA request.
        """
        db = get_database()
        # Delete user profile
        await db.users.delete_one({"whatsapp_number": whatsapp_number})
        # Delete session
        await db.sessions.delete_one({"whatsapp_number": whatsapp_number})
        # Delete conversation history
        await db.conversation_history.delete_many({"whatsapp_number": whatsapp_number})
        return True

    # Provider operations
    async def get_providers_by_service(self, service_type: str, location: Optional[str] = None) -> List[Dict[str, Any]]:
        db = get_database()
        query: Dict[str, Any] = {"service_type": service_type, "status": "active"}
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

    async def update_provider_fields(self, provider_id: str, updates: Dict[str, Any]) -> bool:
        db = get_database()
        try:
            oid = ObjectId(provider_id)
        except Exception:
            return False
        to_set = dict(updates or {})
        to_set["updated_at"] = datetime.utcnow()
        result = await db.providers.update_one({"_id": oid}, {"$set": to_set})
        return result.matched_count > 0

    async def append_provider_verification_media(self, provider_id: str, media_item: Dict[str, Any]) -> bool:
        db = get_database()
        try:
            oid = ObjectId(provider_id)
        except Exception:
            return False
        item = dict(media_item or {})
        item.setdefault("added_at", datetime.utcnow())
        result = await db.providers.update_one(
            {"_id": oid},
            {
                "$push": {"verification_media": item},
                "$set": {"verification_state": "pending_review", "updated_at": datetime.utcnow()},
            },
        )
        return result.matched_count > 0

    async def append_user_verification_media(self, whatsapp_number: str, media_item: Dict[str, Any]) -> bool:
        db = get_database()
        item = dict(media_item or {})
        item.setdefault("added_at", datetime.utcnow())
        result = await db.users.update_one(
            {"whatsapp_number": whatsapp_number},
            {
                "$push": {"verification_media": item},
                "$set": {"verification_state": "pending_review", "updated_at": datetime.utcnow()},
            },
        )
        return result.matched_count > 0

    async def list_providers(self, status: Optional[str] = None, service_type: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        db = get_database()
        query: Dict[str, Any] = {}
        if status:
            query["status"] = status
        if service_type:
            query["service_type"] = service_type
        cursor = db.providers.find(query).sort("registered_at", -1).limit(limit)
        return [doc async for doc in cursor]

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

    async def update_booking_fields(self, booking_id: str, updates: Dict[str, Any]) -> bool:
        db = get_database()
        to_set = dict(updates or {})
        to_set["updated_at"] = datetime.utcnow()
        result = await db.bookings.update_one({"booking_id": booking_id}, {"$set": to_set})
        return result.matched_count > 0

    async def list_bookings(self, limit: int = 20, start: Optional[datetime] = None, end: Optional[datetime] = None) -> List[Dict[str, Any]]:
        db = get_database()
        query: Dict[str, Any] = {}
        if start or end:
            time_filter: Dict[str, Any] = {}
            if start:
                time_filter["$gte"] = start
            if end:
                time_filter["$lte"] = end
            query["created_at"] = time_filter
        cursor = db.bookings.find(query).sort("created_at", -1).limit(limit)
        return [doc async for doc in cursor]

    async def list_bookings_for_provider(self, provider_whatsapp_number: str, limit: int = 20, status: Optional[str] = None) -> List[Dict[str, Any]]:
        db = get_database()
        query: Dict[str, Any] = {"provider_whatsapp_number": provider_whatsapp_number}
        if status:
            query["status"] = status
        cursor = db.bookings.find(query).sort("created_at", -1).limit(limit)
        return [doc async for doc in cursor]

    async def count_bookings(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> int:
        db = get_database()
        query: Dict[str, Any] = {}
        if start or end:
            time_filter: Dict[str, Any] = {}
            if start:
                time_filter["$gte"] = start
            if end:
                time_filter["$lte"] = end
            query["created_at"] = time_filter
        return await db.bookings.count_documents(query)

    async def count_bookings_by_status(self, status: str, start: Optional[datetime] = None, end: Optional[datetime] = None) -> int:
        db = get_database()
        query: Dict[str, Any] = {"status": status}
        if start or end:
            time_filter: Dict[str, Any] = {}
            if start:
                time_filter["$gte"] = start
            if end:
                time_filter["$lte"] = end
            query["created_at"] = time_filter
        return await db.bookings.count_documents(query)

    async def count_providers(self, status: Optional[str] = None) -> int:
        db = get_database()
        query: Dict[str, Any] = {}
        if status:
            query["status"] = status
        return await db.providers.count_documents(query)

    async def count_users(self, start: Optional[datetime] = None, end: Optional[datetime] = None) -> int:
        db = get_database()
        query: Dict[str, Any] = {}
        if start or end:
            time_filter: Dict[str, Any] = {}
            if start:
                time_filter["$gte"] = start
            if end:
                time_filter["$lte"] = end
            query["registered_at"] = time_filter
        return await db.users.count_documents(query)

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

    async def get_bookings_needing_reminders(self, within_minutes: int = 30) -> List[Dict[str, Any]]:
        """Return bookings that are due for a reminder within the next window.

        We rely on the 'date_time' field being stored in a consistent
        '%Y-%m-%d %H:%M' format so lexical comparison is safe.
        """
        db = get_database()
        now = datetime.utcnow()
        end = now + timedelta(minutes=within_minutes)

        now_str = now.strftime('%Y-%m-%d %H:%M')
        end_str = end.strftime('%Y-%m-%d %H:%M')

        query = {
            "reminder_sent": False,
            "status": {"$in": ["pending", "confirmed"]},
            "date_time": {"$gte": now_str, "$lte": end_str},
        }

        cursor = db.bookings.find(query)
        return [doc async for doc in cursor]

    async def mark_booking_reminder_sent(self, booking_id: str) -> bool:
        """Mark a booking's reminder as sent."""
        db = get_database()
        result = await db.bookings.update_one(
            {"booking_id": booking_id},
            {"$set": {"reminder_sent": True, "reminder_sent_at": datetime.utcnow()}},
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

    async def delete_conversation_history(self, whatsapp_number: str) -> bool:
        db = get_database()
        result = await db.conversation_history.delete_many({"whatsapp_number": whatsapp_number})
        return result.acknowledged

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

    async def store_media_upload(self, media_doc: Dict[str, Any]) -> Any:
        db = get_database()
        doc = dict(media_doc or {})
        now = datetime.utcnow()
        doc.setdefault("created_at", now)
        result = await db.media_uploads.insert_one(doc)
        return result.inserted_id

    async def log_admin_audit(self, record: Dict[str, Any]) -> Any:
        db = get_database()
        doc = dict(record or {})
        doc.setdefault("timestamp", datetime.utcnow())
        result = await db.admin_audit.insert_one(doc)
        return result.inserted_id
