# Hustlr — WhatsApp Chatbot Platform

Hustlr is a WhatsApp chatbot backend built with FastAPI. It aims to connect users with local service providers (plumbers, electricians, etc.), handling onboarding, provider registration, bookings, and automated reminders — all via WhatsApp.

This repository currently includes the FastAPI app skeleton, a WhatsApp webhook endpoint, a simple message model, and placeholders for future modules (providers, bookings, reminders, and scheduler). See `docs/prd.txt` for the detailed product scope.

## Tech Stack
- **Python** (FastAPI)
- **FastAPI** for the web API
- **Uvicorn** (recommended) as the ASGI server
- **MongoDB** planned (models and flows are scaffolded but not implemented yet)

## Project Structure
```
hustlr/
  app/
    main.py                      # FastAPI app entrypoint
    api/
      whatsapp.py                # POST webhook endpoint for WhatsApp messages
      bookings.py                # (placeholder)
      service_providers.py       # (placeholder)
    models/
      message.py                 # Simple parser for incoming WhatsApp messages
      booking.py                 # (placeholder)
      provider.py                # (placeholder)
      user.py                    # (placeholder)
    utils/
      webhook_verifier.py        # Placeholder for Meta signature verification
      reminders.py               # (placeholder)
      scheduler.py               # (placeholder)
  docs/
    prd.txt                      # Product Requirements Document
  config.py                      # (placeholder)
  requirements.txt               # Currently empty (see install step below)
  .env                           # Local environment variables (not committed)
  README.md
```

## Quick Start

### Prerequisites
- Python 3.10+ (recommended)
- pip

### Setup
1. Create and activate a virtual environment (optional but recommended).
2. Install runtime packages:
   ```bash
   pip install fastapi uvicorn
   ```
3. Create a `.env` file in the project root with your WhatsApp Cloud API settings (see Environment Variables below). Keep this file private.

### Run the API (local)
```bash
uvicorn app.main:app --reload
```

The API will start at `http://127.0.0.1:8000`.

### Basic Health Check
- GET `http://127.0.0.1:8000/` → `{ "message": "Hustlr WhatsApp Bot is running" }`

## API Endpoints

- **POST** `/api/whatsapp/webhook`
  - Receives WhatsApp webhook notifications from the Meta (Facebook) Graph API.
  - Parses the message payload into a simple model (`WhatsAppMessage`).
  - Currently logs the received message and returns `{ "status": "success" }`.

Example payload shape the parser expects (simplified):
```json
{
  "entry": [
    {
      "changes": [
        {
          "value": {
            "messages": [
              { "from": "15551234567", "text": { "body": "hello" } }
            ]
          }
        }
      ]
    }
  ]
}
```

Notes:
- The GET webhook verification handshake used by Meta (with `hub.challenge`) is not implemented yet. You will need to add a GET endpoint for verification before connecting this to a live WhatsApp app.
- `utils/webhook_verifier.py` is a placeholder — implement signature verification if you enable it for your app.

## Environment Variables
Define the following in `.env` (do not commit real secrets):

- `WHATSAPP_API_URL` — Graph API messages endpoint for your phone number ID
- `WHATSAPP_ACCESS_TOKEN` — WhatsApp Cloud API access token
- `WHATSAPP_PHONE_NUMBER_ID` — Your WhatsApp Business phone number ID
- `WHATSAPP_VERIFY_TOKEN` — Token you choose for initial webhook verification
- `WHATSAPP_BUSINESS_ACCOUNT_ID` — Your business account ID (if needed)

Keep `.env` private. `.gitignore` already excludes it.

## Development Notes & Roadmap
- Implement GET webhook verification for Meta (returning `hub.challenge`).
- Add outbound message sending to WhatsApp via Graph API.
- Integrate MongoDB for users, providers, and bookings (see `docs/prd.txt`).
- Flesh out API modules:
  - `service_providers.py`: registration and availability flows
  - `bookings.py`: create/confirm/complete bookings
  - `reminders.py` & `scheduler.py`: scheduled reminders for upcoming bookings
- Replace placeholders in `config.py` with a proper settings module (e.g., Pydantic BaseSettings).
- Add proper logging, error handling, and tests.
- Populate `requirements.txt` and pin versions.

## Security
- Do not log or commit secrets.
- Consider implementing request signature verification if required by your Meta app settings (`utils/webhook_verifier.py`).

## License
No license file is provided. If you plan to open-source, add a LICENSE file. Otherwise treat this as private, proprietary code.

 