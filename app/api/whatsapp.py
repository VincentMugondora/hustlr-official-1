from fastapi import APIRouter, Request, status, HTTPException, Body, Query
from fastapi.responses import PlainTextResponse
from app.utils.webhook_verifier import verify_whatsapp_signature
from app.models.message import WhatsAppMessage
from app.utils.whatsapp_cloud_api import WhatsAppCloudAPI
from app.utils.message_handler import MessageHandler
from app.utils.aws_lambda import AWSLambdaService
from app.utils.gemini_service import GeminiService
from app.utils.mongo_service import MongoService
from app.utils.baileys_client import BaileysClient
from app.utils.location_service import get_location_service
from config import settings
import logging
import json
from datetime import datetime
from app.utils.storage_service import StorageService

router = APIRouter()

# Initialize services
whatsapp_api = WhatsAppCloudAPI()
mongo_service = MongoService()
storage_service = StorageService()

# Choose AI backend: Gemini for testing, otherwise Bedrock
if settings.USE_GEMINI_INTENT:
    ai_service = GeminiService()
else:
    ai_service = AWSLambdaService()

message_handler = MessageHandler(whatsapp_api, mongo_service, ai_service)

# Baileys-based transport (WhatsApp Web via Node service)
baileys_client = BaileysClient()
baileys_message_handler = MessageHandler(baileys_client, mongo_service, ai_service)

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
    
    message_id = None
    msg_timestamp = None
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
                    message_id = msg_data.get("id")
                    msg_timestamp = msg_data.get("timestamp")
                    logger.info(f"Message ID: {message_id or 'N/A'}")
                    logger.info(f"Message timestamp: {msg_timestamp or 'N/A'}")
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
                        
                        # Try to reverse geocode coordinates to get accurate location
                        location_name = None
                        if lat is not None and lng is not None:
                            location_service = get_location_service()
                            location_name = await location_service.reverse_geocode(lat, lng)
                            logger.info(f"Reverse geocoded coordinates ({lat}, {lng}) to: {location_name}")
                        
                        # Build location text with priority: reverse geocoded name > provided name > address > coordinates
                        parts = []
                        if location_name:
                            parts.append(location_name)
                        elif name:
                            parts.append(name)
                        elif address:
                            parts.append(address)
                        
                        if lat is not None and lng is not None:
                            parts.append(f"({lat},{lng})")
                        
                        location_text = " ".join(parts) or "[location shared]"
                        message.text = location_text
                        logger.info(f"Normalized location text: {location_text}")
    except Exception as e:
        logger.exception("Error parsing payload details")
    
    incoming_doc_id = None
    try:
        created_at = None
        if msg_timestamp and str(msg_timestamp).isdigit():
            try:
                created_at = datetime.utcfromtimestamp(int(msg_timestamp))
            except Exception:
                created_at = None
        if created_at is None:
            created_at = datetime.utcnow()
        incoming_doc = {
            "from_number": message.from_number,
            "text": message.text,
            "message_id": message_id,
            "source": "cloud",
            "created_at": created_at,
            "msg_type": getattr(message, "type", "text"),
            "media_id": getattr(message, "media_id", None),
            "media_mime": getattr(message, "media_mime", None),
            "media_caption": getattr(message, "media_caption", None),
        }
        incoming_doc_id = await mongo_service.store_incoming_message(incoming_doc)
    except Exception as e:
        logger.warning(f"Failed to store incoming message for {message.from_number}: {e}")
    
    # Console output for immediate visibility
    print(f"[{timestamp}] WhatsApp Message Received")
    print(f"From: {message.from_number}")
    print(f"Message: '{message.text}'")
    print(f"Full payload logged to application logs")

    # Media handling for Cloud webhook
    try:
        msg_type_lower = (getattr(message, "type", "") or "").lower()
        if msg_type_lower in ("image", "document", "video"):
            media_id = getattr(message, "media_id", None)
            if not media_id:
                logger.info(f"Media message without media_id from {message.from_number}")
                return {"status": "skipped_no_media_id"}

            try:
                blob = await whatsapp_api.download_media(media_id)
            except Exception as e:
                logger.warning(f"Download failed for media {media_id} from {message.from_number}: {e}")
                try:
                    await whatsapp_api.send_text_message(message.from_number, "Sorry, I couldn't fetch that file. Please try again.")
                except Exception:
                    pass
                return {"status": "media_download_failed"}

            content_type = (blob.get("content_type") or "").lower()
            size_bytes = int(blob.get("size") or 0)
            if not content_type.startswith("image/"):
                await whatsapp_api.send_text_message(message.from_number, "Please send a clear photo (JPG/PNG).")
                return {"status": "unsupported_media", "content_type": content_type}
            if size_bytes and size_bytes > 10 * 1024 * 1024:
                await whatsapp_api.send_text_message(message.from_number, "That image is too large. Please send a photo under 10MB.")
                return {"status": "too_large"}

            try:
                url = storage_service.upload_bytes(blob.get("bytes") or b"", content_type=content_type, prefix="media")
            except Exception as e:
                logger.warning(f"Storage upload failed for {message.from_number}: {e}")
                await whatsapp_api.send_text_message(message.from_number, "Sorry, I couldn't save that file. Please try again.")
                return {"status": "storage_failed"}

            meta = blob.get("meta") or {}
            media_doc = {
                "whatsapp_number": message.from_number,
                "media_id": media_id,
                "content_type": content_type,
                "size": size_bytes,
                "caption": getattr(message, "media_caption", None),
                "url": url,
                "source": "whatsapp_cloud",
                "raw_meta": meta,
            }
            try:
                await mongo_service.store_media_upload(media_doc)
            except Exception as e:
                logger.warning(f"Failed to record media upload for {message.from_number}: {e}")

            try:
                await whatsapp_api.send_text_message(message.from_number, "Thanks, I received your photo.")
            except Exception:
                pass

            if incoming_doc_id is not None:
                try:
                    await mongo_service.mark_incoming_message_processed(incoming_doc_id)
                except Exception:
                    pass
            return {"status": "media_processed"}
    except Exception:
        logger.exception("Error while handling media message")

    # Handle message with enhanced handler
    try:
        if message.text:  # Only process text messages for now
            await message_handler.handle_message(message)
            logger.info(f"Message processed successfully for {message.from_number}")
            if incoming_doc_id is not None:
                try:
                    await mongo_service.mark_incoming_message_processed(incoming_doc_id)
                except Exception as mark_error:
                    logger.warning(f"Failed to mark incoming message as processed: {mark_error}")
        else:
            logger.info(f"Non-text message received from {message.from_number}, skipping")
    except Exception as e:
        logger.exception(f"Error handling message from {message.from_number}")
        # Send error message to user
        try:
            await whatsapp_api.send_text_message(
                message.from_number,
                "Sorry, I'm having trouble processing your message. Please try again later."
            )
        except Exception as send_error:
            logger.exception("Failed to send error message")

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
    from_number = from_number.split("@")[0]
    text = (payload.get("text") or "").strip()

    # Ignore Baileys stub/system messages that are not real user text
    try:
        raw_msg = (payload.get("rawMessage") or {})
        if isinstance(raw_msg, dict) and raw_msg.get("messageStubType") is not None:
            logger.info(f"Baileys stub/system message from {from_number}, skipping (messageStubType present)")
            return {"status": "skipped_stub"}
    except Exception:
        pass

    # Ignore broadcast/status updates (e.g., status@broadcast) that are not user chats
    try:
        raw_msg = (payload.get("rawMessage") or {})
        remote_jid = ((raw_msg.get("key") or {}).get("remoteJid") or "").lower()
        is_broadcast = bool(raw_msg.get("broadcast")) or remote_jid.endswith("@broadcast") or from_number.lower() == "status"
        if is_broadcast:
            logger.info(f"Baileys broadcast/status message from '{from_number}' (remoteJid='{remote_jid}'), skipping")
            return {"status": "skipped_broadcast"}
    except Exception:
        pass

    if not text:
        raw = payload.get("rawMessage") or {}
        raw_msg = raw.get("message") or {}
        loc = raw_msg.get("locationMessage")
        if loc:
            lat = loc.get("degreesLatitude")
            lng = loc.get("degreesLongitude")
            name = loc.get("name") or ""
            address = loc.get("address") or ""
            parts = []
            location_name = None
            if lat is not None and lng is not None:
                location_service = get_location_service()
                location_name = await location_service.reverse_geocode(lat, lng)
                logger.info(f"Reverse geocoded coordinates ({lat}, {lng}) to: {location_name}")

            # Priority: reverse geocoded name > provided name > address > coordinates
            if location_name:
                parts.append(location_name)
            elif name:
                parts.append(name)
            elif address:
                parts.append(address)

            if lat is not None and lng is not None:
                parts.append(f"({lat},{lng})")

            text = " ".join(parts) or "[location shared]"

    message = WhatsAppMessage(from_number, text)

    logger.info(f"Baileys message from: {message.from_number}")
    logger.info(f"Baileys text: '{message.text}'")

    incoming_doc_id = None
    try:
        incoming_doc_id = await mongo_service.store_incoming_message({
            "from_number": message.from_number,
            "text": message.text,
            "source": "baileys",
        })
    except Exception as e:
        logger.warning(f"Failed to store Baileys incoming message for {message.from_number}: {e}")

    try:
        if message.text:
            await baileys_message_handler.handle_message(message)
            logger.info(f"Baileys message processed successfully for {message.from_number}")
            if incoming_doc_id is not None:
                try:
                    await mongo_service.mark_incoming_message_processed(incoming_doc_id)
                except Exception as mark_error:
                    logger.warning(f"Failed to mark Baileys incoming message as processed: {mark_error}")
        else:
            logger.info(f"Baileys webhook received non-text/empty message from {message.from_number}, skipping")
    except Exception as e:
        logger.exception(f"Error handling Baileys message from {message.from_number}")
        # Best-effort error notification via Baileys transport
        try:
            await baileys_client.send_text_message(
                message.from_number,
                "Sorry, I'm having trouble processing your message. Please try again later.",
            )
        except Exception as send_error:
            logger.exception("Failed to send Baileys error message")

    return {"status": "success"}

