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
    BOOKING_RESUME_DECISION = "booking_resume_decision"
    
    # Provider registration states
    PROVIDER_REGISTER = "provider_register"
    PROVIDER_REGISTER_NAME = "provider_register_name"
    PROVIDER_REGISTER_SERVICE = "provider_register_service"
    PROVIDER_REGISTER_LOCATION = "provider_register_location"
    PROVIDER_REGISTER_BUSINESS = "provider_register_business"
    PROVIDER_REGISTER_CONTACT = "provider_register_contact"

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
        
        # Store bot response in conversation history for context
        try:
            await self.db.store_message(user_number, "assistant", message)
        except Exception as e:
            logger.warning(f"Could not store bot message in history for {user_number}: {e}")
    
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
        
        # Store user message in conversation history for context
        try:
            await self.db.store_message(user_number, "user", message_text)
        except Exception as e:
            logger.warning(f"Could not store user message in history for {user_number}: {e}")
        
        # Route based on conversation state
        current_state = session['state']
        if current_state == ConversationState.BOOKING_PENDING_PROVIDER:
            # Allow providers to respond to booking requests even if they
            # haven't gone through user onboarding
            await self.handle_main_menu(user_number, message_text, session, user or {})
        elif not user or not user.get('onboarding_completed', False):
            await self.handle_onboarding(user_number, message_text, session)
        elif current_state == ConversationState.PROVIDER_REGISTER:
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
                "Welcome to Hustlr! I'll help you find local service providers.\n\n"
                "To get started, send your name and area in one message.\n"
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
                location_raw = parts[1]
                # Normalize user location so suburbs/towns map to the
                # nearest known service area (e.g. Aspindale -> Harare).
                location_extractor = get_location_extractor()
                normalized_location = location_extractor.normalize_user_location(location_raw)
                if normalized_location:
                    location = normalized_location
                else:
                    location = location_raw.title()
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
                "Privacy Policy:\n\n"
                "- We store your name, location, and booking history\n"
                "- We share your info with service providers you choose\n"
                "- We never sell your data to third parties\n"
                "- You can request data deletion anytime\n\n"
                "Do you agree? (Yes/No)"
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
                        f"Great! You're all set, {session['data']['name']}!\n\n"
                        "You can now search for service providers and book appointments.\n\n"
                        "What service are you looking for?",
                        "onboarding_complete"
                    )
                    session['state'] = ConversationState.SERVICE_SEARCH
                else:
                    await self._log_and_send_response(
                        user_number,
                        "Sorry, there was an issue setting up your account. Please try again later.",
                        "onboarding_error"
                    )
            else:
                await self._log_and_send_response(
                    user_number,
                    "You need to agree to the privacy policy to use Hustlr.\n\n"
                    "Type 'yes' to agree, or 'no' to decline.",
                    "privacy_policy_decline"
                )
    
    async def handle_main_menu(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle main menu and service search"""
        state = session['state']
        
        # Default any legacy/"new" state for onboarded users into service search
        if state == ConversationState.NEW:
            session['state'] = ConversationState.SERVICE_SEARCH
            state = session['state']
        
        if state == ConversationState.BOOKING_RESUME_DECISION:
            await self.handle_booking_resume_decision(user_number, message_text, session, user)
            return
        
        booking_progress_states = {
            ConversationState.BOOKING_SERVICE_DETAILS,
            ConversationState.BOOKING_LOCATION,
            ConversationState.PROVIDER_SELECTION,
            ConversationState.BOOKING_TIME,
            ConversationState.BOOKING_CONFIRM,
        }
        
        if state in booking_progress_states:
            last_activity_str = session.get('last_activity')
            if last_activity_str:
                try:
                    last_activity = datetime.fromisoformat(last_activity_str)
                    if (datetime.utcnow() - last_activity).total_seconds() > 600:
                        session.setdefault('data', {})
                        session['data']['previous_state'] = state.value if isinstance(state, ConversationState) else state
                        session['state'] = ConversationState.BOOKING_RESUME_DECISION
                        await self._log_and_send_response(
                            user_number,
                            "You still have a booking in progress. Do you want to continue it? Reply 'yes' to continue or 'no' to start a new booking.",
                            "booking_resume_prompt"
                        )
                        return
                except Exception:
                    pass
        
        # Check for register command
        provider_intent_phrases = [
            'register as provider',
            'register as a provider',
            'register as a service provider',
            'become provider',
            'become a provider',
            'become a service provider',
            'service provider',
            'join as provider',
            'join as a provider',
        ]
        if (
            message_text in ['register', 'become provider', 'join', 'provider']
            or any(phrase in message_text for phrase in provider_intent_phrases)
        ):
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
    
    def detect_problem_statement(self, message_text: str) -> Optional[str]:
        """
        Detect problem statements like 'I have a leaking pipe' and map to service type.
        
        Returns:
            Service type (e.g., 'plumber') or None if no problem detected.
        """
        problem_keywords = {
            'plumber': [
                'leaking pipe', 'burst pipe', 'blocked drain', 'burst tap', 'leaking tap',
                'water leak', 'burst water', 'clogged drain', 'blocked toilet', 'leaking toilet',
                'plumbing issue', 'plumbing problem', 'water problem', 'drainage', 'sewage',
            ],
            'electrician': [
                'electrical fault', 'power cut', 'no electricity', 'broken outlet', 'broken socket',
                'electrical problem', 'electrical issue', 'power issue', 'light not working',
                'switch not working', 'electrical wiring', 'electrical damage',
            ],
            'cleaner': [
                'need cleaning', 'need a clean', 'house dirty', 'office dirty', 'place dirty',
                'cleaning needed', 'need to clean', 'dirty house', 'dirty office',
            ],
            'carpenter': [
                'broken door', 'broken window', 'broken furniture', 'furniture broken',
                'door broken', 'window broken', 'wood damage', 'carpentry', 'woodwork',
                'cabinet broken', 'shelf broken', 'table broken',
            ],
            'painter': [
                'need painting', 'need paint', 'walls need paint', 'house needs paint',
                'repainting', 'paint job', 'painting needed', 'paint damage',
            ],
        }
        
        message_lower = message_text.lower()
        
        # Check each service type's keywords
        for service_type, keywords in problem_keywords.items():
            for keyword in keywords:
                if keyword in message_lower:
                    logger.info(f"Problem detected: '{keyword}' → service type: {service_type}")
                    return service_type
        
        return None
    
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
        
        # If no explicit service type, try to detect problem statements
        if not service_type:
            service_type = self.detect_problem_statement(message_text)
        
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
            f"Here's your booking:\n\n"
            f"Service: {service_type}\n"
            f"Issue: {issue}\n"
            f"Date & Time: {booking_time}\n"
            f"Location: {provider_location}\n"
            f"\nReply \"Yes\" to confirm or \"No\" to edit."
        )
        
        await self._log_and_send_response(
            user_number,
            confirmation_msg,
            "booking_confirmation_summary"
        )
        
        session['state'] = ConversationState.BOOKING_CONFIRM
    
    async def handle_booking_resume_decision(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        text = message_text.strip().lower()
        if text in ['yes', 'y', 'continue', 'resume', 'ok', 'sure']:
            previous_state_str = None
            data = session.get('data') or {}
            if 'previous_state' in data:
                previous_state_str = data.pop('previous_state')
            if previous_state_str:
                try:
                    previous_state = ConversationState(previous_state_str)
                except ValueError:
                    previous_state = ConversationState.SERVICE_SEARCH
            else:
                previous_state = ConversationState.SERVICE_SEARCH
            session['state'] = previous_state
            await self._log_and_send_response(
                user_number,
                "Okay, let's continue your last booking. You can answer my last question to carry on.",
                "booking_resume_continue"
            )
            return
        if text in ['no', 'n', 'cancel', 'start over', 'restart', 'new']:
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
            await self._log_and_send_response(
                user_number,
                "No problem. Let's start a new booking. What service do you need?",
                "booking_resume_start_over"
            )
            return
        await self._log_and_send_response(
            user_number,
            "Please reply with 'yes' to continue your last booking or 'no' to start a new one.",
            "booking_resume_invalid"
        )
    
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
                f"Your booking was sent to {provider_name}!\n\n"
                f"We're waiting for their confirmation.\n"
                f"Reference: {booking_id}\n\n"
                f"You'll get a message once they respond.",
                "booking_sent_waiting"
            )
            
            # Send message to provider: ask to accept/deny booking
            provider_message = (
                f"New Booking Request\n\n"
                f"Customer: {customer_name}\n"
                f"Service: {session['data']['service_type']}\n"
                f"Issue: {session['data'].get('issue', 'Not specified')}\n"
                f"Time: {session['data']['booking_time']}\n"
                f"Reference: {booking_id}\n\n"
                f"Reply with 'accept' to confirm or 'deny' to decline"
            )
            
            await self._log_and_send_response(
                provider_number,
                provider_message,
                "booking_request_to_provider"
            )
            
            # Store booking data for provider response handling in provider session
            provider_session = await self.db.get_session(provider_number) or {
                'state': ConversationState.BOOKING_PENDING_PROVIDER,
                'data': {},
                'last_activity': datetime.utcnow().isoformat()
            }
            provider_session.setdefault('data', {})
            provider_session['data']['booking_id'] = booking_id
            provider_session['data']['customer_number'] = user_number
            provider_session['data']['service_type'] = session['data']['service_type']
            provider_session['data']['issue'] = session['data'].get('issue', 'Not specified')
            provider_session['data']['booking_time'] = session['data']['booking_time']
            provider_session['state'] = ConversationState.BOOKING_PENDING_PROVIDER

            provider_session_to_save = provider_session.copy()
            if isinstance(provider_session_to_save.get('state'), ConversationState):
                provider_session_to_save['state'] = provider_session_to_save['state'].value
            await self.db.save_session(provider_number, provider_session_to_save)
            
            # Reset customer session back to service search after sending booking request
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
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
                f"Booking Confirmed!\n\n"
                f"Reference: {booking_id}\n\n"
                f"You've accepted this booking. Contact the customer to arrange details.",
                "provider_booking_accepted"
            )
            
            # Send confirmation to customer
            if customer_number:
                await self._log_and_send_response(
                    customer_number,
                    f"Booking Confirmed!\n\n"
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
                f"You've declined booking {booking_id}.\n\n"
                f"The customer will be notified and can book with another provider.",
                "provider_booking_declined"
            )
            
            # Notify customer
            if customer_number:
                await self._log_and_send_response(
                    customer_number,
                    f"Sorry, {provider_name} is unable to take this booking.\n\n"
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
                "Provider Registration\n\n"
                "Let's get you registered as a service provider.\n\n"
                "What's your full name?",
                "provider_registration_start"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_NAME
        
        elif state == ConversationState.PROVIDER_REGISTER_NAME:
            session['data']['name'] = message_text.title()
            await self._log_and_send_response(
                user_number,
                f"Great, {session['data']['name']}!\n\n"
                "What service do you provide? (e.g., plumber, electrician, carpenter, etc.)",
                "provider_registration_service_prompt"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_SERVICE
        
        elif state == ConversationState.PROVIDER_REGISTER_SERVICE:
            service_type = message_text.lower()
            session['data']['service_type'] = service_type
            
            await self._log_and_send_response(
                user_number,
                f"Perfect!\n\n"
                "What area or neighborhood do you serve?",
                "provider_registration_location_prompt"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_LOCATION
        
        elif state == ConversationState.PROVIDER_REGISTER_LOCATION:
            # Store location and ask for optional business name
            session['data']['location'] = message_text.title()
            await self._log_and_send_response(
                user_number,
                "Nice! If you have a business name, send it now.\n\nIf not, reply with 'skip'.",
                "provider_registration_business_prompt"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_BUSINESS
        
        elif state == ConversationState.PROVIDER_REGISTER_BUSINESS:
            text = message_text.strip()
            if text.lower() not in ['skip', 'none', '-'] and text:
                session['data']['business_name'] = text.title()
            else:
                session['data']['business_name'] = None
            await self._log_and_send_response(
                user_number,
                "Great! Finally, what contact details should customers use? (e.g., phone number or email).\n\nReply with 'skip' to use this WhatsApp number only.",
                "provider_registration_contact_prompt"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_CONTACT
        
        elif state == ConversationState.PROVIDER_REGISTER_CONTACT:
            text = message_text.strip()
            if text.lower() not in ['skip', 'none', '-'] and text:
                contact_value = text
            else:
                contact_value = user_number
            session['data']['contact'] = contact_value
            
            # Complete registration with all collected fields
            provider_data = {
                'whatsapp_number': user_number,
                'name': session['data']['name'],
                'service_type': session['data']['service_type'],
                'location': session['data']['location'],
                'business_name': session['data'].get('business_name'),
                'contact': session['data'].get('contact'),
                'status': 'pending',  # Requires approval
                'registered_at': datetime.utcnow().isoformat()
            }
            
            success = await self.db.create_provider(provider_data)
            
            if success:
                await self._log_and_send_response(
                    user_number,
                    f"Registration Submitted!\n\n"
                    f"Name: {session['data']['name']}\n"
                    f"Service: {session['data']['service_type']}\n"
                    f"Area: {session['data']['location']}\n"
                    f"Business: {session['data'].get('business_name') or 'N/A'}\n"
                    f"Contact: {session['data'].get('contact')}\n\n"
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
                    "Sorry, there was an issue with your registration. Please try again.",
                    "provider_registration_error"
                )
    
    async def handle_ai_response(self, user_number: str, message_text: str, user: Dict) -> None:
        """Delegate general queries to Claude via AWSLambdaService.

        All conversational logic and wording comes from the LLM. This method
        simply forwards the message (and basic user context) and returns the
        raw Claude response to the user.
        
        Also fetches conversation history so the LLM has full context and
        doesn't ask the same questions repeatedly.
        """
        # Fetch conversation history for this user (if available)
        conversation_history = []
        try:
            # Try to get recent messages from the database
            # This assumes db has a method to fetch conversation history
            if hasattr(self.db, 'get_conversation_history'):
                conversation_history = await self.db.get_conversation_history(user_number, limit=10)
        except Exception as e:
            logger.warning(f"Could not fetch conversation history for {user_number}: {e}")
        
        # Check if the lambda_service (could be GeminiService) accepts conversation_history
        # For now, pass it if the method signature supports it
        try:
            ai_response = await self.lambda_service.invoke_question_answerer(
                message_text,
                {
                    'name': user.get('name'),
                    'location': user.get('location'),
                    'booking_history': await self.db.get_user_bookings(user_number)
                },
                conversation_history=conversation_history
            )
        except TypeError:
            # Fallback for services that don't support conversation_history
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
            "Here's what I can help you with:\n\n"
            "- Find service providers (plumbers, electricians, carpenters, etc.)\n"
            "- Book appointments\n"
            "- Get booking reminders\n"
            "- Register as a service provider\n\n"
            "Just tell me what you need! For example:\n"
            "\"I need a plumber\"\n"
            "\"Book electrician for tomorrow\"\n"
            "\"Find a carpenter in Harare\"\n\n"
            "What can I help you with?"
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
        
        # Handle simple cases and common combinations
        if 'tomorrow' in message_lower and 'morning' in message_lower:
            return "Tomorrow morning"
        if 'tomorrow' in message_lower and 'afternoon' in message_lower:
            return "Tomorrow afternoon"
        if 'tomorrow' in message_lower and 'evening' in message_lower:
            return "Tomorrow evening"
        
        if 'today' in message_lower and 'morning' in message_lower:
            return "Today morning"
        if 'today' in message_lower and 'afternoon' in message_lower:
            return "Today afternoon"
        if 'today' in message_lower and 'evening' in message_lower:
            return "Today evening"
        
        if 'tomorrow' in message_lower:
            return "Tomorrow"
        if 'today' in message_lower:
            return "Today"
        if 'morning' in message_lower:
            return "Morning"
        if 'afternoon' in message_lower:
            return "Afternoon"
        if 'evening' in message_lower:
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
