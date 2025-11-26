from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProviderBase(BaseModel):
    whatsapp_number: str = Field(..., min_length=5, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    service_type: str = Field(..., min_length=1, max_length=50)
    location: str = Field(..., min_length=1, max_length=100)
    business_name: Optional[str] = Field(None, max_length=100)
    contact: Optional[str] = Field(None, max_length=100)


class ProviderCreate(ProviderBase):
    pass


class Provider(ProviderBase):
    id: str
    status: str = "active"
    registered_at: datetime

