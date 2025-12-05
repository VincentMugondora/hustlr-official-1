from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.models.user import UserCreate, User


router = APIRouter()


def _serialize_user(doc: dict) -> User:
    return User(
        id=str(doc["_id"]),
        whatsapp_number=doc["whatsapp_number"],
        name=doc["name"],
        location=doc["location"],
        agreed_privacy_policy=doc["agreed_privacy_policy"],
        onboarding_completed=doc.get("onboarding_completed", True),
        registered_at=doc["registered_at"],
    )


@router.post("/onboard", response_model=User, status_code=status.HTTP_201_CREATED)
async def onboard_user(
    data: UserCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    if not data.agreed_privacy_policy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Privacy policy must be accepted.")

    existing = await db.users.find_one({"whatsapp_number": data.whatsapp_number})
    if existing:
        # idempotent: return existing user
        return _serialize_user(existing)

    doc = {
        "whatsapp_number": data.whatsapp_number,
        "name": data.name,
        "location": data.location,
        "agreed_privacy_policy": data.agreed_privacy_policy,
        "onboarding_completed": True,
        "registered_at": datetime.utcnow(),
    }
    result = await db.users.insert_one(doc)
    created = await db.users.find_one({"_id": result.inserted_id})
    return _serialize_user(created)


@router.get("/", response_model=List[User])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    docs = await db.users.find({}).sort("registered_at", -1).skip(skip).limit(limit).to_list(length=limit or 100)
    return [_serialize_user(doc) for doc in docs]
