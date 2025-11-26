from datetime import datetime

from pydantic import BaseModel, Field


class UserBase(BaseModel):
    whatsapp_number: str = Field(..., min_length=5, max_length=20)
    name: str = Field(..., min_length=1, max_length=100)
    location: str = Field(..., min_length=1, max_length=100)
    agreed_privacy_policy: bool


class UserCreate(UserBase):
    pass


class User(UserBase):
    id: str
    onboarding_completed: bool = True
    registered_at: datetime

