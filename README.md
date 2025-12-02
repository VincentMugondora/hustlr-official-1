# Hustlr — WhatsApp Service Booking Chatbot

Hustlr is a comprehensive WhatsApp chatbot platform built with FastAPI that connects users with local service providers (plumbers, electricians, carpenters, etc.). The bot handles the complete user journey: onboarding, service discovery, provider selection, booking management, and confirmation — all through natural WhatsApp conversations.

**Status:** Production-ready core features with active development on advanced features.

---

## Table of Contents
- [Overview](#overview)
- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [API Endpoints](#api-endpoints)
- [Core Features](#core-features)
- [Architecture](#architecture)
- [Database Schema](#database-schema)
- [Conversation Flow](#conversation-flow)
- [Development](#development)
- [Deployment](#deployment)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

Hustlr simplifies how users find and book local service providers through WhatsApp. Instead of searching online or making phone calls, users can:

1. **Chat naturally** with the bot about what they need
2. **Browse providers** in their area with real-time availability
3. **Book appointments** with confirmed providers
4. **Receive confirmations** and reminders automatically

The bot maintains conversation context across sessions, understands user intent, and guides them through the booking process seamlessly.

### Key Differentiators
- **Persistent Sessions**: Bot remembers conversation context across restarts
- **Accurate Location Handling**: Uses reverse geocoding and provider database for real locations (no hallucination)
- **Natural Conversations**: Feels like texting a helpful friend, not a program
- **Two-Way Confirmations**: Both users and providers confirm bookings
- **Flexible Booking Flow**: Users can modify details or restart at any time

---

## Features

### User Features
- **Smart Onboarding**: Collect name and location in one message
- **Service Search**: Find providers by service type (plumber, electrician, etc.)
- **Location-Based Filtering**: Automatic location detection and provider filtering
- **Provider Selection**: Browse available providers with details
- **Booking Management**: Schedule appointments with time and issue description
- **Booking Confirmation**: Review all details before confirming
- **Session Persistence**: Bot remembers your preferences and booking history

### Provider Features
- **Easy Registration**: Simple 3-step registration process
- **Booking Requests**: Receive booking requests with customer details
- **Accept/Deny**: Confirm or decline bookings with one message
- **Service Area Management**: Define areas you serve

### Bot Capabilities
- **Intent Detection**: Understands service requests in natural language
- **Reverse Geocoding**: Converts GPS coordinates to accurate locations
- **Conversation State Management**: Tracks multi-step booking flows
- **Error Recovery**: Gracefully handles invalid input and API failures
- **Fallback Responses**: Helpful guidance when uncertain

---

## Tech Stack

### Backend
- **Python 3.10+** - Core language
- **FastAPI** - Web framework for API endpoints
- **Uvicorn** - ASGI server
- **Pydantic** - Data validation and settings management

### Database & Storage
- **MongoDB** - User, provider, and booking data
- **DynamoDB** - Session persistence (optional, with MongoDB fallback)

### External Services
- **WhatsApp Cloud API** - Message sending and receiving
- **Baileys** (Node.js) - Alternative WhatsApp transport
- **AWS Bedrock** - LLM for intent understanding (optional)
- **AWS Lambda** - Serverless functions (optional)
- **Geopy/Nominatim** - Reverse geocoding for location accuracy

### Development Tools
- **pytest** - Testing framework
- **black** - Code formatting
- **flake8** - Linting
- **python-dotenv** - Environment variable management

---

## Project Structure

```
hustlr-official-1/
├── app/
│   ├── main.py                          # FastAPI app entry point
│   ├── db.py                            # MongoDB connection
│   ├── api/
│   │   ├── whatsapp.py                  # WhatsApp webhook endpoints
│   │   ├── bookings.py                  # Booking management endpoints
│   │   ├── service_providers.py         # Provider management endpoints
│   │   └── users.py                     # User management endpoints
│   ├── models/
│   │   ├── message.py                   # WhatsApp message model
│   │   ├── booking.py                   # Booking data model
│   │   ├── provider.py                  # Provider data model
│   │   └── user.py                      # User data model
│   └── utils/
│       ├── message_handler.py           # Core conversation logic
│       ├── whatsapp_cloud_api.py        # WhatsApp API client
│       ├── location_service.py          # Reverse geocoding service
│       ├── location_extractor.py        # Location normalization
│       ├── mongo_service.py             # MongoDB operations
│       ├── dynamodb_service.py          # DynamoDB operations
│       ├── aws_lambda.py                # Lambda service client
│       ├── baileys_client.py            # Baileys WhatsApp client
│       └── webhook_verifier.py          # Meta signature verification
├── baileys-service/                     # Node.js WhatsApp service
│   ├── package.json
│   ├── index.js
│   └── sessions/
├── docs/
│   ├── prd.txt                          # Product requirements
│   └── import_providers.py              # Provider data import script
├── config.py                            # Configuration settings
├── requirements.txt                     # Python dependencies
├── .env                                 # Environment variables (not committed)
├── .env.example                         # Example environment file
├── .gitignore
└── README.md
```

---

## Quick Start

### Prerequisites
- Python 3.10+
- Node.js 16+ (for Baileys WhatsApp service)
- MongoDB instance (local or Atlas)
- WhatsApp Business Account with API access
- Git

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd hustlr-official-1
   ```

2. **Create Python virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

5. **Set up Node.js Baileys service** (optional, for WhatsApp Web transport)
   ```bash
   cd baileys-service
   npm install
   cd ..
   ```

6. **Start MongoDB** (if running locally)
   ```bash
   mongod
   ```

### Running the Application

**Terminal 1: Start FastAPI backend**
```bash
uvicorn app.main:app --reload
```
API runs at `http://127.0.0.1:8000`

**Terminal 2: Start Baileys WhatsApp service** (optional)
```bash
cd baileys-service
npm start
```
Service runs at `http://localhost:3000`

### Verify Installation

- Health check: `curl http://127.0.0.1:8000/`
- API docs: `http://127.0.0.1:8000/docs`
- Logs: Check console output for incoming messages

---

## Configuration

### Environment Variables

Create a `.env` file in the project root:

```env
# MongoDB
MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/?retryWrites=true&w=majority
MONGODB_DB_NAME=hustlr

# WhatsApp Cloud API
WHATSAPP_API_URL=https://graph.instagram.com/v18.0/YOUR_PHONE_ID/messages
WHATSAPP_ACCESS_TOKEN=your_access_token_here
WHATSAPP_PHONE_NUMBER_ID=your_phone_number_id
WHATSAPP_VERIFY_TOKEN=your_verify_token
WHATSAPP_BUSINESS_ACCOUNT_ID=your_business_account_id

# AWS (optional)
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_REGION=us-east-1
AWS_DYNAMODB_SESSIONS_TABLE=hustlr-sessions
AWS_DYNAMODB_USERS_TABLE=hustlr-users
AWS_DYNAMODB_PROVIDERS_TABLE=hustlr-providers
AWS_DYNAMODB_BOOKINGS_TABLE=hustlr-bookings

# AWS Bedrock (optional, for LLM)
USE_BEDROCK_INTENT=false
BEDROCK_MODEL_ID=anthropic.claude-3-sonnet-20240229-v1:0

# AWS Lambda (optional)
AWS_LAMBDA_QUESTION_ANSWERER_FUNCTION_NAME=your_function_name

# Geopy (for reverse geocoding)
GEOPY_NOMINATIM_USER_AGENT=hustlr-bot
```

### Configuration File

Edit `config.py` to customize:
- Database connection strings
- API timeouts
- Message limits
- Service types
- Location boundaries

---

## API Endpoints

### WhatsApp Webhooks

**POST** `/api/whatsapp/webhook`
- Receives WhatsApp Cloud API messages
- Validates Meta signature
- Processes message and routes to handlers
- Returns: `{ "status": "success" }`

**GET** `/api/whatsapp/webhook`
- Webhook verification handshake
- Required for initial Meta setup
- Returns: `hub.challenge` value

**POST** `/api/whatsapp/baileys-webhook`
- Alternative webhook for Baileys transport
- Receives messages from WhatsApp Web
- Same processing as Cloud API

### User Endpoints

**GET** `/api/users/{phone_number}`
- Retrieve user profile
- Returns: User data with location and booking history

**POST** `/api/users`
- Create new user
- Body: `{ "phone_number": "...", "name": "...", "location": "..." }`

**PUT** `/api/users/{phone_number}`
- Update user profile
- Body: `{ "location": "...", "name": "..." }`

### Provider Endpoints

**GET** `/api/providers/service/{service_type}`
- List providers by service type
- Query params: `location` (optional)
- Returns: Array of provider objects

**POST** `/api/providers`
- Register new provider
- Body: `{ "name": "...", "service_type": "...", "location": "..." }`

**GET** `/api/providers/{phone_number}`
- Get provider details
- Returns: Provider profile and service areas

### Booking Endpoints

**POST** `/api/bookings`
- Create new booking
- Body: `{ "user_number": "...", "provider_number": "...", "service_type": "...", "date_time": "...", "issue": "..." }`
- Returns: Booking ID and reference number

**GET** `/api/bookings/{booking_id}`
- Retrieve booking details
- Returns: Full booking information

**PUT** `/api/bookings/{booking_id}/status`
- Update booking status
- Body: `{ "status": "confirmed|declined|completed" }`

**GET** `/api/bookings/user/{phone_number}`
- List user's bookings
- Returns: Array of booking objects

---

## Core Features

### 1. Conversation State Management

The bot tracks conversation state through `ConversationState` enum:

```python
class ConversationState(Enum):
    NEW = "new"
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_PRIVACY = "onboarding_privacy"
    SERVICE_SEARCH = "service_search"
    BOOKING_LOCATION = "booking_location"
    PROVIDER_SELECTION = "provider_selection"
    BOOKING_SERVICE_DETAILS = "booking_service_details"
    BOOKING_TIME = "booking_time"
    BOOKING_CONFIRM = "booking_confirm"
    BOOKING_PENDING_PROVIDER = "booking_pending_provider"
    PROVIDER_REGISTER = "provider_register"
```

### 2. Session Persistence

Sessions are stored in MongoDB/DynamoDB with:
- User phone number (key)
- Current conversation state
- Session data (service type, location, selected provider, etc.)
- Last activity timestamp
- Updated timestamp

Sessions survive server restarts and allow multi-instance deployments.

### 3. Location Accuracy

**LocationExtractor** (`location_extractor.py`):
- Extracts cities/towns from provider database
- Normalizes user input to match database locations
- Supports Zimbabwe cities and suburbs
- Filters providers by exact location match
- Never hallucinated locations

**LocationService** (`location_service.py`):
- Reverse geocodes GPS coordinates to location names
- Uses Nominatim (OpenStreetMap)
- Validates coordinates within Zimbabwe bounds
- Returns format: "Suburb, City"

### 4. Message Handler

**MessageHandler** (`message_handler.py`):
- Routes messages based on conversation state
- Handles onboarding flow
- Manages booking flow
- Handles provider registration
- Provides AI-powered guidance
- Logs all interactions

### 5. Intent Detection

Extracts service types from user messages:
- Plumber, electrician, carpenter, cleaner, etc.
- Handles variations (plumbing → plumber)
- Falls back to AI for complex queries

---

## Architecture

### Message Flow

```
WhatsApp User
    ↓
WhatsApp Cloud API / Baileys
    ↓
FastAPI Webhook Endpoint
    ↓
Message Parser (WhatsAppMessage)
    ↓
Message Handler
    ├→ Load Session (MongoDB/DynamoDB)
    ├→ Get User Profile (MongoDB)
    ├→ Route by Conversation State
    ├→ Handle Specific State
    ├→ Update Session
    └→ Send Response via WhatsApp API
    ↓
WhatsApp User
```

### Service Layers

**API Layer** (`app/api/`)
- HTTP endpoints
- Request/response handling
- Error handling

**Handler Layer** (`message_handler.py`)
- Conversation logic
- State management
- Business rules

**Service Layer** (`app/utils/`)
- Database operations
- WhatsApp API client
- Location services
- AWS services

**Model Layer** (`app/models/`)
- Data validation
- Type definitions

---

## Database Schema

### Users Collection
```json
{
  "_id": ObjectId,
  "whatsapp_number": "263777530322",
  "name": "Vincent Mugondora",
  "location": "Mufakose, Harare",
  "agreed_privacy_policy": true,
  "onboarding_completed": true,
  "registered_at": "2025-12-01T10:00:00Z",
  "updated_at": "2025-12-02T10:00:00Z"
}
```

### Providers Collection
```json
{
  "_id": ObjectId,
  "whatsapp_number": "263718275163",
  "name": "Seagate Plumbers",
  "service_type": "plumber",
  "location": "6 Weale Road, Milton Park, Harare",
  "status": "active",
  "registered_at": "2025-12-01T10:00:00Z"
}
```

### Bookings Collection
```json
{
  "_id": ObjectId,
  "booking_id": "booking_1764662906.123",
  "user_whatsapp_number": "263777530322",
  "provider_whatsapp_number": "263718275163",
  "service_type": "plumber",
  "issue": "Bathroom sink is blocked",
  "date_time": "Tomorrow at 3pm",
  "status": "confirmed",
  "created_at": "2025-12-01T10:00:00Z",
  "updated_at": "2025-12-01T10:30:00Z"
}
```

### Sessions Collection
```json
{
  "_id": ObjectId,
  "whatsapp_number": "263777530322",
  "state": "booking_confirm",
  "data": {
    "service_type": "plumber",
    "location": "Harare",
    "issue": "Drain unclogging",
    "booking_time": "Tomorrow at 3pm",
    "selected_provider": {...},
    "providers": [...]
  },
  "last_activity": "2025-12-01T10:00:00Z",
  "updated_at": "2025-12-01T10:00:00Z"
}
```

---

## Conversation Flow

### User Booking Flow

```
1. User: "I need a plumber"
   Bot: "Great! I can help you find a plumber. Where are you located?"

2. User: "Harare"
   Bot: "Found 5 plumbers in Harare. Which one interests you?"
   (Shows provider list with buttons)

3. User: "Seagate Plumbers"
   Bot: "What's the issue? (e.g., blocked drain, leaking pipe)"

4. User: "My bathroom sink is blocked"
   Bot: "When would you like the service? (e.g., tomorrow at 3pm)"

5. User: "Tomorrow at 3pm"
   Bot: "Here's your booking:
         Service: Plumber
         Issue: Bathroom sink is blocked
         Date & Time: Tomorrow at 3pm
         Location: 6 Weale Road, Milton Park, Harare
         
         Reply 'Yes' to confirm or 'No' to edit."

6. User: "Yes"
   Bot: "Your booking was sent to Seagate Plumbers!
         We're waiting for their confirmation.
         Reference: booking_1764662906.123"

7. Provider: "accept"
   Bot (to Provider): "Booking Confirmed! Reference: booking_1764662906.123"
   Bot (to User): "Booking Confirmed! Seagate Plumbers has accepted your booking!"
```

### Provider Registration Flow

```
1. User: "register"
   Bot: "Provider Registration. What's your full name?"

2. User: "John Smith"
   Bot: "Great, John Smith! What service do you provide?"

3. User: "plumber"
   Bot: "Perfect! What area or neighborhood do you serve?"

4. User: "Harare"
   Bot: "Registration Submitted!
         Name: John Smith
         Service: plumber
         Area: Harare
         
         Your registration is pending review."
```

---

## Development

### Running Tests

```bash
pytest tests/
pytest tests/ -v  # Verbose
pytest tests/test_message_handler.py  # Specific file
```

### Code Style

```bash
# Format code
black app/

# Lint code
flake8 app/

# Type checking
mypy app/
```

### Adding New Service Types

1. Update `extract_service_type()` in `message_handler.py`
2. Add service to provider database
3. Test with sample providers

### Adding New Conversation States

1. Add state to `ConversationState` enum
2. Create handler method in `MessageHandler`
3. Add routing in `handle_main_menu()`
4. Test conversation flow

### Debugging

Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

Check logs:
```bash
tail -f hustlr_bot.log
```

---

## Deployment

### Docker Deployment

```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Environment Setup

1. Set all environment variables in production
2. Use managed MongoDB (Atlas)
3. Use AWS services (Lambda, DynamoDB, Bedrock)
4. Enable HTTPS for webhooks
5. Configure firewall rules

### Scaling

- Use load balancer for multiple API instances
- Use DynamoDB for session persistence across instances
- Use AWS Lambda for serverless scaling
- Monitor with CloudWatch

---

## Security

### Best Practices

1. **Never commit secrets** - Use `.env` file (in `.gitignore`)
2. **Validate all input** - Use Pydantic models
3. **Verify Meta signatures** - Implement `webhook_verifier.py`
4. **Rate limiting** - Implement per-user/IP limits
5. **HTTPS only** - Use TLS for all endpoints
6. **Sanitize logs** - Don't log sensitive data
7. **Access control** - Validate user ownership of data

### Sensitive Data

- WhatsApp access tokens
- Database credentials
- AWS keys
- User personal information
- Booking details

---

## Troubleshooting

### Bot Not Responding

1. Check webhook endpoint is receiving messages
2. Verify MongoDB connection
3. Check logs for errors
4. Verify WhatsApp API credentials

### Location Not Accurate

1. Check reverse geocoding service
2. Verify provider location data in database
3. Check Zimbabwe bounds configuration
4. Test with known coordinates

### Session Lost

1. Check MongoDB/DynamoDB connection
2. Verify session TTL settings
3. Check server logs for errors
4. Ensure session data is being saved

### Provider Not Receiving Booking

1. Verify provider phone number in database
2. Check WhatsApp API credentials
3. Verify provider is registered and active
4. Check message sending logs

---

## Contributing

1. Create feature branch: `git checkout -b feature/your-feature`
2. Make changes and test
3. Commit: `git commit -m "Add your feature"`
4. Push: `git push origin feature/your-feature`
5. Create Pull Request

---

## License

This project is proprietary and confidential. Unauthorized copying or distribution is prohibited.

For licensing inquiries, contact the project maintainers.

---

## Support & Contact

For issues, questions, or suggestions:
- Create an issue in the repository
- Contact the development team
- Check existing documentation

---

## Changelog

### v1.0.0 (Current)
- Complete booking flow with multi-step confirmation
- Session persistence with MongoDB/DynamoDB
- Accurate location handling with reverse geocoding
- Natural conversation flow without emojis
- Two-way booking confirmations
- Provider registration and management
- Comprehensive error handling and fallbacks

### Planned Features
- Booking reminders and notifications
- Rating and review system
- Payment integration
- Multi-language support
- Advanced analytics and reporting
- Mobile app integration

---

**Last Updated:** December 2, 2025
**Maintained By:** Hustlr Development Team