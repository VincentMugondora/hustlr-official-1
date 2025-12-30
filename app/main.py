import warnings

# Silence specific Pydantic v2 config warning about schema_extra/json_schema_extra
warnings.filterwarnings(
    "ignore",
    message="Valid config keys have changed in V2:",
    category=UserWarning,
)

from fastapi import FastAPI
from app.api import whatsapp, service_providers, bookings, users
from app.db import connect_to_mongo, close_mongo_connection
from app.utils.mongo_service import MongoService
from config import settings
from app.utils.baileys_client import BaileysClient
import logging
import sys
import httpx
import asyncio

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
    # Non-breaking: ensure indexes if enabled
    try:
        if getattr(settings, 'ENABLE_INDEX_CREATION', False):
            svc = MongoService()
            await svc.ensure_indexes()
    except Exception:
        # Do not fail startup on index ensure
        pass

@app.on_event("shutdown")
async def on_shutdown():
    await close_mongo_connection()

# Include API routes
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp"])
app.include_router(service_providers.router, prefix="/api/providers", tags=["Providers"])
app.include_router(bookings.router, prefix="/api/bookings", tags=["Bookings"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])

@app.get("/")
def root():
    return {"message": "Hustlr WhatsApp Bot is running"}

@app.post("/admin/notify-admins")
async def notify_admins():
    """Log admin notification info (manual sending required via WhatsApp)"""
    logger = logging.getLogger(__name__)
    admin_numbers = [
        '+263783961640',
        '+263775251636',
        '+263777530322',
        '+16509965727'
    ]
    
    admin_welcome_message = (
        "🎉 Welcome to Hustlr Admin Panel!\n\n"
        "You have been designated as a Hustlr administrator.\n\n"
        "YOUR RESPONSIBILITIES:\n"
        "1. Review new service provider registrations\n"
        "2. Verify provider credentials (name, ID, experience, location)\n"
        "3. Approve or deny provider applications\n"
        "4. Manage provider status and information\n"
        "5. Handle provider disputes and complaints\n"
        "6. Monitor booking quality and customer satisfaction\n\n"
        "ADMIN COMMANDS:\n"
        "• 'approve +263777530322' - Approve a provider registration\n"
        "• 'deny +263777530322' - Reject a provider registration\n\n"
        "When you receive a provider registration request, review the details carefully and respond with the appropriate command.\n\n"
        "Thank you for helping Hustlr grow! 🚀"
    )
    
    results = {}
    
    # Log the admin notification for manual sending
    logger.info("=" * 80)
    logger.info("ADMIN NOTIFICATION REQUIRED")
    logger.info("=" * 80)
    logger.info(f"Please send the following message to these {len(admin_numbers)} admin numbers:")
    logger.info("")
    for admin_num in admin_numbers:
        logger.info(f"  • {admin_num}")
    logger.info("")
    logger.info("MESSAGE:")
    logger.info("-" * 80)
    logger.info(admin_welcome_message)
    logger.info("-" * 80)
    logger.info("")
    
    # Return info about what needs to be done
    return {
        "status": "pending_manual_send",
        "message": "Admin welcome messages need to be sent manually via WhatsApp",
        "admin_numbers": admin_numbers,
        "message_content": admin_welcome_message,
        "instructions": "Copy the message above and send it to each admin number via WhatsApp"
    }
