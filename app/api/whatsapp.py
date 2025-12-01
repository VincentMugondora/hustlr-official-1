from fastapi import APIRouter, Request, status, HTTPException, Body, Query
from fastapi.responses import PlainTextResponse
from app.utils.webhook_verifier import verify_whatsapp_signature
from app.models.message import WhatsAppMessage
from app.utils.whatsapp_cloud_api import WhatsAppCloudAPI
from app.utils.message_handler import MessageHandler
from app.utils.aws_lambda import AWSLambdaService
from app.utils.dynamodb_service import DynamoDBService
from app.utils.baileys_client import BaileysClient
from config import settings
import logging
import json
from datetime import datetime

router = APIRouter()

# Initialize services
whatsapp_api = WhatsAppCloudAPI()
dynamodb_service = DynamoDBService()
lambda_service = AWSLambdaService()
message_handler = MessageHandler(whatsapp_api, dynamodb_service, lambda_service)

# Baileys-based transport (WhatsApp Web via Node service)
baileys_client = BaileysClient()
baileys_message_handler = MessageHandler(baileys_client, dynamodb_service, lambda_service)

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
                    msg_type = msg_data.get("type", "N/A")
                    logger.info(f"Message ID: {msg_data.get('id', 'N/A')}")
                    logger.info(f"Message timestamp: {msg_data.get('timestamp', 'N/A')}")
                    logger.info(f"Message type: {msg_type}")
                    
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
                    # Normalize WhatsApp location messages into text so the
                    # downstream handler can treat them like typed locations.
                    if msg_type == "location":
                        loc = msg_data.get("location", {})
                        lat = loc.get("latitude")
                        lng = loc.get("longitude")
                        name = loc.get("name") or ""
                        address = loc.get("address") or ""
                        parts = []
                        if name:
                            parts.append(name)
                        if address:
                            parts.append(address)
                        if lat is not None and lng is not None:
                            parts.append(f"({lat},{lng})")
                        location_text = " ".join(parts) or "[location shared]"
                        message.text = location_text
                        logger.info(f"Normalized location text: {location_text}")
    except Exception as e:
        logger.error(f"Error parsing payload details: {e}")
    
    # Console output for immediate visibility
    print(f"[{timestamp}] 📱 WhatsApp Message Received")
    print(f"👤 From: {message.from_number}")
    print(f"💬 Message: '{message.text}'")
    print(f"📊 Full payload logged to application logs")

    # Handle message with enhanced handler
    try:
        if message.text:  # Only process text messages for now
            await message_handler.handle_message(message)
            logger.info(f"Message processed successfully for {message.from_number}")
        else:
            logger.info(f"Non-text message received from {message.from_number}, skipping")
    except Exception as e:
        logger.error(f"Error handling message from {message.from_number}: {e}")
        # Send error message to user
        try:
            await whatsapp_api.send_text_message(
                message.from_number,
                "❌ Sorry, I'm having trouble processing your message. Please try again later."
            )
        except Exception as send_error:
            logger.error(f"Failed to send error message: {send_error}")

    # Respond to WhatsApp so Meta knows you received successfully
    return {"status": "success"}


@router.post("/baileys-webhook")
async def receive_baileys_message(
    payload: dict = Body(...),
):
    """Webhook endpoint for the local Baileys Node service.

    Expects a simple JSON body: {"from": "<number>", "text": "<message>", "rawMessage": {...}}
    and forwards it into the shared MessageHandler using the BaileysClient transport.
    """

    logger = logging.getLogger(__name__)
    timestamp = datetime.utcnow().isoformat()

    logger.info(f"[{timestamp}] Baileys webhook received")
    try:
        logger.info(f"Baileys payload: {json.dumps(payload, indent=2, default=str)}")
    except Exception:
        logger.info("Baileys payload could not be JSON-encoded for logging")

    from_number = (payload.get("from") or "").strip()
    text = (payload.get("text") or "").strip()

    message = WhatsAppMessage(from_number, text)

    logger.info(f"Baileys message from: {message.from_number}")
    logger.info(f"Baileys text: '{message.text}'")

    try:
        if message.text:
            await baileys_message_handler.handle_message(message)
            logger.info(f"Baileys message processed successfully for {message.from_number}")
        else:
            logger.info(f"Baileys webhook received non-text/empty message from {message.from_number}, skipping")
    except Exception as e:
        logger.error(f"Error handling Baileys message from {message.from_number}: {e}")
        # Best-effort error notification via Baileys transport
        try:
            await baileys_client.send_text_message(
                message.from_number,
                "❌ Sorry, I'm having trouble processing your message. Please try again later.",
            )
        except Exception as send_error:
            logger.error(f"Failed to send Baileys error message: {send_error}")

    return {"status": "success"}
