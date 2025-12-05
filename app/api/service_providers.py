from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
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


class PlacesImportRequest(BaseModel):
    query: str
    service_type: Optional[str] = None
    status: Optional[str] = "active"
    limit: Optional[int] = 20


@router.post("/import-places")
async def import_places(
    data: PlacesImportRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    try:
        from app.utils.places_importer import import_places_to_db
        result = await import_places_to_db(
            db,
            query=data.query,
            service_type_override=data.service_type,
            status=data.status,
            limit=data.limit or 20,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


class TextImportRequest(BaseModel):
    text: str
    service_type: Optional[str] = None
    status: Optional[str] = "active"


@router.post("/import-text")
async def import_text(
    data: TextImportRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    try:
        from app.utils.places_importer import parse_text_providers, import_text_to_db
        items = parse_text_providers(data.text)
        stats = await import_text_to_db(
            db,
            text=data.text,
            service_type_override=data.service_type,
            status=data.status or "active",
        )
        return {"items": items, "stats": stats}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PlacesImportFromLink(BaseModel):
    maps_url: str
    service_type: Optional[str] = None
    status: Optional[str] = "active"
    limit: Optional[int] = 20


@router.post("/import-places/link")
async def import_places_from_link(
    data: PlacesImportFromLink,
    db: AsyncIOMotorDatabase = Depends(get_database),
):
    try:
        from app.utils.places_importer import extract_query_from_maps_link, import_places_to_db
        query = await extract_query_from_maps_link(data.maps_url)
        if not query:
            raise HTTPException(status_code=400, detail="Could not extract a search query from the provided link. Try '/import-places' with a 'query' string instead.")
        result = await import_places_to_db(
            db,
            query=query,
            service_type_override=data.service_type,
            status=data.status,
            limit=data.limit or 20,
        )
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))

