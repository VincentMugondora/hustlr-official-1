from datetime import datetime
from typing import Any, Dict, Optional, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db import get_database
from app.utils.aws_lambda import AWSLambdaService
from app.utils.mongo_service import MongoService

import json

router = APIRouter()


class ProcessRequest(BaseModel):
    user_number: str = Field(..., description="User WhatsApp number (e.g., 26377...)")
    message: str = Field(..., description="User's message text")
    service_type: Optional[str] = Field(None, description="Optional hint to filter providers")


class ClaudeProcessResponse(BaseModel):
    status: str
    data: Dict[str, Any]


def _parse_iso_datetime(value: str) -> datetime:
    try:
        # Python >=3.11: fromisoformat handles 'YYYY-MM-DDTHH:MM:SS' and 'YYYY-MM-DD HH:MM'
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid ISO datetime: {value}")


@router.post("/process")
async def process_with_claude(
    payload: ProcessRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    """Delegates conversation control to Claude (Bedrock) and enforces JSON protocol.

    Behavior:
    - Builds context from MongoDB (user profile, provider catalog where applicable)
    - Sends user's message and context to Claude
    - Expects JSON with either {status: IN_PROGRESS, next_question} or {status: COMPLETE, type, data}
    - On COMPLETE, validates and saves to MongoDB
    - Returns the JSON as-is to the caller
    """
    user = await db.users.find_one({"whatsapp_number": payload.user_number})
    if not user:
        # Create a minimal user record on first contact if missing
        user_doc = {
            "whatsapp_number": payload.user_number,
            "name": None,
            "location": None,
            "onboarding_completed": False,
            "registered_at": datetime.utcnow(),
        }
        ins = await db.users.insert_one(user_doc)
        user = await db.users.find_one({"_id": ins.inserted_id})

    # Provider catalog: only include when we have a target service_type to keep context small
    provider_catalog: List[Dict[str, Any]] = []
    # Infer service type if not provided
    normalized_st = (payload.service_type or "").strip().lower()
    if not normalized_st:
        msg_lower = (payload.message or "").lower()
        svc_map = {
            'plumber': ['plumber', 'plumbing'],
            'electrician': ['electrician', 'electrical', 'electricity', 'lights'],
            'carpenter': ['carpenter', 'carpentry', 'wood'],
            'painter': ['painter', 'painting'],
            'cleaner': ['cleaner', 'cleaning'],
            'mechanic': ['mechanic', 'repair'],
            'gardener': ['gardener', 'gardening', 'landscaping'],
            'doctor': ['doctor', 'clinic', 'hospital'],
        }
        for svc, keys in svc_map.items():
            if any(k in msg_lower for k in keys):
                normalized_st = svc
                break
    if normalized_st:
        # Filter by user's saved location if present
        query: Dict[str, Any] = {"service_type": normalized_st}
        if user.get("location"):
            query["location"] = {"$regex": user["location"], "$options": "i"}
        cursor = db.providers.find(query).limit(20)
        providers = [doc async for doc in cursor]
        provider_catalog = [
            {
                "_id": str(p["_id"]),
                "name": p.get("name") or p.get("full_name") or "Provider",
                "service_category": p.get("service_type") or p.get("service_category"),
                "location": p.get("location"),
                "phone": p.get("whatsapp_number") or p.get("phone"),
            }
            for p in providers
        ]

    # Fetch recent conversation history (if present)
    svc = MongoService()
    try:
        history = await svc.get_conversation_history(payload.user_number, limit=10)
    except Exception:
        history = []

    # Build user context for Claude
    user_context = {
        "name": user.get("name"),
        "location": user.get("location"),
        "user_profile": {
            "id": str(user["_id"]),
            "whatsapp_number": user.get("whatsapp_number"),
            "name": user.get("name"),
            "location": user.get("location"),
        },
        "provider_catalog": provider_catalog,
    }

    # Call Bedrock/Claude
    lambda_service = AWSLambdaService()
    ai_text = await lambda_service.invoke_question_answerer(
        payload.message,
        user_context=user_context,
        conversation_history=history,
    )

    # Parse Claude response (must be JSON-only per protocol)
    try:
        data = json.loads(ai_text)
    except Exception:
        raise HTTPException(status_code=500, detail="Claude did not return valid JSON.")

    # Persist this exchange to conversation history
    await svc.store_message(payload.user_number, "user", payload.message)
    await svc.store_message(payload.user_number, "assistant", ai_text)

    status_str = str(data.get("status") or "").upper()
    if status_str not in ("IN_PROGRESS", "COMPLETE"):
        raise HTTPException(status_code=400, detail="Invalid status from Claude.")

    if status_str == "IN_PROGRESS":
        # Expect a next_question string
        if not isinstance(data.get("next_question"), str) or not data.get("next_question").strip():
            raise HTTPException(status_code=400, detail="IN_PROGRESS must include next_question.")
        return data

    # COMPLETE flow
    typ = data.get("type")
    payload_data: Dict[str, Any] = data.get("data") or {}

    if typ == "booking":
        # Validate booking payload
        required = ["service_type", "service_provider_id", "date", "time"]
        missing = [k for k in required if not payload_data.get(k)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing booking fields: {', '.join(missing)}")

        # Validate IDs
        try:
            provider_oid = ObjectId(payload_data["service_provider_id"])
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid service_provider_id")

        provider = await db.providers.find_one({"_id": provider_oid})
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")

        # Client id optional (fill from user if missing)
        client_id_str = payload_data.get("client_id") or str(user["_id"])
        try:
            client_oid = ObjectId(client_id_str)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid client_id")

        # Parse date to datetime
        date_dt = _parse_iso_datetime(payload_data["date"])  # ISO date or datetime
        time_text = str(payload_data.get("time") or "").strip()
        notes = str(payload_data.get("additional_notes") or "").strip()

        booking_doc = {
            "service_type": payload_data["service_type"],
            "service_provider_id": provider_oid,
            "client_id": client_oid,
            "date": date_dt,
            "time": time_text,
            "additional_notes": notes,
            "status": "pending",
            "created_at": datetime.utcnow(),
        }
        ins = await db.bookings.insert_one(booking_doc)
        saved = await db.bookings.find_one({"_id": ins.inserted_id})
        return {
            "status": "COMPLETE",
            "type": "booking",
            "data": {
                "id": str(saved["_id"]),
                "service_type": saved["service_type"],
                "service_provider_id": str(saved["service_provider_id"]),
                "client_id": str(saved["client_id"]),
                "date": saved["date"].isoformat(),
                "time": saved["time"],
                "additional_notes": saved.get("additional_notes") or "",
                "status": saved.get("status"),
            },
        }

    elif typ == "provider_registration":
        # Validate provider registration fields
        required = [
            "full_name",
            "phone",
            "service_category",
            "years_experience",
            "national_id",
            "location",
            "availability_days",
            "availability_hours",
        ]
        missing = [k for k in required if payload_data.get(k) in (None, "")]  # allow 0 for years_experience
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing provider fields: {', '.join(missing)}")

        # Basic normalization/mapping to our providers collection
        provider_doc = {
            "name": payload_data["full_name"],
            "whatsapp_number": payload_data["phone"],
            "service_type": payload_data["service_category"],
            "years_experience": payload_data["years_experience"],
            "national_id": payload_data["national_id"],
            "location": payload_data["location"],
            "availability_days": payload_data["availability_days"],
            "availability_hours": payload_data["availability_hours"],
            "status": "pending",
            "registered_at": datetime.utcnow(),
        }
        ins = await db.providers.insert_one(provider_doc)
        saved = await db.providers.find_one({"_id": ins.inserted_id})
        return {
            "status": "COMPLETE",
            "type": "provider_registration",
            "data": {
                "id": str(saved["_id"]),
                "full_name": saved["name"],
                "phone": saved.get("whatsapp_number"),
                "service_category": saved.get("service_type"),
                "years_experience": saved.get("years_experience"),
                "national_id": saved.get("national_id"),
                "location": saved.get("location"),
                "availability_days": saved.get("availability_days"),
                "availability_hours": saved.get("availability_hours"),
                "status": saved.get("status"),
            },
        }

    else:
        raise HTTPException(status_code=400, detail="Unknown COMPLETE type.")
