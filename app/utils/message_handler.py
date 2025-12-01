from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
import re
import logging
from app.models.message import WhatsAppMessage

logger = logging.getLogger(__name__)

class ConversationState(Enum):
    # Onboarding states
    NEW = "new"
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_LOCATION = "onboarding_location"
    ONBOARDING_PRIVACY = "onboarding_privacy"
    
    # Booking flow states
    SERVICE_SEARCH = "service_search"
    BOOKING_SERVICE_DETAILS = "booking_service_details"  # Ask about specific issue/details
    BOOKING_TIME = "booking_time"
    BOOKING_LOCATION = "booking_location"  # Confirm location for service
    PROVIDER_SELECTION = "provider_selection"
    BOOKING_CONFIRM = "booking_confirm"  # Final confirmation before booking
    
    # Provider registration states
    PROVIDER_REGISTER = "provider_register"
    PROVIDER_REGISTER_NAME = "provider_register_name"
    PROVIDER_REGISTER_SERVICE = "provider_register_service"
    PROVIDER_REGISTER_LOCATION = "provider_register_location"

class MessageHandler:
    """Advanced message handler for WhatsApp conversations"""
    
    def __init__(self, whatsapp_api, dynamodb_service, lambda_service):
        self.whatsapp_api = whatsapp_api
        self.db = dynamodb_service
        self.lambda_service = lambda_service
        self.user_sessions = {}  # In-memory session store (consider Redis for production)
    
    async def _log_and_send_response(self, user_number: str, message: str, response_type: str = "text") -> None:
        """Log bot response and send it to user"""
        logger.info(f"[BOT RESPONSE] To: {user_number}, Type: {response_type}, Message: {message[:100]}...")
        await self.whatsapp_api.send_text_message(user_number, message)
    
    async def _log_and_send_interactive(self, user_number: str, header: str, body: str, buttons: List[Dict], footer: str = None) -> None:
        """Log interactive response and send it to user"""
        logger.info(f"[BOT RESPONSE] To: {user_number}, Type: interactive_buttons, Header: {header}, Body: {body[:50]}...")
        await self.whatsapp_api.send_interactive_buttons(user_number, header, body, buttons, footer)
    
    async def handle_message(self, message: WhatsAppMessage) -> None:
        """Main message handler - routes to appropriate handlers"""
        user_number = message.from_number
        message_text = message.text.strip().lower()
        
        # Get or create user session
        session = self.user_sessions.get(user_number, {
            'state': ConversationState.NEW,
            'data': {},
            'last_activity': datetime.utcnow()
        })
        
        # Get user from database
        user = await self.db.get_user(user_number)
        
        # Route based on conversation state
        if not user or not user.get('onboarding_completed', False):
            await self.handle_onboarding(user_number, message_text, session)
        elif session['state'] == ConversationState.PROVIDER_REGISTER:
            await self.handle_provider_registration(user_number, message_text, session)
        else:
            await self.handle_main_menu(user_number, message_text, session, user)
        
        # Update session
        self.user_sessions[user_number] = session
    
    async def handle_onboarding(self, user_number: str, message_text: str, session: Dict) -> None:
        """Handle new user onboarding flow"""
        state = session['state']
        
        if state == ConversationState.NEW:
            # Start onboarding with combined name + location
            await self._log_and_send_response(
                user_number,
                "👋 Welcome to Hustlr! I'll help you find local service providers.\n\n"
                "To get you set up quickly, please send your *name* and *area* in one message.\n"
                "Example: 'Vincent, Avondale'",
                "onboarding_welcome"
            )
            session['state'] = ConversationState.ONBOARDING_NAME
        
        elif state == ConversationState.ONBOARDING_NAME:
            # Collect name and location from a single message
            raw = message_text.strip()
            parts = re.split(r'[,\n\-]+', raw)
            parts = [p.strip() for p in parts if p.strip()]
            
            if len(parts) >= 2:
                name = parts[0].title()
                location = parts[1].title()
                session['data']['name'] = name
                session['data']['location'] = location
            else:
                # If we can't clearly extract both, ask once more with an example
                await self._log_and_send_response(
                    user_number,
                    "Please send both your *name* and *area* in one message.\n"
                    "Example: 'Vincent, Avondale'",
                    "onboarding_retry"
                )
                return
            
            # Present privacy policy
            privacy_text = (
                "🔒 **Privacy Policy Summary:**\n\n"
                "• We store your name, location, and booking history\n"
                "• We share your info with service providers you choose\n"
                "• We never sell your data to third parties\n"
                "• You can request data deletion anytime\n\n"
                "Do you agree to this privacy policy? (Yes/No)"
            )
            
            await self._log_and_send_response(user_number, privacy_text, "privacy_policy")
            session['state'] = ConversationState.ONBOARDING_PRIVACY
        
        elif state == ConversationState.ONBOARDING_PRIVACY:
            # Handle privacy agreement
            if message_text in ['yes', 'y', 'agree', 'ok', 'sure']:
                # Create user in database
                user_data = {
                    'whatsapp_number': user_number,
                    'name': session['data']['name'],
                    'location': session['data']['location'],
                    'agreed_privacy_policy': True,
                    'onboarding_completed': True,
                    'registered_at': datetime.utcnow().isoformat()
                }
                
                success = await self.db.create_user(user_data)
                
                if success:
                    await self._log_and_send_response(
                        user_number,
                        f"✅ Perfect! You're all set up, {session['data']['name']}!\n\n"
                        "Now you can:\n"
                        "• Search for service providers\n"
                        "• Book appointments\n"
                        "• Get reminders\n\n"
                        "What service are you looking for today?",
                        "onboarding_complete"
                    )
                    session['state'] = ConversationState.SERVICE_SEARCH
                else:
                    await self._log_and_send_response(
                        user_number,
                        "❌ Sorry, there was an issue setting up your account. Please try again later.",
                        "onboarding_error"
                    )
            else:
                await self._log_and_send_response(
                    user_number,
                    "❌ You need to agree to the privacy policy to use Hustlr.\n\n"
                    "Type 'yes' to agree, or 'no' to decline.",
                    "privacy_policy_decline"
                )
    
    async def handle_main_menu(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle main menu and service search"""
        state = session['state']
        
        # Check for register command
        if message_text in ['register', 'become provider', 'join', 'provider']:
            session['state'] = ConversationState.PROVIDER_REGISTER
            await self.handle_provider_registration(user_number, message_text, session)
            return
        
        # Check for help command
        if message_text in ['help', 'menu', 'options']:
            await self.send_help_menu(user_number)
            return
        
        # Try fast booking when the message already includes service + time
        if state == ConversationState.SERVICE_SEARCH:
            handled_fast = await self.try_fast_booking(user_number, message_text, session, user)
            if handled_fast:
                return
        
        # Handle booking flow states
        if state == ConversationState.SERVICE_SEARCH:
            await self.handle_service_search(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_SERVICE_DETAILS:
            await self.handle_booking_service_details(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_LOCATION:
            await self.handle_booking_location(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_TIME:
            await self.handle_booking_time(user_number, message_text, session, user)
        elif state == ConversationState.PROVIDER_SELECTION:
            await self.handle_provider_selection(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_CONFIRM:
            await self.handle_booking_confirmation(user_number, message_text, session, user)
        else:
            # Use AI to understand the message
            await self.handle_ai_response(user_number, message_text, user)
    
    async def try_fast_booking(self, user_number: str, message_text: str, session: Dict, user: Dict) -> bool:
        """Attempt to create a booking directly when message has service + time"""
        service_type = self.extract_service_type(message_text)
        if not service_type:
            return False

        if not self._message_contains_time_hint(message_text):
            return False

        user_location = user.get('location', '')
        providers = await self.db.get_providers_by_service(service_type, user_location)
        
        if not providers:
            await self._log_and_send_response(
                user_number,
                f"Sorry, no {service_type}s available in your area right now. Try a different service or area.",
                "no_providers_found"
            )
            return True

        selected_provider = providers[0]
        booking_time = self.parse_datetime(message_text)

        booking_data = {
            'booking_id': f"booking_{datetime.utcnow().timestamp()}",
            'user_whatsapp_number': user_number,
            'provider_whatsapp_number': selected_provider['whatsapp_number'],
            'service_type': service_type,
            'date_time': booking_time,
            'status': 'pending'
        }

        success = await self.db.create_booking(booking_data)

        if success:
            await self._log_and_send_response(
                user_number,
                f"Booking confirmed! {selected_provider['name']} will help you {service_type} on {booking_time}. We'll send you a reminder.",
                "booking_created"
            )
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
        else:
            await self._log_and_send_response(
                user_number,
                "Oops! Something went wrong. Please try again.",
                "booking_error"
            )

        return True
    
    async def handle_service_search(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle service provider search"""
        # Extract service type from message
        service_type = self.extract_service_type(message_text)
        
        if not service_type:
            # Use AI to understand the request
            ai_response = await self.lambda_service.invoke_question_answerer(
                message_text, 
                {'name': user.get('name'), 'location': user.get('location')}
            )
            await self._log_and_send_response(user_number, ai_response, "ai_response")
            return
        
        # Search for providers
        user_location = user.get('location', '')
        providers = await self.db.get_providers_by_service(service_type, user_location)
        
        if not providers:
            await self._log_and_send_response(
                user_number,
                f"Sorry, no {service_type}s available in your area right now. Try searching for a different service or area.",
                "no_providers_found"
            )
            return
        
        # Present providers as interactive buttons
        buttons = []
        for i, provider in enumerate(providers[:3]):  # Limit to 3 providers
            buttons.append({
                'id': f"provider_{provider['whatsapp_number']}",
                'title': f"{provider['name']} - {provider.get('location', 'Unknown')}"
            })
        
        await self._log_and_send_interactive(
            user_number,
            f"Available {service_type}s",
            f"Found {len(providers)} provider(s) near you. Pick one:",
            buttons,
            "Tap a provider or reply with the number"
        )
        
        session['data']['service_type'] = service_type
        session['data']['providers'] = providers
        session['state'] = ConversationState.PROVIDER_SELECTION
    
    async def handle_provider_selection(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle provider selection for booking"""
        providers = session['data'].get('providers', [])
        
        # Try to match provider by number or name
        selected_provider = None
        
        # Check if user replied with a number
        if message_text.isdigit() and 1 <= int(message_text) <= len(providers):
            selected_provider = providers[int(message_text) - 1]
        else:
            # Check by name match
            for provider in providers:
                if provider['name'].lower() in message_text or message_text in provider['name'].lower():
                    selected_provider = provider
                    break
        
        if not selected_provider:
            await self._log_and_send_response(
                user_number,
                "Please pick a provider from the list above.",
                "invalid_provider_selection"
            )
            return
        
        # Ask for booking time
        session['data']['selected_provider'] = selected_provider
        session['state'] = ConversationState.BOOKING_TIME
        
        await self._log_and_send_response(
            user_number,
            f"Great! You've selected {selected_provider['name']}.\n\nWhen would you like the service? (e.g., 'tomorrow morning', 'Dec 15 at 2pm')",
            "provider_selected"
        )
    
    async def handle_booking_time(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle booking time confirmation"""
        # Parse date/time (simplified - you'd want more robust parsing)
        booking_time = self.parse_datetime(message_text)
        
        if not booking_time:
            await self._log_and_send_response(
                user_number,
                "I didn't catch that. Try 'tomorrow morning', 'Dec 15 at 2pm', or 'next Monday'.",
                "invalid_time_format"
            )
            return
        
        # Create booking
        booking_data = {
            'booking_id': f"booking_{datetime.utcnow().timestamp()}",
            'user_whatsapp_number': user_number,
            'provider_whatsapp_number': session['data']['selected_provider']['whatsapp_number'],
            'service_type': session['data']['service_type'],
            'date_time': booking_time,
            'status': 'pending'
        }
        
        success = await self.db.create_booking(booking_data)
        
        if success:
            provider_name = session['data']['selected_provider']['name']
            await self._log_and_send_response(
                user_number,
                f"Booking confirmed! {provider_name} will help you {session['data']['service_type']} on {booking_time}. We'll send you a reminder.",
                "booking_confirmed"
            )
            
            # Reset session
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
        else:
            await self._log_and_send_response(
                user_number,
                "Oops! Something went wrong. Please try again.",
                "booking_error"
            )
    
    async def handle_provider_registration(self, user_number: str, message_text: str, session: Dict) -> None:
        """Handle service provider registration"""
        state = session['state']
        
        if state == ConversationState.PROVIDER_REGISTER:
            await self._log_and_send_response(
                user_number,
                "👨‍🔧 **Provider Registration**\n\n"
                "Let's get you registered as a service provider.\n\n"
                "What's your full name?",
                "provider_registration_start"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_NAME
        
        elif state == ConversationState.PROVIDER_REGISTER_NAME:
            session['data']['name'] = message_text.title()
            await self._log_and_send_response(
                user_number,
                f"Great, {session['data']['name']}! 🛠️\n\n"
                "What service do you provide? (e.g., plumber, electrician, carpenter, etc.)",
                "provider_registration_service_prompt"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_SERVICE
        
        elif state == ConversationState.PROVIDER_REGISTER_SERVICE:
            service_type = message_text.lower()
            session['data']['service_type'] = service_type
            
            await self._log_and_send_response(
                user_number,
                f"Perfect! 📍\n\n"
                "What area or neighborhood do you serve?",
                "provider_registration_location_prompt"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_LOCATION
        
        elif state == ConversationState.PROVIDER_REGISTER_LOCATION:
            # Complete registration
            provider_data = {
                'whatsapp_number': user_number,
                'name': session['data']['name'],
                'service_type': session['data']['service_type'],
                'location': message_text.title(),
                'status': 'pending',  # Requires approval
                'registered_at': datetime.utcnow().isoformat()
            }
            
            success = await self.db.create_provider(provider_data)
            
            if success:
                await self._log_and_send_response(
                    user_number,
                    f"✅ **Registration Submitted!**\n\n"
                    f"👨‍🔧 Name: {session['data']['name']}\n"
                    f"🛠️ Service: {session['data']['service_type']}\n"
                    f"📍 Area: {message_text.title()}\n\n"
                    f"Your registration is pending review. We'll notify you once approved!\n\n"
                    f"You can start receiving bookings once approved.",
                    "provider_registration_complete"
                )
                
                # Reset session
                session['state'] = ConversationState.SERVICE_SEARCH
                session['data'] = {}
            else:
                await self._log_and_send_response(
                    user_number,
                    "❌ Sorry, there was an issue with your registration. Please try again.",
                    "provider_registration_error"
                )
    
    async def handle_ai_response(self, user_number: str, message_text: str, user: Dict) -> None:
        """Use AI to handle complex queries"""
        ai_response = await self.lambda_service.invoke_question_answerer(
            message_text,
            {
                'name': user.get('name'),
                'location': user.get('location'),
                'booking_history': await self.db.get_user_bookings(user_number)
            }
        )
        
        await self._log_and_send_response(user_number, ai_response, "ai_response")
    
    async def send_help_menu(self, user_number: str) -> None:
        """Send help menu with options"""
        help_text = (
            "🤖 **Hustlr Bot Help**\n\n"
            "**What I can do:**\n"
            "🔧 Find service providers (plumbers, electricians, etc.)\n"
            "📅 Book appointments\n"
            "⏰ Send booking reminders\n"
            "👨‍🔧 Register as a service provider\n\n"
            "**Commands:**\n"
            "• 'plumber', 'electrician', etc. - Search providers\n"
            "• 'register' - Become a provider\n"
            "• 'help' - Show this menu\n"
            "• Just type what you need!\n\n"
            "**Example:**\n"
            "\"I need a plumber in downtown\"\n"
            "\"Book electrician for tomorrow\"\n\n"
            "How can I help you today?"
        )
        
        await self._log_and_send_response(user_number, help_text, "help_menu")
    
    def extract_service_type(self, message_text: str) -> Optional[str]:
        """Extract service type from message"""
        services = {
            'plumber': 'plumber',
            'plumbing': 'plumber',
            'electrician': 'electrician',
            'electrical': 'electrician',
            'electricity': 'electrician',
            'carpenter': 'carpenter',
            'carpentry': 'carpenter',
            'wood': 'carpenter',
            'painter': 'painter',
            'painting': 'painter',
            'cleaner': 'cleaner',
            'cleaning': 'cleaner',
            'mechanic': 'mechanic',
            'repair': 'mechanic',
            'gardener': 'gardener',
            'gardening': 'gardener',
            'landscaping': 'gardener'
        }
        
        for keyword, service in services.items():
            if keyword in message_text:
                return service
        
        return None
    
    def parse_datetime(self, message_text: str) -> Optional[str]:
        """Simple datetime parsing (would need enhancement for production)"""
        # This is a simplified implementation
        # In production, you'd want a more robust date parser
        
        message_lower = message_text.lower()
        
        # Handle simple cases
        if 'tomorrow' in message_lower:
            return "Tomorrow"
        elif 'today' in message_lower:
            return "Today"
        elif 'morning' in message_lower:
            return "Morning"
        elif 'afternoon' in message_lower:
            return "Afternoon"
        elif 'evening' in message_lower:
            return "Evening"
        
        # Return the original text if we can't parse it
        return message_text

    def _message_contains_time_hint(self, message_text: str) -> bool:
        """Heuristic check for time-related words/patterns in a message"""
        text = message_text.lower()
        keywords = [
            'today', 'tomorrow', 'tonight', 'morning', 'afternoon', 'evening',
            'next ', 'am', 'pm'
        ]
        if any(k in text for k in keywords):
            return True
        # Simple time pattern like 10, 10am, 10:00, 10:00am
        if re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", text):
            return True
        return False
