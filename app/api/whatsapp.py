from fastapi import APIRouter, Request, status, HTTPException, Body
from app.utils.webhook_verifier import verify_whatsapp_signature
from app.models.message import WhatsAppMessage

router = APIRouter()

@router.post("/webhook")
async def receive_whatsapp_message(
    request: Request,
    payload: dict = Body(...)
):
    """
    Endpoint to receive WhatsApp webhook messages.
    Verifies request (if you implement verification), parses payload, and processes message.
    """
    # (Optional) Signature verification logic if Meta needs it
    # headers = request.headers
    # if not verify_whatsapp_signature(headers, payload):
    #     raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Verification failed.")

    # Parse incoming WhatsApp message
    message = WhatsAppMessage.from_webhook(payload)
    # Implement your logic (respond, save to DB, etc.)
    print(f"Received message: {message}")

    # Respond to WhatsApp so Meta knows you received successfully
    return {"status": "success"}
