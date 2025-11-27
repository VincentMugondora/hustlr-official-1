from fastapi import APIRouter, Request, status, HTTPException, Body, Query
from fastapi.responses import PlainTextResponse
from app.utils.webhook_verifier import verify_whatsapp_signature
from app.models.message import WhatsAppMessage
from app.utils.whatsapp_cloud_api import WhatsAppCloudAPI
from app.utils.message_handler import MessageHandler
from app.utils.aws_lambda import AWSLambdaService
from app.utils.dynamodb_service import DynamoDBService
from config import settings
import logging
import json
from datetime import datetime

router = APIRouter()

@router.get("/webhook")
async def verify_whatsapp_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
):
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(content=hub_challenge or "", status_code=status.HTTP_200_OK)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed.")

@router.post("/webhook")
async def receive_whatsapp_message(
    request: Request,
    payload: dict = Body(...)
):
    """
    Endpoint to receive WhatsApp webhook messages.
    Verifies request (if you implement verification), parses payload, and processes message.
    """
    # Configure logging
    logger = logging.getLogger(__name__)
    timestamp = datetime.utcnow().isoformat()
    
    # Log incoming request details
    logger.info(f"[{timestamp}] WhatsApp webhook received")
    logger.info(f"Headers: {dict(request.headers)}")
    
    # Log raw payload
    logger.info(f"Raw payload: {json.dumps(payload, indent=2)}")
    
    # (Optional) Signature verification logic if Meta needs it
    # headers = request.headers
    # if not verify_whatsapp_signature(headers, payload):
    #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Verification failed.")

    # Parse incoming WhatsApp message
    message = WhatsAppMessage.from_webhook(payload)
    
    # Comprehensive logging
    logger.info(f"Parsed message object: {message}")
    logger.info(f"From number: {message.from_number}")
    logger.info(f"Message text: '{message.text}'")
    logger.info(f"Message length: {len(message.text)} characters")
    
    # Additional payload analysis
    try:
        entry = payload.get("entry", [])
        if entry:
            changes = entry[0].get("changes", [])
            if changes:
                value = changes[0].get("value", {})
                messages = value.get("messages", [])
                if messages:
                    msg_data = messages[0]
                    logger.info(f"Message ID: {msg_data.get('id', 'N/A')}")
                    logger.info(f"Message timestamp: {msg_data.get('timestamp', 'N/A')}")
                    logger.info(f"Message type: {msg_data.get('type', 'N/A')}")
                    
                    # Log contact info if present
                    contacts = value.get("contacts", [])
                    if contacts:
                        contact = contacts[0]
                        logger.info(f"Contact name: {contact.get('name', {}).get('formatted_name', 'N/A')}")
                        logger.info(f"Contact wa_id: {contact.get('wa_id', 'N/A')}")
                    
                    # Log metadata
                    metadata = value.get("metadata", {})
                    if metadata:
                        logger.info(f"Phone number ID: {metadata.get('phone_number_id', 'N/A')}")
                        logger.info(f"Display phone number: {metadata.get('display_phone_number', 'N/A')}")
    except Exception as e:
        logger.error(f"Error parsing payload details: {e}")
    
    # Console output for immediate visibility
    print(f"[{timestamp}] 📱 WhatsApp Message Received")
    print(f"👤 From: {message.from_number}")
    print(f"💬 Message: '{message.text}'")
    print(f"📊 Full payload logged to application logs")

    # Implement your logic (respond, save to DB, etc.)
    print(f"Received message: {message}")

    # Respond to WhatsApp so Meta knows you received successfully
    return {"status": "success"}
