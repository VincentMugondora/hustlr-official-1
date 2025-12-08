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
import logging
import sys
import httpx

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

@app.get("/")
def root():
    return {"message": "Hustlr WhatsApp Bot is running"}

@app.post("/admin/notify-admins")
async def notify_admins():
    """Send welcome message to all admin numbers"""
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
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for admin_num in admin_numbers:
                try:
                    response = await client.post(
                        "http://localhost:3000/send-text",
                        json={"to": admin_num, "text": admin_welcome_message},
                        timeout=10
                    )
                    results[admin_num] = {"status": "sent", "code": response.status_code}
                    logger.info(f"Admin welcome message sent to {admin_num}")
                except Exception as e:
                    results[admin_num] = {"status": "failed", "error": str(e)}
                    logger.error(f"Failed to send admin welcome message to {admin_num}: {e}")
    except Exception as e:
        logger.error(f"Could not send admin welcome messages: {e}")
        return {"status": "error", "message": str(e)}
    
    return {"status": "completed", "results": results}