@router.post("/process-pending")
async def process_pending_messages(limit: int = 100):
    logger = logging.getLogger(__name__)
    timestamp = datetime.utcnow().isoformat()
    logger.info(f"[{timestamp}] Processing up to {limit} unprocessed incoming messages")
    docs = await mongo_service.get_unprocessed_incoming_messages(limit=limit)
    processed = 0
    errors = 0

    for doc in docs:
        try:
            from_number = (doc.get("from_number") or "").strip()
            text = (doc.get("text") or "").strip()
            if not from_number or not text:
                await mongo_service.mark_incoming_message_processed(doc["_id"])
                continue

            pending_message = WhatsAppMessage(from_number, text)
            source = (doc.get("source") or "cloud").lower()
            if source == "baileys":
                await baileys_message_handler.handle_message(pending_message)
            else:
                await message_handler.handle_message(pending_message)

            await mongo_service.mark_incoming_message_processed(doc["_id"])
            processed += 1
        except Exception as e:
            errors += 1
            logger.exception(f"Error processing pending message {doc.get('_id')}")

    remaining_docs = await mongo_service.get_unprocessed_incoming_messages(limit=1)
    remaining = len(remaining_docs)
    return {
        "status": "success",
        "processed": processed,
        "errors": errors,
        "remaining_unprocessed": remaining,
    }
