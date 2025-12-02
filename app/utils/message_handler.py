from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
import re
import logging
from app.models.message import WhatsAppMessage
from app.utils.location_extractor import get_location_extractor

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
    BOOKING_PENDING_PROVIDER = "booking_pending_provider"  # Waiting for provider response
    
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
        
        # Try to load session from database first, then fall back to memory
        db_session = await self.db.get_session(user_number)
        if db_session:
            session = db_session
            # Convert state string back to enum
            if isinstance(session.get('state'), str):
                try:
                    session['state'] = ConversationState(session['state'])
                except ValueError:
                    session['state'] = ConversationState.NEW
        else:
            session = self.user_sessions.get(user_number, {
                'state': ConversationState.NEW,
                'data': {},
                'last_activity': datetime.utcnow().isoformat()
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
        
        # Update session in both memory and database
        session['last_activity'] = datetime.utcnow().isoformat()
        # Convert ConversationState enum to string for database storage
        session_to_save = session.copy()
        if isinstance(session_to_save.get('state'), ConversationState):
            session_to_save['state'] = session_to_save['state'].value
        self.user_sessions[user_number] = session
        await self.db.save_session(user_number, session_to_save)
    
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
        elif state == ConversationState.BOOKING_PENDING_PROVIDER:
            await self.handle_provider_response(user_number, message_text, session, user)
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
        
        # Get all providers for this service type (no location filter yet)
        all_providers = await self.db.get_providers_by_service(service_type)
        
        if not all_providers:
            await self._log_and_send_response(
                user_number,
                f"Sorry, no {service_type}s available right now.",
                "no_providers_found"
            )
            return
        
        # Extract available locations from providers
        location_extractor = get_location_extractor()
        available_locations = location_extractor.get_available_locations_for_service(all_providers)
        
        # Get user's location
        user_location = user.get('location', '')
        
        # Filter providers by user location if available
        if user_location:
            normalized_location = location_extractor.normalize_user_location(user_location)
            if normalized_location:
                providers = location_extractor.filter_providers_by_location(all_providers, normalized_location)
                
                if providers:
                    # Show providers from user's location immediately
                    buttons = []
                    for provider in providers[:3]:
                        buttons.append({
                            'id': f"provider_{provider['whatsapp_number']}",
                            'title': f"{provider['name']}"
                        })
                    
                    await self._log_and_send_interactive(
                        user_number,
                        f"Available {service_type}s in {normalized_location}",
                        f"Found {len(providers)} provider(s). Pick one:",
                        buttons,
                        "Tap a provider or reply with the number"
                    )
                    
                    session['data']['service_type'] = service_type
                    session['data']['providers'] = providers
                    session['data']['location'] = normalized_location
                    session['state'] = ConversationState.PROVIDER_SELECTION
                    return
        
        # If no user location or no providers in user location, show available locations
        location_buttons = []
        for location in available_locations[:5]:  # Limit to 5 locations
            location_buttons.append({
                'id': f"location_{location.lower().replace(' ', '_')}",
                'title': location
            })
        
        await self._log_and_send_interactive(
            user_number,
            f"Select Your Area",
            f"Where would you like the {service_type}? Choose from available areas:",
            location_buttons,
            "Tap an area or type the name"
        )
        
        session['data']['service_type'] = service_type
        session['data']['all_providers'] = all_providers
        session['state'] = ConversationState.BOOKING_LOCATION
    
    async def handle_booking_service_details(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Ask for specific details about the service issue"""
        service_type = session['data'].get('service_type', 'service')
        session['data']['issue'] = message_text
        
        await self._log_and_send_response(
            user_number,
            f"Got it! When would you like the {service_type}? (e.g., 'tomorrow morning', 'today at 2pm')",
            "booking_ask_time"
        )
        session['state'] = ConversationState.BOOKING_TIME
    
    async def handle_booking_location(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Confirm or update location for the booking"""
        service_type = session['data'].get('service_type', 'service')
        all_providers = session['data'].get('all_providers', [])
        
        # Normalize user's location input
        location_extractor = get_location_extractor()
        normalized_location = location_extractor.normalize_user_location(message_text)
        
        if not normalized_location:
            # Location not recognized, show available options again
            available_locations = location_extractor.get_available_locations_for_service(all_providers)
            
            await self._log_and_send_response(
                user_number,
                f"I didn't recognize '{message_text}'. Available areas are: {', '.join(available_locations[:5])}",
                "location_not_recognized"
            )
            return
        
        # Filter providers by the selected location
        providers = location_extractor.filter_providers_by_location(all_providers, normalized_location)
        
        if not providers:
            await self._log_and_send_response(
                user_number,
                f"Sorry, no {service_type}s available in {normalized_location} right now. Try a different area.",
                "no_providers_found"
            )
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
            return
        
        # Update user's location in database if it changed
        if user and user.get('location') != normalized_location:
            await self.db.update_user(user_number, {'location': normalized_location})
            logger.info(f"Updated user location from {user.get('location')} to {normalized_location}")
        
        # Show available providers in the selected location
        buttons = []
        for provider in providers[:3]:
            buttons.append({
                'id': f"provider_{provider['whatsapp_number']}",
                'title': f"{provider['name']} - {provider.get('location', 'Unknown')}"
            })
        
        await self._log_and_send_interactive(
            user_number,
            f"Available {service_type}s in {normalized_location}",
            f"Found {len(providers)} provider(s). Pick one:",
            buttons,
            "Tap a provider or reply with the number"
        )
        
        session['data']['location'] = normalized_location
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
        """Handle booking time and move to confirmation"""
        # Parse date/time (simplified - you'd want more robust parsing)
        booking_time = self.parse_datetime(message_text)
        
        if not booking_time:
            await self._log_and_send_response(
                user_number,
                "I didn't catch that. Try 'tomorrow morning', 'Dec 15 at 2pm', or 'next Monday'.",
                "invalid_time_format"
            )
            return
        
        # Store booking time and move to confirmation
        session['data']['booking_time'] = booking_time
        
        # Build formatted confirmation summary with all details
        service_type = session['data'].get('service_type', 'service').title()
        issue = session['data'].get('issue', 'Not specified')
        location = session['data'].get('location', 'Not specified')
        provider_location = session['data'].get('selected_provider', {}).get('location', location)
        
        confirmation_msg = (
            f"Perfect! Here's your booking:\n\n"
            f"🛠 Service: {service_type}\n"
            f"📝 Issue: {issue}\n"
            f"📅 Date & Time: {booking_time}\n"
            f"📍 Location: {provider_location}\n"
            f"\n✅ Reply \"Yes\" to confirm or \"No\" to edit."
        )
        
        await self._log_and_send_response(
            user_number,
            confirmation_msg,
            "booking_confirmation_summary"
        )
        
        session['state'] = ConversationState.BOOKING_CONFIRM
    
    async def handle_booking_confirmation(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle final booking confirmation"""
        if message_text.lower() not in ['yes', 'y', 'confirm', 'ok', 'sure']:
            await self._log_and_send_response(
                user_number,
                "No problem! Let's start over. What service do you need?",
                "booking_cancelled"
            )
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
            return
        
        # Create the booking
        booking_data = {
            'booking_id': f"booking_{datetime.utcnow().timestamp()}",
            'user_whatsapp_number': user_number,
            'provider_whatsapp_number': session['data']['selected_provider']['whatsapp_number'],
            'service_type': session['data']['service_type'],
            'date_time': session['data']['booking_time'],
            'issue': session['data'].get('issue', ''),
            'status': 'pending',
            'customer_number': user_number,  # Store for provider response handling
            'customer_name': user.get('name', 'Customer')
        }
        
        success = await self.db.create_booking(booking_data)
        
        if success:
            provider_name = session['data']['selected_provider']['name']
            provider_number = session['data']['selected_provider']['whatsapp_number']
            booking_id = booking_data['booking_id']
            customer_name = user.get('name', 'Customer')
            
            # Send message to customer: booking sent, waiting for confirmation
            await self._log_and_send_response(
                user_number,
                f"✅ Your booking was sent to {provider_name}!\n\n"
                f"We're waiting for their confirmation.\n"
                f"Reference: {booking_id}\n\n"
                f"You'll receive a message once they respond.",
                "booking_sent_waiting"
            )
            
            # Send message to provider: ask to accept/deny booking
            provider_message = (
                f"📋 **New Booking Request**\n\n"
                f"Customer: {customer_name}\n"
                f"Service: {session['data']['service_type']}\n"
                f"Issue: {session['data'].get('issue', 'Not specified')}\n"
                f"Time: {session['data']['booking_time']}\n"
                f"Reference: {booking_id}\n\n"
                f"Reply with:\n"
                f"• 'accept' to confirm\n"
                f"• 'deny' to decline"
            )
            
            await self._log_and_send_response(
                provider_number,
                provider_message,
                "booking_request_to_provider"
            )
            
            # Store booking data for provider response handling
            session['data']['booking_id'] = booking_id
            session['data']['customer_number'] = user_number
            session['state'] = ConversationState.BOOKING_PENDING_PROVIDER
        else:
            await self._log_and_send_response(
                user_number,
                "Oops! Something went wrong. Please try again.",
                "booking_error"
            )
    
    async def handle_provider_response(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle provider's accept/deny response to booking request"""
        booking_id = session['data'].get('booking_id')
        
        if message_text.lower() in ['accept', 'yes', 'confirm', 'ok']:
            # Provider accepted the booking
            # Update booking status to confirmed
            await self.db.update_booking_status(booking_id, 'confirmed')
            
            # Get booking details to notify customer
            booking_data = session['data']
            customer_number = booking_data.get('customer_number')
            provider_name = user.get('name', 'Provider')
            
            # Send confirmation to provider
            await self._log_and_send_response(
                user_number,
                f"✅ **Booking Confirmed!**\n\n"
                f"Reference: {booking_id}\n\n"
                f"You've accepted this booking. Contact the customer to arrange details.",
                "provider_booking_accepted"
            )
            
            # Send confirmation to customer
            if customer_number:
                await self._log_and_send_response(
                    customer_number,
                    f"✅ **Booking Confirmed!**\n\n"
                    f"{provider_name} has accepted your booking!\n"
                    f"Reference: {booking_id}\n\n"
                    f"They will contact you shortly to confirm details.",
                    "customer_booking_confirmed"
                )
            
            # Reset provider session
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
            
        elif message_text.lower() in ['deny', 'no', 'decline', 'reject']:
            # Provider declined the booking
            # Update booking status to declined
            await self.db.update_booking_status(booking_id, 'declined')
            
            # Get customer number from booking data
            customer_number = session['data'].get('customer_number')
            provider_name = user.get('name', 'Provider')
            
            # Send response to provider
            await self._log_and_send_response(
                user_number,
                f"❌ You've declined booking {booking_id}.\n\n"
                f"The customer will be notified and can book with another provider.",
                "provider_booking_declined"
            )
            
            # Notify customer
            if customer_number:
                await self._log_and_send_response(
                    customer_number,
                    f"❌ Sorry, {provider_name} is unable to take this booking.\n\n"
                    f"Reference: {booking_id}\n\n"
                    f"Would you like to try another provider?",
                    "customer_booking_declined"
                )
            
            # Reset provider session
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
        else:
            # Invalid response
            await self._log_and_send_response(
                user_number,
                "Please reply with 'accept' or 'deny' to respond to the booking request.",
                "invalid_provider_response"
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
        """Handle general queries with helpful menu"""
        # For simple greetings or unclear messages, show help menu
        if message_text in ['hi', 'hello', 'hey', 'help', 'menu', 'options', 'start']:
            await self.send_help_menu(user_number)
            return
        
        # Try AI for complex queries
        ai_response = await self.lambda_service.invoke_question_answerer(
            message_text,
            {
                'name': user.get('name'),
                'location': user.get('location'),
                'booking_history': await self.db.get_user_bookings(user_number)
            }
        )
        
        # If AI fails or returns error message, show help menu
        if "technical difficulties" in ai_response.lower() or "error" in ai_response.lower():
            await self.send_help_menu(user_number)
            return
        
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
