from datetime import datetime

from pydantic import BaseModel, Field


class BookingBase(BaseModel):
    user_id: str = Field(..., description="MongoDB ObjectId of the user as a hex string")
    provider_id: str = Field(..., description="MongoDB ObjectId of the provider as a hex string")
    date_time: datetime


class BookingCreate(BookingBase):
    class Config:
        schema_extra = {
            "example": {
                "user_id": "some_user_id",
                "provider_id": "some_provider_id",
                "date_time": datetime.now()
            }
        }


class Booking(BaseModel):
    id: str
    user_id: str
    provider_id: str
    date_time: datetime
    status: str
    created_at: datetime
