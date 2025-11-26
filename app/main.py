from fastapi import FastAPI
from app.api import whatsapp

# Create FastAPI app
app = FastAPI(title="Hustlr WhatsApp Bot")

# Include WhatsApp API routes
app.include_router(whatsapp.router, prefix="/api/whatsapp", tags=["WhatsApp"])

@app.get("/")
def root():
    return {"message": "Hustlr WhatsApp Bot is running"}
