from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.booking import BookingCreate, Booking


router = APIRouter()


def _serialize_booking(doc: dict) -> Booking:
    return Booking(
        id=str(doc["_id"]),
        user_id=str(doc["user_id"]),
        provider_id=str(doc["provider_id"]),
        date_time=doc["date_time"],
        status=doc["status"],
        created_at=doc["created_at"],
    )


def _ensure_object_id(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ObjectId format.")


@router.post("/", response_model=Booking, status_code=status.HTTP_201_CREATED)
async def create_booking(
    data: BookingCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    user_oid = _ensure_object_id(data.user_id)
    provider_oid = _ensure_object_id(data.provider_id)

    # Ensure user and provider exist
    user = await db.users.find_one({"_id": user_oid})
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    provider = await db.providers.find_one({"_id": provider_oid})
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found.")

    doc = {
        "user_id": user_oid,
        "provider_id": provider_oid,
        "date_time": data.date_time,
        "status": "pending",
        "created_at": datetime.utcnow(),
    }

    result = await db.bookings.insert_one(doc)
    created = await db.bookings.find_one({"_id": result.inserted_id})
    return _serialize_booking(created)
