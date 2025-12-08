import warnings

# Silence specific Pydantic v2 config warning about schema_extra/json_schema_extra
warnings.filterwarnings(
    "ignore",
    message="Valid config keys have changed in V2:",
    category=UserWarning,
)

from fastapi import FastAPI
from app.api import whatsapp, service_providers, bookings, users, claude
from app.db import connect_to_mongo, close_mongo_connection
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),  # Console output
        logging.FileHandler('hustlr_bot.log', mode='a')  # File output
    ]
)

# Create FastAPI app
app = FastAPI(title="Hustlr WhatsApp Bot")

# Startup and shutdown events
@app.on_event("startup")
async def on_startup():
    await connect_to_mongo()

@app.on_event("shutdown")
async def on_shutdown():
    await close_mongo_connection()

# Include API routes
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp"])
app.include_router(service_providers.router, prefix="/api/providers", tags=["Providers"])
app.include_router(bookings.router, prefix="/api/bookings", tags=["Bookings"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(claude.router, prefix="/api/claude", tags=["Claude"])

@app.get("/")
def root():
    return {"message": "Hustlr WhatsApp Bot is running"}
