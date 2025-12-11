from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dateutil.parser import parse as du_parse
from enum import Enum
import re
import logging
import json
from app.models.message import WhatsAppMessage
from app.utils.location_extractor import get_location_extractor
from config import settings

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
    CONFIRM_LOCATION = "confirm_location"  # Explicit yes/no confirmation after time
    BOOKING_USER_NAME = "booking_user_name"
    PROVIDER_SELECTION = "provider_selection"
    BOOKING_CONFIRM = "booking_confirm"  # Final confirmation before booking
    BOOKING_PENDING_PROVIDER = "booking_pending_provider"  # Waiting for provider response
    BOOKING_RESUME_DECISION = "booking_resume_decision"
    VIEW_BOOKINGS = "view_bookings"
    CANCEL_BOOKING_SELECT = "cancel_booking_select"
    CANCEL_BOOKING_CONFIRM = "cancel_booking_confirm"
    RESCHEDULE_BOOKING_SELECT = "reschedule_booking_select"
    RESCHEDULE_BOOKING_NEW_TIME = "reschedule_booking_new_time"
    RESCHEDULE_BOOKING_CONFIRM = "reschedule_booking_confirm"
    
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
    
    def _is_concise(self) -> bool:
        try:
            return bool(getattr(settings, 'USE_CONCISE_RESPONSES', False))
        except Exception:
            return False

    def _is_llm_controlled(self) -> bool:
        try:
            return bool(getattr(settings, 'LLM_CONTROLLED_CONVERSATION', False))
        except Exception:
            return False

    def _short(self, long_text: str, short_text: str) -> str:
        """Return short or long text based on concise mode. When LLM-controlled, always use long."""
        if self._is_llm_controlled():
            return long_text  # Always verbose for LLM mode
        return short_text if self._is_concise() else long_text

    def _build_friendly_provider_body(self, service_type: str, location: str, providers_count: int, session: Dict) -> str:
        if self._is_concise():
            return f"Found {providers_count} {service_type}s in {location}. Pick one:"
        data = (session or {}).get('data') or {}
        issue = (data.get('issue') or '').strip()
        if issue:
            issue_snippet = issue
            if len(issue_snippet) > 120:
                issue_snippet = issue_snippet[:117] + '...'
            prefix = f"Sorry you're going through this. For your issue — {issue_snippet} — I can connect you with Hustlr {service_type}s in {location}."
        else:
            prefix = f"Sorry you're going through this. I can connect you with Hustlr {service_type}s in {location}."
        return f"{prefix}\n\nFound {providers_count} provider(s). Please pick one:"

    def _friendly_footer(self) -> str:
        return "Tap or reply 1-3" if self._is_concise() else "Tap a provider or reply with the number — we will handle the rest"
    
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
                self._short(
                    "Welcome to Hustlr! I'll help you find local service providers.\n\n"
                    "To get started, send your name and area in one message.\n"
                    "Example: 'Vincent, Avondale'",
                    "Welcome to Hustlr! Send: 'Name, Area'"
                ),
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
                    self._short(
                        "Please send both your *name* and *area* in one message.\n"
                        "Example: 'Vincent, Avondale'",
                        "Please send: 'Name, Area'"
                    ),
                    "onboarding_retry"
                )
                return
            
            # Present privacy policy
            privacy_text = self._short(
                "Privacy Policy:\n\n"
                "- We store your name, location, and booking history\n"
                "- We share your info with service providers you choose\n"
                "- We never sell your data to third parties\n"
                "- You can request data deletion anytime\n\n"
                "Do you agree? (Yes/No)",
                "Privacy: we store name/location to help bookings. Agree? (Yes/No)"
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
                        self._short(
                            f"Great! You're all set, {session['data']['name']}!\n\n"
                            "You can now search for service providers and book appointments.\n\n"
                            "What service are you looking for?",
                            f"All set, {session['data']['name']}! What service do you need?"
                        ),
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
        if state == ConversationState.BOOKING_CONFIRM:
            await self.handle_booking_confirmation(user_number, message_text, session, user)
            return
        
        booking_progress_states = {
            ConversationState.BOOKING_SERVICE_DETAILS,
            ConversationState.BOOKING_LOCATION,
            ConversationState.CONFIRM_LOCATION,
            ConversationState.BOOKING_USER_NAME,
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

        text_cmd = message_text.strip().lower()
        text_cmd_compact = re.sub(r"\s+", " ", text_cmd)
        # View bookings intents (singular/plural, various verbs)
        if any(k in text_cmd for k in ["my bookings", "view bookings", "show bookings", "see bookings", "bookings"]):
            await self.show_user_bookings(user_number, session, user, mode="view")
            session['state'] = ConversationState.VIEW_BOOKINGS
            return
        # More flexible patterns like "see my booking" / "can i see my booking"
        if re.search(r"\b(see|view|show)\b.*\bbooking(s)?\b", text_cmd):
            await self.show_user_bookings(user_number, session, user, mode="view")
            session['state'] = ConversationState.VIEW_BOOKINGS
            return
        if "my booking" in text_cmd:
            await self.show_user_bookings(user_number, session, user, mode="view")
            session['state'] = ConversationState.VIEW_BOOKINGS
            return
        if any(k in text_cmd for k in ["cancel booking", "cancel my booking", "cancel a booking"]):
            await self.show_user_bookings(user_number, session, user, mode="cancel")
            session['state'] = ConversationState.CANCEL_BOOKING_SELECT
            return
        # Inline cancel with number: "cancel booking 2" (tolerate extra spaces)
        m_cancel_inline = re.search(r"\bcancel\s+booking\s+(\d+)\b", text_cmd_compact)
        if m_cancel_inline:
            await self.show_user_bookings(user_number, session, user, mode="cancel")
            session['state'] = ConversationState.CANCEL_BOOKING_SELECT
            await self.handle_cancel_booking_select(user_number, m_cancel_inline.group(1), session, user)
            return
        if any(k in text_cmd for k in ["reschedule", "reschedule booking", "postpone", "postpone booking", "move booking", "change time", "change booking time", "shift booking"]):
            await self.show_user_bookings(user_number, session, user, mode="reschedule")
            session['state'] = ConversationState.RESCHEDULE_BOOKING_SELECT
            return
        
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

        # Check for admin approval/denial commands
        approve_match = re.match(r'approve\s+(\+?[\d\s\-\(\)]+)', text_cmd)
        deny_match = re.match(r'deny\s+(\+?[\d\s\-\(\)]+)', text_cmd)
        
        if approve_match or deny_match:
            await self.handle_admin_approval(user_number, message_text, session)
            return

        # Reset conversation to fresh booking search
        if message_text in ['reset', 'restart', 'start over', 'new']:
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
            await self._log_and_send_response(
                user_number,
                self._short("Okay, let's start fresh. What service do you need?", "Starting new. What service?"),
                "session_reset"
            )
            return
        
        # Quick provider selection/booking from free-form text when a provider list is present
        try:
            if (session.get('data') or {}).get('providers') and state in {
                ConversationState.SERVICE_SEARCH,
                ConversationState.PROVIDER_SELECTION,
                ConversationState.BOOKING_TIME,
            }:
                handled = await self._maybe_quick_provider_choice(user_number, message_text, session, user)
                if handled:
                    return
        except Exception:
            pass

        # LLM-controlled mode: if enabled and we're not mid critical flow,
        # let the AI lead general chat/triage.
        if self._is_llm_controlled():
            # Anchor the conversation to the latest explicit user intent for service type
            try:
                latest_service = self.extract_service_type(message_text) or self.detect_problem_statement(message_text)
            except Exception:
                latest_service = None
            if latest_service:
                # If user mentions a different service, update session immediately and invalidate old picks
                prev_service = (session.get('data') or {}).get('service_type') if session.get('data') else None
                if latest_service != prev_service:
                    session.setdefault('data', {})
                    session['data']['service_type'] = latest_service
                    session['data'].pop('providers', None)
                    session['data'].pop('selected_provider', None)
            if state != ConversationState.BOOKING_PENDING_PROVIDER:
                await self.handle_ai_response(user_number, message_text, session, user)
                return

        # Try fast booking when the message already includes service + time
        if state == ConversationState.SERVICE_SEARCH:
            handled_fast = await self.try_fast_booking(user_number, message_text, session, user)
            if handled_fast:
                return
        
        # Handle booking flow states
        if state == ConversationState.SERVICE_SEARCH:
            await self.handle_service_search(user_number, message_text, session, user)
        elif state == ConversationState.VIEW_BOOKINGS:
            await self.handle_view_bookings_state(user_number, message_text, session, user)
        elif state == ConversationState.CANCEL_BOOKING_SELECT:
            await self.handle_cancel_booking_select(user_number, message_text, session, user)
        elif state == ConversationState.CANCEL_BOOKING_CONFIRM:
            await self.handle_cancel_booking_confirm(user_number, message_text, session, user)
        elif state == ConversationState.RESCHEDULE_BOOKING_SELECT:
            await self.handle_reschedule_booking_select(user_number, message_text, session, user)
        elif state == ConversationState.RESCHEDULE_BOOKING_NEW_TIME:
            await self.handle_reschedule_booking_new_time(user_number, message_text, session, user)
        elif state == ConversationState.RESCHEDULE_BOOKING_CONFIRM:
            await self.handle_reschedule_booking_confirm(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_SERVICE_DETAILS:
            await self.handle_booking_service_details(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_LOCATION:
            await self.handle_booking_location(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_TIME:
            await self.handle_booking_time(user_number, message_text, session, user)
        elif state == ConversationState.CONFIRM_LOCATION:
            await self.handle_confirm_location(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_USER_NAME:
            await self.handle_booking_user_name(user_number, message_text, session, user)
        elif state == ConversationState.PROVIDER_SELECTION:
            await self.handle_provider_selection(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_CONFIRM:
            await self.handle_booking_confirmation(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_PENDING_PROVIDER:
            await self.handle_provider_response(user_number, message_text, session, user)
        else:
            # Use AI only for general chat; if the message contains a service intent,
            # the AI handler will route it back into the structured booking flow.
            await self.handle_ai_response(user_number, message_text, session, user)
    
    def detect_problem_statement(self, message_text: str) -> Optional[str]:
        """
        Detect problem statements like 'I have a leaking pipe' and map to service type.
        
        Returns:
            Service type (e.g., 'plumber') or None if no problem detected.
        """
        # Heuristic combinations first (order matters)
        message_lower = message_text.lower()
        if (
            'blocked' in message_lower and any(w in message_lower for w in ['drain', 'sink', 'toilet'])
        ) or (
            'leak' in message_lower and any(w in message_lower for w in ['water', 'pipe', 'tap'])
        ):
            return 'plumber'
        if (
            ('light' in message_lower or 'lights' in message_lower)
            and any(w in message_lower for w in ['not', "won't", 'wont', 'refus', 'no'])
        ) or 'refusing to come on' in message_lower:
            return 'electrician'
        if 'clean' in message_lower:
            return 'cleaner'

        # Keyword lists fallback
        problem_keywords = {
            'plumber': [
                'leaking pipe', 'burst pipe', 'blocked drain', 'burst tap', 'leaking tap',
                'water leak', 'burst water', 'clogged drain', 'blocked toilet', 'leaking toilet',
                'plumbing issue', 'plumbing problem', 'water problem', 'drainage', 'sewage',
                'sink blocked', 'blocked sink', 'toilet blocked', 'no water',
            ],
            'doctor': [
                'heart problem', 'heart attack', 'chest pain', 'difficulty breathing', 'doctor', 'medical emergency',
                'fever', 'sick', 'ill', 'unwell', 'pain', 'hospital', 'clinic', 'ambulance', 'medical help',
                'diabetes', 'hypertension', 'high blood pressure', 'low blood pressure', 'stroke',
                'injury', 'bleeding', 'faint', 'collapse', 'unconscious', 'medical issue',
                'headache', 'head is aching', 'head aching', 'migraine',
            ],
            'electrician': [
                'electrical fault', 'power cut', 'no electricity', 'broken outlet', 'broken socket',
                'electrical problem', 'electrical issue', 'power issue', 'light not working',
                'switch not working', 'electrical wiring', 'electrical damage',
                'lights not turning on', 'no lights', 'lights out', 'lights refusing to come on', 'refusing to come on',
                'bulb not working', 'socket sparking', 'breaker tripping', 'power tripping',
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

        # Check each service type's keywords
        for service_type, keywords in problem_keywords.items():
            for keyword in keywords:
                if keyword in message_lower:
                    logger.info(f"Problem detected: '{keyword}' → service type: {service_type}")
                    return service_type

        return None
    
    async def try_fast_booking(self, user_number: str, message_text: str, session: Dict, user: Dict) -> bool:
        """Fast path: if service + time found, prefer provider selection first.
        Stores the parsed time, shows providers filtered by user's saved location, then proceeds.
        """
        service_type = self.extract_service_type(message_text)
        if not service_type:
            service_type = self.detect_problem_statement(message_text)
            if not service_type:
                return False

        if not self._message_contains_time_hint(message_text):
            return False

        user_location = user.get('location', '')
        if not user_location:
            await self._log_and_send_response(
                user_number,
                self._short(
                    "Please tell me your area or location (e.g., 'Harare', 'Borrowdale', or 'Bulawayo') so I can book the right provider.",
                    "Your area? (e.g., Harare)"
                ),
                "ask_location_for_fast_booking"
            )
            session['data']['service_type'] = service_type
            session['state'] = ConversationState.BOOKING_LOCATION
            return True

        # Capture issue from problem statement if applicable
        try:
            if 'issue' not in session.get('data', {}):
                inferred = self.detect_problem_statement(message_text)
                if inferred == service_type:
                    session.setdefault('data', {})
                    session['data']['issue'] = message_text
        except Exception:
            pass

        providers = await self.db.get_providers_by_service(service_type, user_location)
        if not providers:
            await self._log_and_send_response(
                user_number,
                self._short(
                    f"Sorry, no {service_type}s available in your area right now. Try a different service or area.",
                    f"Sorry, no {service_type}s in your area."
                ),
                "no_providers_found"
            )
            return True

        booking_time = self.parse_datetime(message_text)
        if not booking_time:
            await self._log_and_send_response(
                user_number,
                self._short("When do you want the service? (e.g., 'tomorrow at 10am', 'today 2pm')", "When? (e.g., tomorrow 10am)"),
                "ask_time_for_fast_booking"
            )
            session['data']['service_type'] = service_type
            session['data']['providers'] = providers
            session['state'] = ConversationState.BOOKING_TIME
            return True
        # Store parsed time and present provider choices (don't auto-pick)
        session['data']['service_type'] = service_type
        session['data']['booking_time'] = booking_time
        session['data']['location'] = user_location
        session['data']['providers'] = providers

        buttons = []
        for provider in providers[:3]:
            buttons.append({
                'id': f"provider_{provider['whatsapp_number']}",
                'title': f"{provider['name']}"
            })

        await self._log_and_send_interactive(
            user_number,
            f"Available {service_type}s in {user_location}",
            self._build_friendly_provider_body(service_type, user_location, len(providers), session),
            buttons,
            self._friendly_footer()
        )

        session['state'] = ConversationState.PROVIDER_SELECTION
        return True

    
    async def handle_service_search(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle service provider search"""
        # Extract service type from message
        service_type = self.extract_service_type(message_text)
        
        # If no explicit service type, try to detect problem statements
        if not service_type:
            service_type = self.detect_problem_statement(message_text)
        
        # If message looks like a problem statement for this service, capture it as issue
        if service_type:
            try:
                if 'issue' not in session.get('data', {}):
                    inferred = self.detect_problem_statement(message_text)
                    if inferred == service_type:
                        session.setdefault('data', {})
                        session['data']['issue'] = message_text
            except Exception:
                pass

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
                self._short(f"Sorry, no {service_type}s available right now.", f"Sorry, no {service_type}s right now."),
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
                        self._build_friendly_provider_body(service_type, normalized_location, len(providers), session),
                        buttons,
                        self._friendly_footer()
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
                self._short(
                    f"I didn't recognize '{message_text}'. Available areas are: {', '.join(available_locations[:5])}",
                    f"Didn't recognize '{message_text}'. Try: {', '.join(available_locations[:5])}"
                ),
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
            self._build_friendly_provider_body(service_type, normalized_location, len(providers), session),
            buttons,
            self._friendly_footer()
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
            self._short(
                f"Great! You've selected {selected_provider['name']}.\n\nWhen would you like the service? (e.g., 'tomorrow morning', 'Dec 15 at 2pm')",
                f"Great choice. When?"
            ),
            "provider_selected"
        )
    
    async def handle_booking_time(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle booking time and move to explicit location confirmation"""
        # Parse date/time (simplified - you'd want more robust parsing)
        booking_time = self.parse_datetime(message_text)
        
        if not booking_time:
            await self._log_and_send_response(
                user_number,
                self._short("I didn't catch that. Try 'tomorrow morning', 'Dec 15 at 2pm', or 'next Monday'.",
                            "Didn't get the time. Try 'tomorrow 10am' or 'Dec 15 14:00'."),
                "invalid_time_format"
            )
            return
        
        # Store booking time
        session['data']['booking_time'] = booking_time
        
        # Ask to confirm saved location if available; otherwise ask for location
        saved_location = (user or {}).get('location') or (session.get('data', {}).get('location') if session.get('data') else None)
        if saved_location:
            prompt = self._short(
                f"Should the provider come to your usual address at {saved_location}? Reply Yes or No.",
                f"Use saved location ({saved_location})? Yes/No"
            )
        else:
            prompt = self._short(
                "Where should the provider come? Please send your area (e.g., 'Harare', 'Borrowdale').",
                "Your area for this booking?"
            )
        await self._log_and_send_response(user_number, prompt, "confirm_location_prompt")
        session['state'] = ConversationState.CONFIRM_LOCATION

    async def handle_confirm_location(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Explicit Yes/No confirmation of location after time selection, with ability to change location."""
        text = (message_text or '').strip().lower()
        saved_location = (user or {}).get('location')
        final_location: Optional[str] = None

        yes_values = {'yes', 'y', 'yeah', 'yep', 'sure', 'ok', 'okay'}
        no_values = {'no', 'n', 'nope', 'nah'}

        if saved_location and text in yes_values:
            final_location = saved_location
        elif text in no_values:
            # Ask for a new location explicitly
            await self._log_and_send_response(
                user_number,
                self._short(
                    "No problem. Please send the area for this booking (e.g., 'Harare', 'Borrowdale').",
                    "Send area (e.g., Harare)"
                ),
                "ask_new_location"
            )
            return
        else:
            # Try to interpret input as a location value
            location_extractor = get_location_extractor()
            normalized_location = location_extractor.normalize_user_location(message_text)
            if normalized_location:
                final_location = normalized_location
                # Update user's saved location if it changed
                try:
                    if (user or {}).get('location') != final_location:
                        await self.db.update_user(user_number, {'location': final_location})
                except Exception:
                    pass
            else:
                await self._log_and_send_response(
                    user_number,
                    self._short(
                        f"I didn't recognize '{message_text}'. Please send your area (e.g., 'Harare', 'Borrowdale').",
                        "Area not recognized. Try 'Harare'"
                    ),
                    "location_not_recognized_after_time"
                )
                return

        # Persist final location and ask for user name next
        session.setdefault('data', {})
        session['data']['location'] = final_location
        await self._log_and_send_response(
            user_number,
            self._short("Great. What name should we put on the booking?", "Your name?"),
            "ask_user_name"
        )
        session['state'] = ConversationState.BOOKING_USER_NAME

    async def handle_booking_user_name(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Capture the user's name for this booking, update profile, and show final confirmation summary."""
        raw = (message_text or '').strip()
        if not raw:
            await self._log_and_send_response(
                user_number,
                self._short("What name should we put on the booking?", "Your name?"),
                "ask_user_name_retry"
            )
            return
        # Simple cleanup/title case
        name = raw.title()
        try:
            await self.db.update_user(user_number, {'name': name})
        except Exception:
            pass
        session.setdefault('data', {})
        session['data']['user_name'] = name

        # Build confirmation summary using latest fields
        service_type = session['data'].get('service_type', 'service').title()
        issue = session['data'].get('issue', 'Not specified')
        booking_time = session['data'].get('booking_time', 'Not specified')
        location_display = session['data'].get('location') or (user.get('location') if user else None) or 'your area'
        provider = (session['data'].get('selected_provider') or {})
        provider_name = provider.get('name')

        parts = []
        if provider_name:
            parts.append(f"Provider: {provider_name}")
        parts.append(f"Service: {service_type}")
        parts.append(f"Location: {location_display}")
        parts.append(f"Date & Time: {booking_time}")
        parts.append(f"Name: {name}")
        if issue and issue != 'Not specified':
            parts.insert(2, f"Issue: {issue}")

        summary_long = "Here's your booking:\n\n" + "\n".join(parts) + "\n\nReply \"Yes\" to confirm or \"No\" to edit."
        summary_short = "Confirm: " + " | ".join([p.split(": ",1)[1] if ": " in p else p for p in parts]) + ". Reply Yes/No."
        await self._log_and_send_response(user_number, self._short(summary_long, summary_short), "booking_confirmation_summary")
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
        
        # New flow: use pending booking summary if present, else fallback
        pending = (session.get('data') or {}).get('_pending_booking') or {}
        if pending:
            bt_text = pending.get('date_time') or ''
            try:
                bt_dt = self._canonicalize_booking_time(bt_text)
                bt_iso = bt_dt.strftime('%Y-%m-%d %H:%M') if bt_dt else bt_text
            except Exception:
                bt_iso = bt_text

            booking_id = f"booking_{datetime.utcnow().timestamp()}"
            booking_data = {
                'booking_id': booking_id,
                'user_whatsapp_number': user_number,
                'provider_whatsapp_number': pending.get('provider_number'),
                'service_type': pending.get('service_type') or (session.get('data', {}).get('service_type') or ''),
                'date_time': bt_iso,
                'issue': pending.get('issue') or '',
                'status': 'pending',
                'customer_number': user_number,
                'customer_name': user.get('name', 'Customer')
            }

            success = await self.db.create_booking(booking_data)
            if not success:
                await self._log_and_send_response(user_number, "Oops! Something went wrong. Please try again.", "booking_error")
                return

            provider_name = pending.get('provider_name') or 'Provider'
            provider_number = pending.get('provider_number')
            location_display = pending.get('location') or (session.get('data', {}).get('location') or user.get('location') or 'your area')
            customer_name = user.get('name', 'Customer')

            provider_message_final = (
                f"New Booking Request\n\n"
                f"Customer: {customer_name}\n"
                f"Service: {pending.get('service_type') or (session.get('data', {}).get('service_type') or '')}\n"
                f"Issue: {pending.get('issue') or 'Not specified'}\n"
                f"Location: {location_display}\n"
                f"Time: {bt_iso}\n"
                f"Reference: {booking_id}\n\n"
                f"Reply with 'accept' to confirm or 'deny' to decline"
            )

            # Notify customer and echo provider message
            await self._log_and_send_response(
                user_number,
                self._short(
                    f"Your booking was sent to {provider_name}!\n\nWe're waiting for their confirmation.\nReference: {booking_id}",
                    f"Sent to {provider_name}. Waiting. Ref: {booking_id}"
                ),
                "booking_sent_waiting"
            )
            await self._log_and_send_response(user_number, f"Sent to provider:\n\n{provider_message_final}", "booking_provider_message_copy")

            if provider_number:
                await self._log_and_send_response(provider_number, provider_message_final, "booking_request_to_provider")

                # Prepare provider session for response
                provider_session = await self.db.get_session(provider_number) or {
                    'state': ConversationState.BOOKING_PENDING_PROVIDER,
                    'data': {},
                    'last_activity': datetime.utcnow().isoformat()
                }
                provider_session.setdefault('data', {})
                provider_session['data']['booking_id'] = booking_id
                provider_session['data']['customer_number'] = user_number
                provider_session['data']['service_type'] = pending.get('service_type') or (session.get('data', {}).get('service_type') or '')
                provider_session['data']['issue'] = pending.get('issue') or 'Not specified'
                provider_session['data']['booking_time'] = bt_iso
                provider_session['state'] = ConversationState.BOOKING_PENDING_PROVIDER

                provider_session_to_save = provider_session.copy()
                if isinstance(provider_session_to_save.get('state'), ConversationState):
                    provider_session_to_save['state'] = provider_session_to_save['state'].value
                await self.db.save_session(provider_number, provider_session_to_save)

            # Reset session
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
            return

        # Fallback (legacy keys)
        # Create the booking (store canonical ISO date-time when possible)
        bt_text = session['data']['booking_time']
        try:
            bt_dt = self._canonicalize_booking_time(bt_text)
            bt_iso = bt_dt.strftime('%Y-%m-%d %H:%M') if bt_dt else bt_text
        except Exception:
            bt_iso = bt_text

        booking_data = {
            'booking_id': f"booking_{datetime.utcnow().timestamp()}",
            'user_whatsapp_number': user_number,
            'provider_whatsapp_number': session['data']['selected_provider']['whatsapp_number'],
            'service_type': session['data']['service_type'],
            'date_time': bt_iso,
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
            
            await self._log_and_send_response(
                user_number,
                self._short(
                    f"Your booking was sent to {provider_name}!\n\n"
                    f"We're waiting for their confirmation.\n"
                    f"Reference: {booking_id}\n\n"
                    f"You'll get a message once they respond.",
                    f"Sent to {provider_name}. Waiting for confirmation. Ref: {booking_id}"
                ),
                "booking_sent_waiting"
            )
            
            provider_message = (
                f"New Booking Request\n\n"
                f"Customer: {customer_name}\n"
                f"Service: {session['data']['service_type']}\n"
                f"Issue: {session['data'].get('issue', 'Not specified')}\n"
                f"Time: {session['data']['booking_time']}\n"
                f"Reference: {booking_id}\n\n"
                f"Reply with 'accept' to confirm or 'deny' to decline"
            )
            await self._log_and_send_response(provider_number, provider_message, "booking_request_to_provider")
            await self._log_and_send_response(user_number, f"Sent to provider:\n\n{provider_message}", "booking_provider_message_copy")
            
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
                self._short(
                    f"Booking Confirmed!\n\n"
                    f"Reference: {booking_id}\n\n"
                    f"You've accepted this booking. Contact the customer to arrange details.",
                    f"Confirmed. Ref: {booking_id}."
                ),
                "provider_booking_accepted"
            )
            
            # Send confirmation to customer
            if customer_number:
                await self._log_and_send_response(
                    customer_number,
                    self._short(
                        f"Booking Confirmed!\n\n"
                        f"{provider_name} has accepted your booking!\n"
                        f"Reference: {booking_id}\n\n"
                        f"They will contact you shortly to confirm details.",
                        f"Confirmed by {provider_name}. Ref: {booking_id}."
                    ),
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
                self._short(
                    f"You've declined booking {booking_id}.\n\n"
                    f"The customer will be notified and can book with another provider.",
                    f"Declined. Ref: {booking_id}."
                ),
                "provider_booking_declined"
            )
            
            # Notify customer
            if customer_number:
                await self._log_and_send_response(
                    customer_number,
                    self._short(
                        f"Sorry, {provider_name} is unable to take this booking.\n\n"
                        f"Reference: {booking_id}\n\n"
                        f"Would you like to try another provider?",
                        f"{provider_name} declined. Ref: {booking_id}. Try another provider?"
                    ),
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
    
    async def handle_admin_approval(self, user_number: str, message_text: str, session: Dict) -> None:
        """Handle admin approval/denial of provider registrations"""
        text_cmd = message_text.strip().lower()
        
        # Extract phone number and action
        approve_match = re.match(r'approve\s+(\+?[\d\s\-\(\)]+)', text_cmd)
        deny_match = re.match(r'deny\s+(\+?[\d\s\-\(\)]+)', text_cmd)
        
        if not (approve_match or deny_match):
            await self._log_and_send_response(
                user_number,
                "Please use format: 'approve +263777530322' or 'deny +263777530322'",
                "admin_invalid_format"
            )
            return
        
        # Extract and normalize phone number
        phone_raw = approve_match.group(1) if approve_match else deny_match.group(1)
        # Remove spaces, dashes, parentheses
        phone = re.sub(r'[\s\-\(\)]', '', phone_raw).strip()
        # Ensure it starts with +
        if not phone.startswith('+'):
            phone = '+' + phone
        
        action = 'approve' if approve_match else 'deny'
        
        # Get provider by phone
        provider = await self.db.get_provider_by_phone(phone)
        if not provider:
            await self._log_and_send_response(
                user_number,
                f"No pending provider found with phone {phone}",
                "admin_provider_not_found"
            )
            return
        
        # Update provider status
        provider_id = provider.get('_id')
        new_status = 'approved' if action == 'approve' else 'rejected'
        
        success = await self.db.update_provider_status(provider_id, new_status)
        if not success:
            await self._log_and_send_response(
                user_number,
                f"Failed to update provider status. Please try again.",
                "admin_update_error"
            )
            return
        
        # Notify admin
        admin_confirmation = (
            f"✅ Provider {action.upper()}ED\n\n"
            f"Name: {provider.get('name')}\n"
            f"Phone: {phone}\n"
            f"Service: {provider.get('service_type')}\n"
            f"Status: {new_status.upper()}\n\n"
            f"Timestamp: {datetime.utcnow().isoformat()}"
        )
        await self._log_and_send_response(user_number, admin_confirmation, f"admin_{action}_confirmation")
        
        # Notify provider
        provider_whatsapp = provider.get('whatsapp_number') or phone
        if action == 'approve':
            provider_message = (
                f"🎉 Great news!\n\n"
                f"Your Hustlr provider registration has been APPROVED!\n\n"
                f"You can now start receiving booking requests from customers.\n\n"
                f"Service: {provider.get('service_type')}\n"
                f"Location: {provider.get('location')}\n"
                f"Hours: {provider.get('availability_hours')}\n\n"
                f"Welcome to Hustlr! 🚀"
            )
        else:
            provider_message = (
                f"❌ Provider Registration Status\n\n"
                f"Your Hustlr provider registration has been REJECTED.\n\n"
                f"If you believe this is a mistake, please contact our support team.\n\n"
                f"Contact: support@hustlr.co.zw"
            )
        
        try:
            await self._log_and_send_response(provider_whatsapp, provider_message, f"provider_{action}_notification")
        except Exception as e:
            logger.error(f"Failed to notify provider {provider_whatsapp}: {e}")
        
        # Notify all admins of the decision
        admin_numbers = [
            '+263783961640',
            '+263775251636',
            '+263777530322',
            '+16509965727'
        ]
        
        decision_message = (
            f"📢 PROVIDER REGISTRATION DECISION\n\n"
            f"Status: {action.upper()}ED\n"
            f"Name: {provider.get('name')}\n"
            f"Phone: {phone}\n"
            f"Service: {provider.get('service_type')}\n"
            f"Decision by: {user_number}\n"
            f"Time: {datetime.utcnow().isoformat()}"
        )
        
        for admin_num in admin_numbers:
            if admin_num != user_number:  # Don't send to the admin who made the decision
                try:
                    await self._log_and_send_response(admin_num, decision_message, f"admin_{action}_notification")
                except Exception as e:
                    logger.error(f"Failed to notify admin {admin_num}: {e}")
    
    async def handle_ai_response(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Delegate conversation to Claude via AI service using JSON-only contract.

        Expected model outputs:
        - {"status":"IN_PROGRESS","next_question":"..."}
        - {"status":"COMPLETE","type":"booking"|"provider_registration","data":{...}}
        """
        # Provide recent history
        conversation_history = []
        try:
            if hasattr(self.db, 'get_conversation_history'):
                conversation_history = await self.db.get_conversation_history(user_number, limit=10)
        except Exception as e:
            logger.warning(f"Could not fetch conversation history for {user_number}: {e}")

        # Try to supply provider options when we can infer a service type
        provider_options = None
        maybe_service_ctx: Optional[str] = None
        try:
            maybe_service = (session.get('data', {}) or {}).get('service_type')
            if not maybe_service:
                maybe_service = self.extract_service_type(message_text) or self.detect_problem_statement(message_text)
            user_loc = (user or {}).get('location')
            normalized_location = None
            if user_loc:
                try:
                    location_extractor = get_location_extractor()
                    normalized_location = location_extractor.normalize_user_location(user_loc) or user_loc
                except Exception:
                    normalized_location = user_loc
            if maybe_service:
                # Fetch by location when known, otherwise fetch by service only
                provs = await self.db.get_providers_by_service(maybe_service, normalized_location or None)
                # If nothing found with location filter, fall back to no location filter
                if not provs and normalized_location:
                    try:
                        provs = await self.db.get_providers_by_service(maybe_service, None)
                    except Exception:
                        provs = []
                if provs:
                    provider_options = [
                        {"id": str(p.get('_id')), "name": p.get('name'), "service_type": p.get('service_type'), "location": p.get('location')}
                        for p in provs[:10]
                    ]
                # Remember the service_type & options for later
                session.setdefault('data', {})
                session['data']['service_type'] = maybe_service
                if provider_options:
                    session['data']['provider_options_cached'] = provider_options
                # Keep for known_fields below even if this try block fails later
                maybe_service_ctx = maybe_service
        except Exception as e:
            logger.warning(f"Could not build provider options: {e}")

        # Build user context for the model
        client_id = None
        try:
            if user and user.get('_id'):
                client_id = str(user['_id'])
        except Exception:
            client_id = None

        # Build known_fields to reduce repeated questions
        known_fields: Dict[str, Any] = {}
        if (session.get('data') or {}).get('service_type'):
            known_fields['service_type'] = session['data']['service_type']
        else:
            if maybe_service_ctx:
                known_fields['service_type'] = maybe_service_ctx
        # Try to parse a date/time from the current message
        try:
            dt_text = self.parse_datetime(message_text)
            if dt_text:
                # Split to date and time components
                parts = dt_text.split(" ", 1)
                if parts:
                    known_fields['date'] = parts[0]
                    if len(parts) > 1:
                        known_fields['time'] = parts[1]
        except Exception:
            pass

        user_context = {
            'name': user.get('name'),
            'location': user.get('location'),
            'client_id': client_id,
            'booking_history': await self.db.get_user_bookings(user_number),
            'provider_options': provider_options or (session.get('data', {}).get('provider_options_cached') if session.get('data') else None),
            'known_fields': known_fields or None,
        }

        # Call Claude
        try:
            ai_response = await self.lambda_service.invoke_question_answerer(
                message_text,
                user_context,
                conversation_history=conversation_history,
            )
        except Exception as e:
            logger.exception("AI invocation failed")
            await self._log_and_send_response(
                user_number,
                self._short("I'm having trouble connecting right now. Please try again in a moment.", "Temporary issue, try again."),
                "ai_invoke_error",
            )
            return

        # Parse JSON response strictly; if not JSON, DO NOT forward natural text (backend controls rendering)
        try:
            payload = json.loads((ai_response or '').strip())
        except Exception:
            # If the model returned non-JSON or nothing, fall back to backend-driven prompts/actions
            try:
                known_fields = user_context.get('known_fields') or {}
                service_type = (session.get('data') or {}).get('service_type') or known_fields.get('service_type')
            except Exception:
                service_type = None
            if service_type:
                try:
                    await self._ai_action_list_providers(user_number, {'service_type': service_type}, session, user)
                    return
                except Exception:
                    pass
            await self._log_and_send_response(user_number, self._short("What service do you need? (e.g., plumber, electrician)", "What service?"), "ai_parse_error")
            return

        # Final JSON at completion (Option C): booking_complete or provider_registration_complete
        try:
            if isinstance(payload, dict) and payload.get('booking_complete') is True:
                service = (payload.get('service') or '').strip().lower()
                issue = (payload.get('issue') or '').strip()
                time_text = (payload.get('time') or '').strip()
                loc_text = (payload.get('location') or '').strip()
                if service:
                    session.setdefault('data', {})
                    session['data']['service_type'] = service
                if issue:
                    session.setdefault('data', {})
                    session['data']['issue'] = issue
                if time_text:
                    session.setdefault('data', {})
                    session['data']['booking_time'] = time_text
                if loc_text:
                    session.setdefault('data', {})
                    session['data']['location'] = loc_text

                providers = (session.get('data') or {}).get('providers') or []
                sel_idx = (session.get('data') or {}).get('selected_provider_index')
                if providers and isinstance(sel_idx, int) and 1 <= sel_idx <= len(providers):
                    await self._ai_action_create_booking(
                        user_number,
                        {'action': 'create_booking', 'service_type': service, 'provider_index': sel_idx, 'time_text': time_text, 'issue': issue},
                        session,
                        user,
                    )
                    return
                # If no provider choice yet, list providers for the chosen service
                if service:
                    await self._ai_action_list_providers(user_number, {'service_type': service}, session, user)
                    return
                # If service missing, ask user to specify
                await self._log_and_send_response(user_number, "Which service do you need? (e.g., plumber, electrician)", "ai_need_service")
                return

            if isinstance(payload, dict) and payload.get('provider_registration_complete') is True:
                mapped = {
                    'action': 'register_provider',
                    'name': payload.get('name'),
                    'service_type': (payload.get('service') or ''),
                    'location': payload.get('location') or '',
                    'contact': payload.get('phone') or user_number,
                }
                await self._ai_action_register_provider(user_number, mapped, session)
                return
        except Exception:
            pass

        # Execute tool actions from the model, if any, to infer user intent (e.g., select provider, list providers)
        try:
            actions = None
            if isinstance(payload, dict):
                if isinstance(payload.get('actions'), list):
                    actions = payload.get('actions')
                elif isinstance(payload.get('actions'), dict):
                    actions = [payload.get('actions')]
                elif payload.get('action'):
                    actions = [payload]
            if actions:
                for action in actions:
                    await self._perform_ai_action(user_number, action, session, user)
                return
        except Exception:
            pass

        status = (payload or {}).get('status')
        # Strict ASK schema: backend renders messages and controls state
        if status == 'ASK':
            field = (payload or {}).get('field') or ''
            question = (payload or {}).get('question') or ''
            field = str(field).strip().lower()
            qtext = str(question).strip() or ""

            # Map field to our internal state to keep flow ordered
            field_state_map = {
                'service_type': ConversationState.SERVICE_SEARCH,
                'location': ConversationState.BOOKING_LOCATION,
                'date': ConversationState.BOOKING_TIME,
                'time': ConversationState.BOOKING_TIME,
                'selected_provider': ConversationState.PROVIDER_SELECTION,
                'user_name': ConversationState.BOOKING_USER_NAME,
            }

            # Special handling for provider selection: render backend provider list
            if field == 'selected_provider':
                stype = (session.get('data') or {}).get('service_type') or (user_context.get('known_fields') or {}).get('service_type')
                if stype:
                    try:
                        await self._ai_action_list_providers(user_number, {'service_type': stype}, session, user)
                        session['state'] = ConversationState.PROVIDER_SELECTION
                        return
                    except Exception:
                        pass
                # If service unknown, ask for it first
                await self._log_and_send_response(user_number, self._short("Which service do you need? (e.g., plumber, electrician)", "What service?"), "ask_service_type")
                session['state'] = ConversationState.SERVICE_SEARCH
                return

            # Use model's question text when provided; otherwise fallback concise prompts
            if not qtext:
                fallback_q = {
                    'service_type': self._short("Which service do you need? (e.g., plumber, electrician)", "What service?"),
                    'location': self._short("Where should the provider come? Please send your area (e.g., 'Harare').", "Your area?"),
                    'date': self._short("What date works for you? (e.g., 'tomorrow', 'Dec 15')", "Which date?"),
                    'time': self._short("What time works for you? (e.g., '10am', '2:30pm')", "What time?"),
                    'user_name': self._short("What name should we put on the booking?", "Your name?"),
                }
                qtext = fallback_q.get(field, self._short("What service do you need?", "What service?"))

            # Render the question and set state if we have a mapping
            await self._log_and_send_response(user_number, qtext, f"ask_{field or 'unknown'}")
            if field in field_state_map:
                session['state'] = field_state_map[field]
            return
        if status == 'IN_PROGRESS':
            question = (payload or {}).get('next_question') or ""
            # Guard: avoid re-asking service if it's already known
            ql = question.lower()
            um = (message_text or '').lower()
            # Persist provider selection index if indicated by model's question (e.g., first/second/third or a number)
            try:
                ord_map = {'first': 1, '1st': 1, 'second': 2, '2nd': 2, 'third': 3, '3rd': 3}
                sel_idx = None
                for k, v in ord_map.items():
                    if k in ql:
                        sel_idx = v
                        break
                if sel_idx is None:
                    mnum = re.search(r"\b(\d+)\b", ql)
                    if mnum:
                        sel = int(mnum.group(1))
                        if sel > 0:
                            sel_idx = sel
                if sel_idx:
                    session.setdefault('data', {})
                    session['data']['selected_provider_index'] = sel_idx
            except Exception:
                pass

            service_known = bool((session.get('data') or {}).get('service_type')) or bool('service_type' in (user_context.get('known_fields') or {}))
            wants_list = any(k in ql for k in ['available', 'options', 'list', 'providers']) or any(k in um for k in ['list', 'show options', 'show list', 'providers'])
            if service_known and wants_list:
                # List providers only when explicitly asked
                service_type = (session.get('data') or {}).get('service_type') or (user_context.get('known_fields') or {}).get('service_type') or ''
                if service_type:
                    try:
                        await self._ai_action_list_providers(user_number, {'service_type': service_type}, session, user)
                        return
                    except Exception:
                        pass
            if not question:
                question = self._short("What service do you need?", "What service?")
            await self._log_and_send_response(user_number, question, "ai_next_question")
            return

        if status == 'COMPLETE':
            ptype = (payload or {}).get('type')
            data = (payload or {}).get('data') or {}
            if ptype == 'booking':
                # Validate fields
                service_type = (data.get('service_type') or '').strip().lower()
                provider_id = (data.get('service_provider_id') or '').strip()
                date_str = (data.get('date') or '').strip()
                time_str = (data.get('time') or '').strip()
                notes = (data.get('additional_notes') or '').strip()

                if not (service_type and provider_id and date_str and time_str):
                    await self._log_and_send_response(user_number, "Missing booking fields. Please provide required details.", "booking_missing_fields")
                    return

                # Fetch provider
                provider = await self.db.get_provider_by_id(provider_id)
                if not provider:
                    await self._log_and_send_response(user_number, "Selected provider not found. Please pick again.", "provider_not_found")
                    return

                # Combine date and time
                try:
                    base_dt = du_parse(date_str)
                    tm = du_parse(time_str, fuzzy=True, default=base_dt)
                    bt_iso = tm.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    bt_iso = f"{date_str} {time_str}".strip()

                booking_id = f"booking_{datetime.utcnow().timestamp()}"
                booking_data = {
                    'booking_id': booking_id,
                    'user_whatsapp_number': user_number,
                    'user_id': client_id,
                    'provider_id': provider_id,
                    'provider_whatsapp_number': provider.get('whatsapp_number') or provider.get('contact'),
                    'service_type': service_type,
                    'date': date_str,
                    'time': time_str,
                    'date_time': bt_iso,
                    'additional_notes': notes,
                    'status': 'pending',
                    'customer_number': user_number,
                    'customer_name': user.get('name', 'Customer'),
                }

                success = await self.db.create_booking(booking_data)
                if not success:
                    await self._log_and_send_response(user_number, "Oops! Something went wrong. Please try again.", "booking_error")
                    return

                provider_name = provider.get('name') or 'Provider'
                provider_number = provider.get('whatsapp_number') or provider.get('contact')

                await self._log_and_send_response(
                    user_number,
                    self._short(
                        f"Your booking was sent to {provider_name}!\n\nWe're waiting for their confirmation.\nReference: {booking_id}",
                        f"Sent to {provider_name}. Waiting. Ref: {booking_id}"
                    ),
                    "booking_sent_waiting"
                )

                if provider_number:
                    provider_message = (
                        f"New Booking Request\n\n"
                        f"Customer: {user.get('name', 'Customer')}\n"
                        f"Service: {service_type}\n"
                        f"Notes: {notes or 'N/A'}\n"
                        f"Time: {bt_iso}\n"
                        f"Reference: {booking_id}\n\n"
                        f"Reply with 'accept' to confirm or 'deny' to decline"
                    )
                    await self._log_and_send_response(provider_number, provider_message, "booking_request_to_provider")

                    # Prepare provider session for response
                    provider_session = await self.db.get_session(provider_number) or {
                        'state': ConversationState.BOOKING_PENDING_PROVIDER,
                        'data': {},
                        'last_activity': datetime.utcnow().isoformat(),
                    }
                    provider_session.setdefault('data', {})
                    provider_session['data']['booking_id'] = booking_id
                    provider_session['data']['customer_number'] = user_number
                    provider_session['data']['service_type'] = service_type
                    provider_session['data']['issue'] = notes or 'Not specified'
                    provider_session['data']['booking_time'] = bt_iso
                    provider_session['state'] = ConversationState.BOOKING_PENDING_PROVIDER
                    provider_session_to_save = provider_session.copy()
                    if isinstance(provider_session_to_save.get('state'), ConversationState):
                        provider_session_to_save['state'] = provider_session_to_save['state'].value
                    await self.db.save_session(provider_number, provider_session_to_save)

                # Reset customer session
                session['state'] = ConversationState.SERVICE_SEARCH
                session['data'] = {}
                return

            if ptype == 'provider_registration':
                full_name = (data.get('full_name') or '').strip()
                phone = (data.get('phone') or '').strip()
                service_category = (data.get('service_category') or '').strip().lower()
                years_experience = data.get('years_experience')
                national_id = (data.get('national_id') or '').strip()
                location = (data.get('location') or '').strip()
                availability_days = data.get('availability_days') or []
                availability_hours = (data.get('availability_hours') or '').strip()

                if not (full_name and phone and service_category and national_id and location and availability_hours):
                    await self._log_and_send_response(user_number, "Missing registration fields. Please provide the required info.", "provider_registration_missing")
                    return

                # Check if phone number is already registered
                existing_provider = await self.db.get_provider_by_phone(phone)
                if existing_provider:
                    await self._log_and_send_response(
                        user_number,
                        self._short(
                            f"This phone number ({phone}) is already registered. If you need to update your information, please contact our support team.",
                            f"Phone number already registered."
                        ),
                        "provider_already_registered"
                    )
                    return

                provider_data = {
                    'whatsapp_number': phone,
                    'name': full_name,
                    'service_type': service_category,
                    'location': location,
                    'business_name': None,
                    'contact': phone,
                    'status': 'pending',
                    'years_experience': years_experience,
                    'national_id': national_id,
                    'availability_days': availability_days,
                    'availability_hours': availability_hours,
                    'registered_by_number': user_number,
                    'registered_at': datetime.utcnow().isoformat(),
                }
                success = await self.db.create_provider(provider_data)
                if success:
                    await self._log_and_send_response(
                        user_number,
                        self._short(
                            "Registration received. We'll review your information and notify you soon.",
                            "Registration submitted."
                        ),
                        "provider_registration_complete"
                    )
                    
                    # Send registration details to admin numbers
                    admin_numbers = [
                        '+263783961640',
                        '+263775251636',
                        '+263777530322',
                        '+16509965727'
                    ]
                    
                    admin_message = (
                        f"📋 NEW PROVIDER REGISTRATION REQUEST\n\n"
                        f"Name: {full_name}\n"
                        f"Phone: {phone}\n"
                        f"Service: {service_category}\n"
                        f"Experience: {years_experience} years\n"
                        f"National ID: {national_id}\n"
                        f"Location: {location}\n"
                        f"Available: {', '.join(availability_days) if availability_days else 'Not specified'}\n"
                        f"Hours: {availability_hours}\n"
                        f"Registered by: {user_number}\n\n"
                        f"Reply with:\n"
                        f"'approve {phone}' to approve\n"
                        f"'deny {phone}' to deny"
                    )
                    
                    for admin_num in admin_numbers:
                        try:
                            await self._log_and_send_response(admin_num, admin_message, "provider_registration_admin_notification")
                        except Exception as e:
                            logger.error(f"Failed to send admin notification to {admin_num}: {e}")
                    
                    session['state'] = ConversationState.SERVICE_SEARCH
                    session['data'] = {}
                else:
                    await self._log_and_send_response(user_number, "Sorry, there was an issue with your registration. Please try again.", "provider_registration_error")
                return

            await self._log_and_send_response(user_number, "Unsupported operation.", "ai_unsupported")
            return

        # Fallback
        await self._log_and_send_response(user_number, self._short("Sorry, I couldn't process that.", "Sorry."), "ai_unexpected_payload")

    async def _perform_ai_action(self, user_number: str, action: Dict[str, Any], session: Dict, user: Dict) -> None:
        act = (action.get('action') or '').lower()
        if act == 'list_providers':
            await self._ai_action_list_providers(user_number, action, session, user)
        elif act == 'create_booking':
            await self._ai_action_create_booking(user_number, action, session, user)
        elif act == 'register_provider':
            await self._ai_action_register_provider(user_number, action, session)
        else:
            # Unknown action; ignore silently
            return

    async def _ai_action_list_providers(self, user_number: str, payload: Dict[str, Any], session: Dict, user: Dict) -> None:
        # Always prefer the current conversation's service and location from session
        service_type = (session.get('data', {}).get('service_type') or payload.get('service_type') or '').strip().lower()
        if not service_type:
            await self._log_and_send_response(user_number, "Which service do you need? (e.g., plumber, electrician)", "ai_need_service")
            return
        # Prefer session location for this booking, then user profile
        sess_loc = (session.get('data', {}) or {}).get('location') or ''
        user_loc = sess_loc or (user.get('location') or '')
        location_extractor = get_location_extractor()
        normalized_location = location_extractor.normalize_user_location(user_loc) if user_loc else ''
        providers = await self.db.get_providers_by_service(service_type, normalized_location or None)
        if not providers:
            await self._log_and_send_response(user_number, self._short(f"Sorry, no {service_type}s available in your area right now.", f"Sorry, no {service_type}s in your area."), "no_providers_found")
            return

        # Build buttons from top 3 providers
        buttons = []
        for provider in providers[:3]:
            buttons.append({
                'id': f"provider_{provider['whatsapp_number']}",
                'title': f"{provider['name']}"
            })
        header = f"Available {service_type}s in {normalized_location or user_loc or 'your area'}"
        await self._log_and_send_interactive(
            user_number,
            header,
            self._build_friendly_provider_body(service_type, normalized_location or user_loc or 'your area', len(providers), session),
            buttons,
            self._friendly_footer()
        )
        # Persist context for the LLM and for backend mapping
        session.setdefault('data', {})
        session['data']['service_type'] = service_type
        session['data']['providers'] = providers
        session['data']['location'] = normalized_location or user_loc
        # Summarize tool result for LLM context
        try:
            summary = {
                'providers': [
                    {'index': i + 1, 'name': p.get('name'), 'whatsapp_number': p.get('whatsapp_number')}
                    for i, p in enumerate(providers[:5])
                ],
                'location': session['data']['location'],
                'service_type': service_type,
            }
            session['data']['last_tool_result'] = json.dumps(summary)
        except Exception:
            session['data']['last_tool_result'] = None

    async def _ai_action_create_booking(self, user_number: str, payload: Dict[str, Any], session: Dict, user: Dict) -> None:
        service_type = (payload.get('service_type') or session.get('data', {}).get('service_type') or '').strip().lower()
        issue = (payload.get('issue') or '').strip()
        time_text = (payload.get('time_text') or '').strip()
        provider_index = payload.get('provider_index')

        providers = (session.get('data', {}) or {}).get('providers') or []
        if not providers or not isinstance(provider_index, int) or provider_index < 1 or provider_index > len(providers):
            await self._log_and_send_response(user_number, "Please pick a provider from the list (reply with 1, 2, or 3).", "provider_pick_missing")
            return
        selected_provider = providers[provider_index - 1]

        if not time_text:
            await self._log_and_send_response(user_number, self._short("When do you want the service? (e.g., 'tomorrow at 10am')", "When? (e.g., tomorrow 10am)"), "ask_time_for_booking")
            return
        try:
            bt_dt = self._canonicalize_booking_time(time_text)
            bt_iso = bt_dt.strftime('%Y-%m-%d %H:%M') if bt_dt else time_text
        except Exception:
            bt_iso = time_text

        # Build provider message preview
        provider_name = selected_provider.get('name') or 'Provider'
        provider_number = selected_provider.get('whatsapp_number')
        display_time = self._format_booking_time_for_display(bt_iso)
        location_display = (session.get('data', {}).get('location') or user.get('location') or 'your area')
        preview_provider_message = (
            f"New Booking Request\n\n"
            f"Customer: {user.get('name', 'Customer')}\n"
            f"Service: {service_type or session.get('data', {}).get('service_type') or ''}\n"
            f"Issue: {issue or 'Not specified'}\n"
            f"Location: {location_display}\n"
            f"Time: {bt_iso}\n"
            f"Reference: (will be generated)\n\n"
            f"Reply with 'accept' to confirm or 'deny' to decline"
        )

        # Persist pending booking for confirmation
        session.setdefault('data', {})
        session['data']['_pending_booking'] = {
            'service_type': service_type or (session.get('data', {}).get('service_type') or ''),
            'provider_index': provider_index,
            'provider_name': provider_name,
            'provider_number': provider_number,
            'date_time': bt_iso,
            'issue': issue or '',
            'location': location_display,
            'provider_message': preview_provider_message,
        }

        # Ask user to confirm, and show exactly what will be sent to the provider
        summary = (
            f"Please confirm your booking:\n\n"
            f"Provider: {provider_name}\n"
            f"Service: {service_type or (session.get('data', {}).get('service_type') or '')}\n"
            f"Location: {location_display}\n"
            f"Time: {display_time}\n"
            f"Issue: {issue or 'Not specified'}\n\n"
            f"This is what will be sent to the provider:\n\n{preview_provider_message}\n\n"
            f"Reply 'yes' to confirm or 'no' to change."
        )
        await self._log_and_send_response(user_number, summary, "booking_confirm_summary")
        session['state'] = ConversationState.BOOKING_CONFIRM

    async def _ai_action_register_provider(self, user_number: str, payload: Dict[str, Any], session: Dict) -> None:
        name = (payload.get('name') or '').strip()
        service_type = (payload.get('service_type') or '').strip().lower()
        location = (payload.get('location') or '').strip()
        business_name = (payload.get('business_name') or '').strip() or None
        contact = (payload.get('contact') or '').strip()

        missing = [k for k, v in [('name', name), ('service_type', service_type), ('location', location), ('contact', contact)] if not v]
        if missing:
            await self._log_and_send_response(user_number, f"Missing: {', '.join(missing)}. Please provide these to complete registration.", "provider_registration_missing")
            return

        provider_data = {
            'whatsapp_number': contact,
            'name': name,
            'service_type': service_type,
            'location': location,
            'business_name': business_name,
            'contact': contact,
            'status': 'pending',
        }
        success = await self.db.create_provider(provider_data)
        if success:
            await self._log_and_send_response(user_number, self._short("Registration received. We'll review and notify you soon.", "Registration submitted. We'll notify you."), "provider_registration_complete")
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
        else:
            await self._log_and_send_response(user_number, "Sorry, there was an issue with your registration. Please try again.", "provider_registration_error")
    
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
        
        await self._log_and_send_response(
            user_number,
            self._short(
                help_text,
                "Options: find providers, book, reminders, register provider. What do you need?"
            ),
            "help_menu"
        )
    
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
        dt = self._parse_natural_datetime(message_text)
        if dt:
            return dt.strftime('%Y-%m-%d %H:%M')
        return None

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

    def _canonicalize_booking_time(self, text: str) -> Optional[datetime]:
        dt = self._parse_natural_datetime(text)
        if dt:
            return dt
        return None

    def _parse_natural_datetime(self, text: str) -> Optional[datetime]:
        t_raw = (text or '').strip()
        if not t_raw:
            return None
        t = t_raw.lower()
        now = datetime.utcnow()

        # Relative offsets: "in 2 hours", "in 30 minutes", "in 3 days", "in 1 week"
        m = re.match(r"^\s*in\s+(\d+)\s*(minute|minutes|min|hour|hours|hr|hrs|day|days|week|weeks)\s*$", t, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            if unit.startswith('min'):
                return now + timedelta(minutes=n)
            if unit in ('hour', 'hours', 'hr', 'hrs'):
                return now + timedelta(hours=n)
            if unit.startswith('day'):
                return now + timedelta(days=n)
            if unit.startswith('week'):
                return now + timedelta(weeks=n)

        # Keywords today/tomorrow/tonight with optional time after
        def parse_with_base(remove_word: str, base: datetime, default_hour: int = 9) -> Optional[datetime]:
            remainder = re.sub(fr"(?i)\b{remove_word}\b", '', t_raw).strip()
            if remainder:
                try:
                    return du_parse(remainder, fuzzy=True, default=base.replace(hour=default_hour, minute=0, second=0, microsecond=0))
                except Exception:
                    return base.replace(hour=default_hour, minute=0, second=0, microsecond=0)
            return base.replace(hour=default_hour, minute=0, second=0, microsecond=0)

        if 'tomorrow' in t:
            return parse_with_base('tomorrow', now + timedelta(days=1), 9)
        if 'today' in t:
            return parse_with_base('today', now, 9)
        if 'tonight' in t:
            return parse_with_base('tonight', now, 18)
        if 'now' in t:
            return now

        # "next monday 3pm"
        weekdays = ['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
        for idx, name in enumerate(weekdays):
            if f'next {name}' in t:
                days_ahead = (idx - now.weekday() + 7) % 7
                days_ahead = days_ahead if days_ahead != 0 else 7
                base = now + timedelta(days=days_ahead)
                return parse_with_base(f'next {name}', base, 9)
            if f'this {name}' in t:
                days_ahead = (idx - now.weekday() + 7) % 7
                base = now + timedelta(days=days_ahead)
                return parse_with_base(f'this {name}', base, 9)

        # Generic parse attempts (covers: "Dec 31 15:00", "2025-12-31 15:00", "31/12/2025 15:00", "3pm")
        def try_du(s: str, **kwargs) -> Optional[datetime]:
            try:
                return du_parse(s, fuzzy=True, default=now, **kwargs)
            except Exception:
                return None

        dt = try_du(t_raw)
        if not dt:
            dt = try_du(t_raw, dayfirst=True)
        if not dt:
            return None

        # If only time was provided and it's already passed today, roll to next day
        has_date_hint = bool(re.search(r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b", t)) or bool(re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b", t, re.I)) or bool(re.search(r"\b\d{1,2}/\d{1,2}\b", t))
        if not has_date_hint and dt <= now:
            dt = dt + timedelta(days=1)

        return dt

    def _format_booking_time_for_display(self, dt_text: str) -> str:
        s = (dt_text or '').strip()
        if not s:
            return 'Time not set'
        # Try parse ISO
        try:
            dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            pass
        # Try canonicalize from natural text
        try:
            dt2 = self._canonicalize_booking_time(s)
            if dt2:
                return dt2.strftime('%Y-%m-%d %H:%M')
        except Exception:
            pass
        return s

    async def _maybe_quick_provider_choice(self, user_number: str, message_text: str, session: Dict, user: Dict) -> bool:
        """Infer provider selection and/or time from free-form user text and create a booking.

        Works when a providers list already exists in session['data']['providers'].
        Accepts inputs like: 'book the first option', 'I want the second plumber',
        'book Jayhind tomorrow 10am', or just a time after a prior selection.
        """
        try:
            providers = (session.get('data') or {}).get('providers') or []
            if not providers:
                return False
            text = (message_text or '').strip().lower()
            if not text:
                return False

            # Determine provider index from text or previous selection
            idx: Optional[int] = None
            ord_map = {
                'first': 1, '1st': 1,
                'second': 2, '2nd': 2,
                'third': 3, '3rd': 3,
            }
            for k, v in ord_map.items():
                if k in text:
                    idx = v
                    break
            if idx is None:
                # Any number in text
                m = re.search(r"\b(\d+)\b", text)
                if m:
                    try:
                        n = int(m.group(1))
                        if 1 <= n <= len(providers):
                            idx = n
                    except Exception:
                        idx = None
            if idx is None:
                # Match by provider name
                for i, p in enumerate(providers, start=1):
                    name = (p.get('name') or '').lower()
                    if name and name in text:
                        idx = i
                        break

            # Fallback to previously selected index if present
            if idx is None:
                prev_idx = (session.get('data') or {}).get('selected_provider_index')
                if isinstance(prev_idx, int) and 1 <= prev_idx <= len(providers):
                    idx = prev_idx

            # Try extract a time from the message
            time_dt = None
            try:
                time_dt = self._canonicalize_booking_time(message_text)
            except Exception:
                time_dt = None

            if idx is None and not time_dt:
                return False

            # If we have a provider index but no time yet, remember selection and ask for time
            if idx is not None and not time_dt:
                session.setdefault('data', {})
                session['data']['selected_provider_index'] = idx
                await self._log_and_send_response(
                    user_number,
                    self._short("When would you like the service? (e.g., 'tomorrow 10am')", "When? (e.g., tomorrow 10am)"),
                    "ask_time_for_booking_quick"
                )
                session['state'] = ConversationState.BOOKING_TIME
                return True

            # If we only have a time, use the previously selected provider index
            if time_dt and idx is None:
                prev_idx = (session.get('data') or {}).get('selected_provider_index')
                if isinstance(prev_idx, int) and 1 <= prev_idx <= len(providers):
                    idx = prev_idx
                else:
                    return False

            # Create booking via existing helper using provider index and time text
            if idx is not None:
                payload = {
                    'action': 'create_booking',
                    'service_type': (session.get('data', {}) or {}).get('service_type') or '',
                    'provider_index': idx,
                    'time_text': message_text,
                    'issue': (session.get('data', {}) or {}).get('issue') or ''
                }
                await self._ai_action_create_booking(user_number, payload, session, user)
                return True

            return False
        except Exception:
            return False

    async def show_user_bookings(self, user_number: str, session: Dict, user: Dict, mode: str = "view") -> None:
        bookings = []
        try:
            bookings = await self.db.get_user_bookings(user_number)
        except Exception:
            bookings = []
        if not bookings:
            await self._log_and_send_response(user_number, "You have no bookings yet.", "no_bookings")
            return
        try:
            bookings.sort(key=lambda b: b.get('created_at') or b.get('date_time') or '', reverse=True)
        except Exception:
            pass
        enriched = []
        for b in bookings:
            pnum = b.get('provider_whatsapp_number')
            pname = None
            if pnum and hasattr(self.db, 'get_provider_by_whatsapp'):
                try:
                    pdoc = await self.db.get_provider_by_whatsapp(pnum)
                    if pdoc:
                        pname = pdoc.get('name')
                except Exception:
                    pass
            enriched.append({
                'id': b.get('booking_id') or '',
                'provider': pname or pnum or 'Provider',
                'time': self._format_booking_time_for_display(b.get('date_time') or ''),
                'status': b.get('status') or 'pending',
            })
        lines = []
        for idx, e in enumerate(enriched[:10], start=1):
            lines.append(f"{idx}) {e['provider']} — {e['time']} [{e['status']}]\nRef: {e['id']}")
        header = "Your bookings"
        if mode == "cancel":
            body = "Select a booking to cancel:\n\n" + "\n".join(lines)
            footer = "Reply with the number to cancel"
        elif mode == "reschedule":
            body = "Select a booking to reschedule:\n\n" + "\n".join(lines)
            footer = "Reply with the number to reschedule"
        else:
            body = "Here are your recent bookings:\n\n" + "\n".join(lines)
            footer = None
        buttons = []
        for idx, e in enumerate(enriched[:3], start=1):
            title = f"{e['provider']}"
            buttons.append({'id': f"b_{e['id']}", 'title': title})
        await self._log_and_send_interactive(user_number, header, body, buttons, footer)
        session.setdefault('data', {})
        session['data']['_bookings_list'] = enriched

    async def handle_view_bookings_state(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        text = message_text.strip().lower()
        if any(k in text for k in ["cancel", "cancel booking"]):
            await self.show_user_bookings(user_number, session, user, mode="cancel")
            session['state'] = ConversationState.CANCEL_BOOKING_SELECT
            return
        # Inline: "cancel booking 2" while viewing list
        m_cancel_inline = re.search(r"\bcancel\s+booking\s+(\d+)\b", re.sub(r"\s+", " ", text))
        if m_cancel_inline:
            await self.show_user_bookings(user_number, session, user, mode="cancel")
            session['state'] = ConversationState.CANCEL_BOOKING_SELECT
            await self.handle_cancel_booking_select(user_number, m_cancel_inline.group(1), session, user)
            return
        if any(k in text for k in ["reschedule", "postpone", "change time", "move booking", "reschedule booking"]):
            await self.show_user_bookings(user_number, session, user, mode="reschedule")
            session['state'] = ConversationState.RESCHEDULE_BOOKING_SELECT
            return
        await self._log_and_send_response(user_number, "Say 'cancel booking' to cancel one, or tell me what service you need.", "view_bookings_hint")
        session['state'] = ConversationState.SERVICE_SEARCH

    async def handle_cancel_booking_select(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        items = session.get('data', {}).get('_bookings_list') or []
        selected = None
        # Accept number anywhere in text
        num_match = re.search(r"\b(\d+)\b", str(message_text))
        if num_match:
            i = int(num_match.group(1))
            if 1 <= i <= len(items):
                selected = items[i-1]
        if not selected:
            await self._log_and_send_response(user_number, "Please reply with the number of the booking to cancel.", "cancel_booking_select_invalid")
            return
        session['data']['_cancel_booking_id'] = selected['id']
        await self._log_and_send_response(user_number, f"Cancel booking {selected['id']} with {selected['provider']} at {selected['time']}? Reply 'yes' to confirm or 'no' to keep it.", "cancel_booking_confirm")
        session['state'] = ConversationState.CANCEL_BOOKING_CONFIRM

    async def handle_cancel_booking_confirm(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        text = message_text.strip().lower()
        if text in ['yes', 'y', 'confirm', 'ok', 'sure']:
            bid = session.get('data', {}).get('_cancel_booking_id')
            if bid:
                try:
                    await self.db.update_booking_status(bid, 'cancelled')
                except Exception:
                    pass
            await self._log_and_send_response(user_number, "Your booking has been cancelled.", "booking_cancelled_success")
        else:
            await self._log_and_send_response(user_number, "Okay, I will keep your booking.", "booking_cancelled_aborted")
        session['state'] = ConversationState.SERVICE_SEARCH
        session['data'].pop('_cancel_booking_id', None)
        session['data'].pop('_bookings_list', None)

    async def handle_reschedule_booking_select(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        items = session.get('data', {}).get('_bookings_list') or []
        selected = None
        num_match = re.search(r"\b(\d+)\b", str(message_text))
        if num_match:
            i = int(num_match.group(1))
            if 1 <= i <= len(items):
                selected = items[i-1]
        if not selected:
            await self._log_and_send_response(user_number, "Please reply with the number of the booking to reschedule.", "reschedule_booking_select_invalid")
            return
        session['data']['_reschedule_booking_id'] = selected['id']
        await self._log_and_send_response(user_number, "What new date/time would you like? (e.g., 'tomorrow 10am', 'Dec 20 14:30')", "reschedule_booking_ask_time")
        session['state'] = ConversationState.RESCHEDULE_BOOKING_NEW_TIME

    async def handle_reschedule_booking_new_time(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        # Canonicalize new time
        new_dt = None
        try:
            new_dt = self._canonicalize_booking_time(message_text)
        except Exception:
            new_dt = None
        if not new_dt:
            await self._log_and_send_response(user_number, "I couldn't understand that time. Try 'tomorrow at 10am' or 'Dec 20 14:30'.", "reschedule_time_invalid")
            return
        new_iso = new_dt.strftime('%Y-%m-%d %H:%M')
        session['data']['_reschedule_new_time'] = new_iso
        await self._log_and_send_response(user_number, f"Reschedule to {new_iso}? Reply 'yes' to confirm or 'no' to keep the original time.", "reschedule_booking_confirm")
        session['state'] = ConversationState.RESCHEDULE_BOOKING_CONFIRM

    async def handle_reschedule_booking_confirm(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        text = message_text.strip().lower()
        bid = session.get('data', {}).get('_reschedule_booking_id')
        new_iso = session.get('data', {}).get('_reschedule_new_time')
        if text in ['yes', 'y', 'confirm', 'ok', 'sure'] and bid and new_iso:
            try:
                await self.db.update_booking_time(bid, new_iso, set_status='pending')
            except Exception:
                pass
            await self._log_and_send_response(user_number, f"Your booking has been rescheduled to {new_iso}.", "booking_rescheduled_success")
        else:
            await self._log_and_send_response(user_number, "Okay, I will keep your original booking time.", "booking_rescheduled_aborted")
        session['state'] = ConversationState.SERVICE_SEARCH
        session['data'].pop('_reschedule_booking_id', None)
        session['data'].pop('_reschedule_new_time', None)
        session['data'].pop('_bookings_list', None)
