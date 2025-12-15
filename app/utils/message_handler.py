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
    ONBOARDING_EMAIL = "onboarding_email"
    ONBOARDING_PREFERENCES = "onboarding_preferences"
    
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
        # Some terminals on Windows can't render emojis / non-ASCII; strip them from log preview
        preview = message[:100]
        try:
            safe_preview = preview.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
        except Exception:
            safe_preview = preview[:80]
        logger.info(f"[BOT RESPONSE] To: {user_number}, Type: {response_type}, Message: {safe_preview}...")

        # Network / Baileys errors (e.g., 404 from /send-text) should not crash the app
        try:
            await self.whatsapp_api.send_text_message(user_number, message)
        except Exception as e:
            logger.warning(f"Failed to send WhatsApp message to {user_number}: {e}")
            # Do not re-raise; booking/flow logic should continue even if delivery fails
        
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
                    "By continuing, you agree to our User Policy (reply POLICY to read it anytime).",
                    "You can still use Hustlr without extra data."
                ),
                "onboarding_privacy_declined"
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
                # Record core consent flags and proceed to email collection
                session['data']['agreed_privacy_policy'] = True
                session['data']['consent_transactional'] = True
                session['data']['consent_marketing'] = False
                session['data']['consent_timestamp'] = datetime.utcnow().isoformat()

                await self._log_and_send_response(
                    user_number,
                    self._short(
                        "If you'd like email confirmations and account recovery, please share your email address now, or reply 'skip'.",
                        "Share your email for confirmations, or reply 'skip'."
                    ),
                    "onboarding_ask_email"
                )
                session['state'] = ConversationState.ONBOARDING_EMAIL
            else:
                await self._log_and_send_response(
                    user_number,
                    self._short(
                        "You need to agree to the privacy policy to use Hustlr.\n\n"
                        "Type 'yes' to agree, or 'no' to decline.",
                        "You need to agree to the privacy policy to use Hustlr.\n\n"
                        "Type 'yes' to agree, or 'no' to decline."
                    ),
                    "onboarding_privacy_declined"
                )
        
        elif state == ConversationState.ONBOARDING_EMAIL:
            # Optional email collection (allow 'skip')
            text = (message_text or '').strip()
            email = None
            if text.lower() not in ['skip', 'no', 'none', 'na', 'n/a', '']:
                # Very light validation
                if re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", text):
                    email = text
                else:
                    await self._log_and_send_response(
                        user_number,
                        self._short(
                            "That doesn't look like a valid email. Please send a correct email address, or reply 'skip' to continue without one.",
                            "Invalid email. Send a valid one or 'skip'."
                        ),
                        "onboarding_email_invalid"
                    )
                    return
            if email:
                session['data']['email'] = email

            # Ask for service preferences
            await self._log_and_send_response(
                user_number,
                self._short(
                    "Which services are you most interested in? For example: plumber, electrician, cleaner, driver. You can list several or reply 'skip'.",
                    "Which services do you use most? e.g. plumber, electrician, cleaner (or 'skip')."
                ),
                "onboarding_ask_preferences"
            )
            session['state'] = ConversationState.ONBOARDING_PREFERENCES

        elif state == ConversationState.ONBOARDING_PREFERENCES:
            text = (message_text or '').strip().lower()
            prefs: List[str] = []
            if text not in ['skip', 'no', 'none', 'na', 'n/a', '']:
                # Reuse service keyword mapping from extract_service_type
                services_map = {
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
                for keyword, service in services_map.items():
                    if keyword in text and service not in prefs:
                        prefs.append(service)

                if not prefs:
                    await self._log_and_send_response(
                        user_number,
                        self._short(
                            "I couldn't match any services from that. Try something like: plumber, electrician, cleaner, driver. Or reply 'skip'.",
                            "Couldn't match services. Try: plumber, electrician, cleaner (or 'skip')."
                        ),
                        "onboarding_preferences_invalid"
                    )
                    return

            if prefs:
                session['data']['service_preferences'] = prefs

            # Create user in database now that we have all onboarding data
            user_data = {
                'whatsapp_number': user_number,
                'name': session['data']['name'],
                'location': session['data']['location'],
                'email': session['data'].get('email'),
                'service_preferences': session['data'].get('service_preferences', []),
                'agreed_privacy_policy': bool(session['data'].get('agreed_privacy_policy')), 
                'consent_transactional': bool(session['data'].get('consent_transactional', True)),
                'consent_marketing': bool(session['data'].get('consent_marketing', False)),
                'consent_timestamp': session['data'].get('consent_timestamp') or datetime.utcnow().isoformat(),
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
                    "Sorry, there was an issue completing your registration. Please try again.",
                    "onboarding_error"
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
            # Only treat as resumable if there is meaningful booking data in the session
            data = session.get('data') or {}
            has_booking_context = any(
                k in data for k in [
                    'service_type',
                    'booking_time',
                    'location',
                    'selected_provider',
                    '_pending_booking',
                ]
            )

            last_activity_str = session.get('last_activity')
            if has_booking_context and last_activity_str:
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

        # Graceful conversation endings / small talk that should not trigger errors
        closing_phrases = [
            "nothing for today",
            "nothing today",
            "nothing now",
            "see you tomorrow",
            "see you later",
            "that is all",
            "that's all",
        ]
        if any(p in text_cmd for p in closing_phrases):
            await self._log_and_send_response(
                user_number,
                self._short(
                    "No problem. If you need anything later, just send me a message.",
                    "Got it. Message me anytime."
                ),
                "conversation_closing",
            )
            # Keep session state unchanged; user can resume later
            return
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
        
        # Check for help / policy / control commands
        if message_text in ['help', 'menu', 'options']:
            await self.send_help_menu(user_number)
            return

        if message_text in ['policy', 'user policy', 'terms', 'privacy']:
            from config import settings as _s  # local import to avoid circulars
            policy_text = getattr(_s, 'USER_POLICY_TEXT', None) or (
                "Hustlr User Policy:\n\n"
                "You can ask for help with local services, opt out at any time by replying STOP,"
                " and request deletion of your data by replying DELETE MY DATA."
            )
            await self._log_and_send_response(user_number, policy_text, 'user_policy')
            return

        if message_text in ['stop', 'opt out', 'opt-out', 'unsubscribe']:
            try:
                await self.db.update_user(user_number, {'consent_transactional': False, 'opted_out': True})
            except Exception:
                pass
            await self._log_and_send_response(
                user_number,
                "You have been opted out of Hustlr notifications. You can message again anytime to start a new chat.",
                'opt_out_confirm',
            )
            return

        if message_text in ['delete my data', 'delete data', 'erase my data', 'remove my data']:
            try:
                await self.db.delete_user_and_data(user_number)
            except Exception:
                pass
            await self._log_and_send_response(
                user_number,
                "Your Hustlr profile, session, and chat history have been deleted. Bookings already sent to providers may be kept for records.",
                'delete_data_confirm',
            )
            return

        # Simple greeting handler so a bare "hi" / "hello" always gets a reply
        if text_cmd in ["hi", "hello", "hey", "morning", "good morning", "good afternoon", "good evening"]:
            await self._log_and_send_response(
                user_number,
                self._short(
                    "Hi! I'm Hustlr on WhatsApp. I can help you find and book local service providers like plumbers and electricians. What do you need help with today?",
                    "Hi! What service do you need?",
                ),
                "greeting",
            )
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
                    session['data'].pop('provider_options_cached', None)
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
        """Detect if the user message contains a problem description implying a service type."""
        if not message_text:
            return None
        """Detect problem statements like 'I have a leaking pipe' and map to service type.
        
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
                    # Use ASCII-only arrow in logs to avoid Windows console encoding issues
                    logger.info(f"Problem detected: '{keyword}' -> service type: {service_type}")
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
            # Use AI to understand the request, but only show assistantMessage to the user
            ai_response = await self.lambda_service.invoke_question_answerer(
                message_text,
                {'name': user.get('name'), 'location': user.get('location')}
            )

            text = (ai_response or "").strip()
            # Strip ```json fences if Claude wraps output
            if text.startswith("```"):
                parts = text.split("```")
                if len(parts) >= 3:
                    text = parts[1].strip()

            try:
                payload = json.loads(text)
            except Exception:
                # Fallback: treat whole response as plain text
                await self._log_and_send_response(
                    user_number,
                    text or "Sorry, I couldn't process that.",
                    "ai_response_plain",
                )
                return

            if isinstance(payload, dict):
                assistant_msg = (payload.get('assistantMessage') or "").strip()
                if assistant_msg:
                    await self._log_and_send_response(user_number, assistant_msg, f"ai_{payload.get('status') or 'response'}")
                    return

            # Last resort if shape is unexpected
            await self._log_and_send_response(
                user_number,
                text or "Sorry, I couldn't process that.",
                "ai_response_fallback",
            )
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
        
        # Resolve provider choice from free-form text (numbers, ordinal words, or provider name)
        idx = self._resolve_provider_index_from_text(providers, message_text)
        selected_provider = providers[idx - 1] if idx and 1 <= idx <= len(providers) else None
        
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
                "Where should the provider come?"
            )
        await self._log_and_send_response(user_number, prompt, "confirm_location_prompt")
        session['state'] = ConversationState.CONFIRM_LOCATION

    async def _maybe_quick_provider_choice(self, user_number: str, message_text: str, session: Dict, user: Dict) -> bool:
        """Try to infer provider + time from a free-form user message.

        Returns True if this method fully handled the message (so caller
        should return), or False to let normal flow continue.
        """
        text = (message_text or '').strip().lower()
        saved_location = (user or {}).get('location')
        final_location: Optional[str] = None

        yes_values = {'yes', 'y', 'yeah', 'yep', 'sure', 'ok', 'okay'}

    async def send_due_booking_reminders(self, within_minutes: int = 30) -> None:
        """Send reminders for bookings whose start time is within the next window.

        This uses the 'date_time' field on bookings (stored as '%Y-%m-%d %H:%M')
        and only touches bookings with reminder_sent == False.
        """
        try:
            bookings = await self.db.get_bookings_needing_reminders(within_minutes=within_minutes)
        except Exception as e:
            logger.error(f"Failed to fetch bookings needing reminders: {e}")
            return

        for b in bookings:
            try:
                user_number = b.get('user_whatsapp_number') or b.get('customer_number')
                provider_name = None

                # Prefer Claude's snapshot if present
                snap = b.get('provider_snapshot') or {}
                if isinstance(snap, dict):
                    provider_name = snap.get('name')

                if not provider_name:
                    # Fallback to stored provider document if needed
                    try:
                        provider_id = b.get('provider_id')
                        provider = await self.db.get_provider_by_id(provider_id) if provider_id else None
                        provider_name = (provider or {}).get('name')
                    except Exception:
                        provider_name = None

                provider_name = provider_name or 'your provider'

                time_text = b.get('date_time') or (f"{b.get('date','')} {b.get('time','')}".strip())
                service = b.get('service_type') or (b.get('provider_snapshot') or {}).get('service') or 'service'

                if not user_number:
                    continue

                reminder_text = (
                    f"Reminder: Your {service} provider {provider_name} is scheduled for {time_text}. "
                    f"Reply HELP if you need assistance."
                )

                await self._log_and_send_response(user_number, reminder_text, "booking_reminder")
                await self.db.mark_booking_reminder_sent(b.get('booking_id'))
            except Exception as e:
                logger.error(f"Failed to send reminder for booking {b.get('booking_id')}: {e}")

    async def _notify_booking_other_party(self, actor_number: str, booking_id: str, change_type: str, new_time: Optional[str] = None) -> None:
        try:
            booking = await self.db.get_booking_by_id(booking_id)
        except Exception:
            booking = None
        if not booking:
            return

        user_num = booking.get('user_whatsapp_number') or booking.get('customer_number')
        provider_num = booking.get('provider_whatsapp_number')

        if not user_num or not provider_num:
            return

        if actor_number == user_num:
            target = provider_num
            role = 'provider'
        elif actor_number == provider_num:
            target = user_num
            role = 'customer'
        else:
            target = provider_num
            role = 'provider'

        service = booking.get('service_type') or 'service'
        time_text = booking.get('date_time') or (f"{booking.get('date','')} {booking.get('time','')}".strip()) or 'unscheduled time'
        location = booking.get('location') or (booking.get('provider_snapshot') or {}).get('location') or ''

        if change_type == 'cancelled':
            if role == 'provider':
                msg = f"A customer has cancelled their {service} booking for {time_text} at {location}."
            else:
                msg = f"Your {service} booking for {time_text} at {location} has been cancelled by the provider."
        elif change_type == 'rescheduled':
            nt = new_time or booking.get('date_time') or 'a new time'
            if role == 'provider':
                msg = f"A customer has rescheduled their {service} booking to {nt} at {location}."
            else:
                msg = f"Your {service} booking has been rescheduled to {nt}."
        else:
            return

        await self._log_and_send_response(target, msg, f"booking_{change_type}_notify_{role}")

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

    async def handle_ai_response(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Minimal AI handler to delegate conversation to Claude.

        This implementation keeps the existing Bedrock JSON contract but only
        uses `assistantMessage` for now, without advanced action routing.
        """
        user_context: Dict[str, Any] = {
            "user_name": (user or {}).get("name"),
            "user_location": (user or {}).get("location"),
            "session_state": str(session.get("state")),
            "known_fields": (session.get("data") or {}),
        }

        try:
            ai_raw = await self.lambda_service.invoke_question_answerer(
                message_text,
                user_context=user_context,
                conversation_history=None,
            )
        except Exception as e:
            logger.error(f"AI invoke failed for {user_number}: {e}")
            await self._log_and_send_response(
                user_number,
                "Sorry, I couldn't process that right now. Please try again in a moment.",
                "ai_invoke_error",
            )
            return

        # Strip markdown fences if present and remove optional language tag (e.g. ```json)
        text = (ai_raw or "").strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                inner = parts[1].strip()
                # Drop a leading language identifier like 'json' if present
                if inner.lower().startswith("json"):
                    # Remove the word 'json' and any immediate newline/space after it
                    inner = inner[4:].lstrip("\n\r ")
                text = inner

        payload: Any = None
        try:
            payload = json.loads(text)
        except Exception:
            # Treat whole response as plain text if JSON parsing fails
            await self._log_and_send_response(
                user_number,
                text or (ai_raw or "").strip() or "Sorry, I couldn't process that.",
                "ai_plain",
            )
            return

        # If it's a bare dict in our standard shape, prefer assistantMessage
        if isinstance(payload, dict):
            status = str(payload.get("status") or "").upper()
            field = str(payload.get("field") or "").lower()
            data = payload.get("data") or {}

            assistant_msg = (payload.get("assistantMessage") or "").strip()
            if assistant_msg:
                await self._log_and_send_response(user_number, assistant_msg, f"ai_{status or 'reply'}")
            else:
                # Fallback to entire JSON as text if no assistantMessage
                await self._log_and_send_response(user_number, ai_raw.strip(), "ai_fallback_json")

            # Shallow merge any data into session for future turns
            if isinstance(data, dict):
                session.setdefault("data", {})
                session["data"].update(data)

            # Special handling: when Claude says CONFIRM selected_provider, actually
            # list real providers for the chosen service/location so the user can pick.
            if status == "CONFIRM" and field == "selected_provider":
                try:
                    service_type = (data.get("service_type") or (session.get("data") or {}).get("service_type") or "").strip().lower()
                    location = (data.get("location") or (session.get("data") or {}).get("location") or (user or {}).get("location") or "").strip()
                    if service_type:
                        providers = await self.db.get_providers_by_service(service_type, location or None)
                    else:
                        providers = []

                    if not providers:
                        await self._log_and_send_response(
                            user_number,
                            self._short(
                                f"Sorry, no {service_type or 'provider'}s available in your area right now.",
                                "Sorry, no providers available right now."
                            ),
                            "ai_no_providers_for_confirm",
                        )
                        return

                    # Cache providers in session and move to PROVIDER_SELECTION
                    session.setdefault("data", {})
                    session["data"]["service_type"] = service_type
                    session["data"]["providers"] = providers
                    if location:
                        session["data"]["location"] = location

                    # Build interactive buttons (reuse existing UX)
                    buttons: List[Dict[str, Any]] = []
                    for p in providers[:3]:
                        buttons.append({
                            "id": f"provider_{p.get('whatsapp_number') or p.get('_id')}",
                            "title": f"{p.get('name') or 'Provider'}",
                        })

                    header_loc = location or (user or {}).get("location") or "your area"
                    await self._log_and_send_interactive(
                        user_number,
                        f"Available {service_type}s in {header_loc}",
                        self._build_friendly_provider_body(service_type or 'provider', header_loc, len(providers), session),
                        buttons,
                        self._friendly_footer(),
                    )

                    session["state"] = ConversationState.PROVIDER_SELECTION
                    return
                except Exception as e:
                    logger.error(f"Error while listing providers after AI CONFIRM: {e}")

            # Execute booking-level actions that Claude has already explained
            # to the user via assistantMessage. Backend only performs the
            # state change; all wording stays with Claude.
            if status == "COMPLETE" and field == "cancel_booking":
                try:
                    # Claude may send a single booking_id or a list of booking_ids
                    bids_any = []
                    # Single id
                    single_bid = (data or {}).get("booking_id") or (session.get("data") or {}).get("_cancel_booking_id")
                    if single_bid:
                        bids_any.append(single_bid)
                    # List of ids
                    many_bids = (data or {}).get("booking_ids") or []
                    if isinstance(many_bids, list):
                        bids_any.extend([b for b in many_bids if b])

                    for bid in bids_any:
                        try:
                            await self.db.update_booking_status(bid, "cancelled")
                        except Exception:
                            # Ignore per-booking failures; Claude has already
                            # informed the user in assistantMessage.
                            pass
                finally:
                    # Clear any local helper fields but keep general session data
                    if session.get("data"):
                        session["data"].pop("_cancel_booking_id", None)
                        session["data"].pop("_bookings_list", None)
                    session["state"] = ConversationState.SERVICE_SEARCH
                return

            if status == "COMPLETE" and field == "reschedule_booking":
                try:
                    bid = (data or {}).get("booking_id") or (session.get("data") or {}).get("_reschedule_booking_id")
                    new_time = (data or {}).get("new_time") or (data or {}).get("date_time") or (session.get("data") or {}).get("_reschedule_new_time")
                    if bid and new_time:
                        try:
                            await self.db.update_booking_time(bid, new_time, set_status="pending")
                        except Exception:
                            pass
                finally:
                    if session.get("data"):
                        session["data"].pop("_reschedule_booking_id", None)
                        session["data"].pop("_reschedule_new_time", None)
                        session["data"].pop("_bookings_list", None)
                    session["state"] = ConversationState.SERVICE_SEARCH
                return

            return

        # Last resort
        await self._log_and_send_response(user_number, (ai_raw or "").strip() or "Sorry, I couldn't process that.", "ai_unknown_payload")
    
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
            idx: Optional[int] = self._resolve_provider_index_from_text(providers, message_text)

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
                    await self._notify_booking_other_party(user_number, bid, 'cancelled')
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
                await self._notify_booking_other_party(user_number, bid, 'rescheduled', new_time=new_iso)
            except Exception:
                pass
            await self._log_and_send_response(user_number, f"Your booking has been rescheduled to {new_iso}.", "booking_rescheduled_success")
        else:
            await self._log_and_send_response(user_number, "Okay, I will keep your original booking time.", "booking_rescheduled_aborted")
        session['state'] = ConversationState.SERVICE_SEARCH
        session['data'].pop('_reschedule_booking_id', None)
        session['data'].pop('_reschedule_new_time', None)
        session['data'].pop('_bookings_list', None)
