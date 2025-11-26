from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.provider import ProviderCreate, Provider


router = APIRouter()


def _serialize_provider(doc: dict) -> Provider:
    return Provider(
        id=str(doc["_id"]),
        whatsapp_number=doc["whatsapp_number"],
        name=doc["name"],
        service_type=doc["service_type"],
        location=doc["location"],
        business_name=doc.get("business_name"),
        contact=doc.get("contact"),
        status=doc.get("status", "active"),
        registered_at=doc["registered_at"],
    )


@router.post("/register", response_model=Provider, status_code=status.HTTP_201_CREATED)
async def register_provider(
    data: ProviderCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    existing = await db.providers.find_one({"whatsapp_number": data.whatsapp_number})
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Provider already registered.")

    doc = {
        "whatsapp_number": data.whatsapp_number,
        "name": data.name,
        "service_type": data.service_type,
        "location": data.location,
        "business_name": data.business_name,
        "contact": data.contact,
        "status": "active",
        "registered_at": datetime.utcnow(),
    }
    result = await db.providers.insert_one(doc)
    created = await db.providers.find_one({"_id": result.inserted_id})
    return _serialize_provider(created)

