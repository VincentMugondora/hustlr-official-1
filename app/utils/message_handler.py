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
    CANCEL_EXISTING_BOOKING_CONFIRM = "cancel_existing_booking_confirm"
    
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
        self.ai_paused = False
    
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
        return "Reply with one or more numbers (e.g., 1 or 1, 2)" if self._is_concise() else "Tap one or more providers or reply with numbers (e.g., 1 or 1, 2) to book."
    
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
        elif current_state in {
            ConversationState.PROVIDER_REGISTER,
            ConversationState.PROVIDER_REGISTER_NAME,
            ConversationState.PROVIDER_REGISTER_SERVICE,
            ConversationState.PROVIDER_REGISTER_LOCATION,
            ConversationState.PROVIDER_REGISTER_BUSINESS,
            ConversationState.PROVIDER_REGISTER_CONTACT,
        }:
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
                services_map = self.extract_service_type(text, return_map=True)
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

    def _normalize_msisdn(self, phone: str) -> Optional[str]:
        s = re.sub(r"\D+", "", str(phone or ""))
        if not s:
            return None
        if s.startswith("0") and len(s) >= 9:
            return "263" + s[1:]
        if s.startswith("7") and len(s) >= 9:
            return "263" + s
        if s.startswith("263"):
            return s
        if len(s) >= 9:
            return "263" + s
        return s

    def _admin_numbers(self) -> List[str]:
        try:
            raw = getattr(settings, 'ADMIN_WHATSAPP_NUMBERS', "") or ""
        except Exception:
            raw = ""
        if isinstance(raw, (list, tuple)):
            vals = list(raw)
        else:
            vals = [p.strip() for p in str(raw).replace(";", ",").split(",") if p.strip()]
        if not vals:
            vals = ['+263783961640', '+263775251636', '+263777530322', '+16509965727']
        norm = []
        for v in vals:
            n = self._normalize_msisdn(v)
            if n:
                norm.append(n)
        return list(dict.fromkeys(norm))

    async def _notify_admins_new_provider(self, provider: Dict[str, Any]) -> None:
        admins = self._admin_numbers()
        if not admins:
            return
        name = provider.get('name') or ''
        svc = provider.get('service_type') or ''
        loc = provider.get('location') or ''
        phone = provider.get('whatsapp_number') or provider.get('contact') or ''
        phone_norm = self._normalize_msisdn(phone) or phone
        lines = [
            f"New provider registration",
            f"Name: {name}",
            f"Service: {svc}",
            f"Location: {loc}",
            f"Phone: {phone_norm}",
            "Reply APPROVE <number> to approve, or DENY <number> to reject.",
        ]
        body = "\n".join(lines)
        for a in admins:
            try:
                await self._log_and_send_response(a, body, "admin_new_provider")
            except Exception:
                pass

    async def handle_provider_registration(self, user_number: str, message_text: str, session: Dict) -> None:
        state = session.get('state')
        sd = session.setdefault('data', {})
        reg = sd.setdefault('_prov_reg', {})
        text = (message_text or '').strip()
        if state == ConversationState.PROVIDER_REGISTER:
            await self._log_and_send_response(
                user_number,
                self._short("Welcome! Please send your full name to register as a service provider.", "Your full name?"),
                "provider_register_name"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_NAME
            return
        if state == ConversationState.PROVIDER_REGISTER_NAME:
            reg['name'] = text.title()
            await self._log_and_send_response(
                user_number,
                self._short("What service do you offer? (e.g., plumber, electrician)", "What service do you offer?"),
                "provider_register_service"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_SERVICE
            return
        if state == ConversationState.PROVIDER_REGISTER_SERVICE:
            reg['service_type'] = text.strip().lower()
            await self._log_and_send_response(
                user_number,
                self._short("Which area are you based in? (e.g., Harare, Bulawayo)", "Your area?"),
                "provider_register_location"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_LOCATION
            return
        if state == ConversationState.PROVIDER_REGISTER_LOCATION:
            reg['location'] = text
            await self._log_and_send_response(
                user_number,
                self._short("Business name (or reply 'skip')", "Business name (or 'skip')"),
                "provider_register_business"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_BUSINESS
            return
        if state == ConversationState.PROVIDER_REGISTER_BUSINESS:
            if text.lower() not in {"skip", "-", "n/a", "none"}:
                reg['business_name'] = text
            else:
                reg['business_name'] = reg.get('name')
            await self._log_and_send_response(
                user_number,
                self._short("Send your WhatsApp number (or reply 'skip' to use this number)", "Your WhatsApp number? ('skip' to use this)"),
                "provider_register_contact"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_CONTACT
            return
        if state == ConversationState.PROVIDER_REGISTER_CONTACT:
            number = None
            if text.lower() in {"skip", "same", "use this"} or not text:
                number = user_number
            else:
                number = self._normalize_msisdn(text)
            if not number:
                await self._log_and_send_response(user_number, self._short("Please send a valid phone number or 'skip' to use this one.", "Send a valid number or 'skip'."), "provider_register_contact_invalid")
                return
            reg['whatsapp_number'] = number
            reg['contact'] = number
            missing = [k for k in ['name','service_type','location','whatsapp_number'] if not reg.get(k)]
            if missing:
                await self._log_and_send_response(user_number, "Missing some details. Please start again with 'register'.", "provider_registration_missing")
                session['state'] = ConversationState.SERVICE_SEARCH
                sd.pop('_prov_reg', None)
                return
            doc = {
                'whatsapp_number': reg['whatsapp_number'],
                'name': reg['name'],
                'service_type': reg['service_type'],
                'location': reg['location'],
                'business_name': reg.get('business_name') or reg['name'],
                'contact': reg['contact'],
                'status': 'pending',
            }
            ok = await self.db.create_provider(doc)
            if ok:
                await self._log_and_send_response(user_number, self._short("Registration received. We'll review and notify you soon.", "Registration submitted. We'll notify you."), "provider_registration_complete")
                try:
                    prov = await self.db.get_provider_by_phone(doc['whatsapp_number'])
                except Exception:
                    prov = doc
                await self._notify_admins_new_provider(prov or doc)
                session['state'] = ConversationState.SERVICE_SEARCH
                sd.pop('_prov_reg', None)
                return
            await self._log_and_send_response(user_number, "Sorry, there was an issue with your registration. Please try again.", "provider_registration_error")
            session['state'] = ConversationState.SERVICE_SEARCH
            sd.pop('_prov_reg', None)
            return

    async def handle_admin_approval(self, user_number: str, message_text: str, session: Dict) -> None:
        admins = set(self._admin_numbers())
        actor = self._normalize_msisdn(user_number)
        if actor not in admins:
            await self._log_and_send_response(user_number, "You are not authorized to approve providers.", "admin_not_authorized")
            return
        text = (message_text or '').strip().lower()
        action = None
        m = re.match(r"^\s*(approve|deny)\s+(.+)$", text)
        if m:
            action = m.group(1)
            num_raw = m.group(2)
        else:
            await self._log_and_send_response(user_number, "Send 'approve <number>' or 'deny <number>'.", "admin_approval_help")
            return
        target_num = self._normalize_msisdn(num_raw)
        if not target_num:
            await self._log_and_send_response(user_number, "Please include a valid phone number.", "admin_number_invalid")
            return
        prov = await self.db.get_provider_by_phone(target_num)
        if not prov:
            await self._log_and_send_response(user_number, f"No provider found for {target_num}.", "admin_provider_not_found")
            return
        prov_id = str(prov.get('_id')) if prov.get('_id') else None
        if not prov_id:
            await self._log_and_send_response(user_number, "Unable to update provider.", "admin_update_failed")
            return
        if action == 'approve':
            await self.db.update_provider_status(prov_id, 'active')
            await self._log_and_send_response(user_number, f"Approved {prov.get('name')} ({target_num}).", "admin_approved")
            try:
                await self._log_and_send_response(target_num, "Your provider registration has been approved. You are now listed and can receive bookings.", "provider_approved")
            except Exception:
                pass
            others = [a for a in admins if a != actor]
            note = f"Provider approved: {prov.get('name')} — {prov.get('service_type')} — {target_num} (by {actor})."
            for a in others:
                try:
                    await self._log_and_send_response(a, note, "admin_approval_broadcast")
                except Exception:
                    pass
            return
        else:
            await self.db.update_provider_status(prov_id, 'rejected')
            await self._log_and_send_response(user_number, f"Rejected {prov.get('name')} ({target_num}).", "admin_rejected")
            try:
                await self._log_and_send_response(target_num, "Your provider registration has been rejected. You may reply REGISTER to try again.", "provider_rejected")
            except Exception:
                pass
            others = [a for a in admins if a != actor]
            note = f"Provider rejected: {prov.get('name')} — {prov.get('service_type')} — {target_num} (by {actor})."
            for a in others:
                try:
                    await self._log_and_send_response(a, note, "admin_approval_broadcast")
                except Exception:
                    pass
            return

    async def handle_admin_commands(self, user_number: str, message_text: str, session: Dict) -> None:
        actor = self._normalize_msisdn(user_number)
        text = (message_text or '').strip()
        low = text.lower()
        def arg_after(prefix: str) -> str:
            p = low.find(prefix)
            if p == -1:
                return ''
            return text[p+len(prefix):].strip()
        async def send(msg: str, t: str = "admin"):
            await self._log_and_send_response(user_number, msg, t)

        if low in {'/help', '/admin', '/commands'}:
            await self._send_admin_help_via_ai(user_number)
            return

        if low.startswith('/providers'):
            parts = text.split(maxsplit=2)
            status = None
            service = None
            if len(parts) >= 2:
                q = parts[1].strip().lower()
                if q in {'pending','active','rejected','suspended','blacklisted'}:
                    status = q
                else:
                    service = q
            lst = await self.db.list_providers(status=status, service_type=service, limit=20)
            if not lst:
                await send("No providers found.")
                return
            lines = []
            for d in lst:
                lines.append(f"{str(d.get('_id'))[-6:]} | {d.get('name')} | {d.get('service_type')} | {d.get('status')} | {d.get('whatsapp_number')}")
            await send("Providers:\n" + "\n".join(lines), "admin_providers")
            return

        if low.startswith('/provider'):
            token = arg_after('/provider')
            token = token.split()[0] if token else ''
            prov = None
            if re.fullmatch(r"[0-9a-f]{24}", token):
                prov = await self.db.get_provider_by_id(token)
            else:
                pn = self._normalize_msisdn(token)
                if pn:
                    prov = await self.db.get_provider_by_phone(pn)
            if not prov:
                await send("Provider not found.")
                return
            await send(f"Provider:\nID: {prov.get('_id')}\nName: {prov.get('name')}\nService: {prov.get('service_type')}\nStatus: {prov.get('status')}\nPhone: {prov.get('whatsapp_number')}\nLocation: {prov.get('location')}", "admin_provider")
            return

        if low.startswith('/approve provider') or low.startswith('/reject provider') or low.startswith('/suspend provider') or low.startswith('/reinstate provider') or low.startswith('/blacklist provider'):
            action = low.split()[0][1:]
            token = arg_after('/'+action+' provider')
            token = token.split()[0] if token else ''
            prov = None
            if re.fullmatch(r"[0-9a-f]{24}", token):
                prov = await self.db.get_provider_by_id(token)
            else:
                pn = self._normalize_msisdn(token)
                if pn:
                    prov = await self.db.get_provider_by_phone(pn)
            if not prov:
                await send("Provider not found.")
                return
            pid = str(prov.get('_id'))
            if action == 'approve' or action == 'reinstate':
                await self.db.update_provider_status(pid, 'active')
                await send(f"Provider approved: {prov.get('name')} ({prov.get('whatsapp_number')}).", "admin_approved")
                try:
                    await self._log_and_send_response(prov.get('whatsapp_number'), "Your provider account is now active.", "provider_approved")
                except Exception:
                    pass
            elif action == 'reject':
                await self.db.update_provider_status(pid, 'rejected')
                await send(f"Provider rejected: {prov.get('name')}.")
                try:
                    await self._log_and_send_response(prov.get('whatsapp_number'), "Your provider registration was rejected.", "provider_rejected")
                except Exception:
                    pass
            elif action == 'suspend' or action == 'blacklist':
                await self.db.update_provider_status(pid, 'blacklisted' if action=='blacklist' else 'suspended')
                await send(f"Provider {action}ed: {prov.get('name')}.")
            return

        if low.startswith('/edit provider'):
            rest = arg_after('/edit provider')
            parts = rest.split()
            token = parts[0] if parts else ''
            fields_text = rest[len(token):].strip()
            prov = None
            if re.fullmatch(r"[0-9a-f]{24}", token):
                prov = await self.db.get_provider_by_id(token)
            else:
                pn = self._normalize_msisdn(token)
                if pn:
                    prov = await self.db.get_provider_by_phone(pn)
            if not prov:
                await send("Provider not found.")
                return
            updates: Dict[str, Any] = {}
            for m in re.finditer(r"(\w+)=\"([^\"]*)\"", fields_text):
                updates[m.group(1)] = m.group(2)
            if not updates:
                await send("No fields provided.")
                return
            ok = await self.db.update_provider_fields(str(prov.get('_id')), updates)
            await send("Updated." if ok else "No change.")
            return

        if low.startswith('/bookings'):
            now = datetime.utcnow()
            start = None
            if ' today' in low:
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            if ' week' in low:
                start = now - timedelta(days=7)
            items = await self.db.list_bookings(limit=20, start=start, end=None)
            if not items:
                await send("No bookings found.")
                return
            lines = []
            for b in items:
                lines.append(f"{b.get('booking_id','')} | {b.get('service_type','')} | {b.get('status','')} | {b.get('user_whatsapp_number','')} -> {b.get('provider_whatsapp_number','')}")
            await send("Bookings:\n" + "\n".join(lines), "admin_bookings")
            return

        if low.startswith('/booking'):
            bid = arg_after('/booking').split()[0]
            b = await self.db.get_booking_by_id(bid)
            if not b:
                await send("Booking not found.")
                return
            await send(f"Booking {b.get('booking_id')}\nService: {b.get('service_type')}\nStatus: {b.get('status')}\nUser: {b.get('user_whatsapp_number')}\nProvider: {b.get('provider_whatsapp_number')}\nTime: {b.get('date_time')}", "admin_booking")
            return

        if low.startswith('/assign booking') or low.startswith('/reassign booking'):
            bid = arg_after('/assign booking' if low.startswith('/assign') else '/reassign booking').split()[0]
            pv = None
            m = re.search(r"provider\s+([\w\+\-]+)$", text, re.I)
            token = m.group(1) if m else ''
            prov = None
            if re.fullmatch(r"[0-9a-f]{24}", token):
                prov = await self.db.get_provider_by_id(token)
            else:
                pn = self._normalize_msisdn(token)
                if pn:
                    prov = await self.db.get_provider_by_phone(pn)
            if not prov:
                await send("Provider not found.")
                return
            updates = {
                'provider_id': str(prov.get('_id')),
                'provider_whatsapp_number': prov.get('whatsapp_number'),
                'status': 'assigned'
            }
            ok = await self.db.update_booking_fields(bid, updates)
            await send("Assigned." if ok else "No change.")
            return

        if low.startswith('/cancel booking'):
            bid = arg_after('/cancel booking').split()[0]
            m = re.search(r"reason=\"([^\"]*)\"", text)
            reason = m.group(1) if m else ''
            ok = await self.db.update_booking_fields(bid, {'status': 'cancelled', 'cancel_reason': reason})
            await send("Cancelled." if ok else "No change.")
            return

        if low.startswith('/complete booking'):
            bid = arg_after('/complete booking').split()[0]
            ok = await self.db.update_booking_fields(bid, {'status': 'completed'})
            await send("Completed." if ok else "No change.")
            return

        if low.startswith('/conversation'):
            msisdn = self._normalize_msisdn(arg_after('/conversation').split()[0])
            if not msisdn:
                await send("Provide a WhatsApp number.")
                return
            msgs = await self.db.get_conversation_history(msisdn, limit=10)
            if not msgs:
                await send("No recent messages.")
                return
            lines = [f"{m['role']}: {m['text'][:120]}" for m in msgs]
            await send("Conversation:\n" + "\n".join(lines), "admin_conversation")
            return

        if low.startswith('/reset conversation'):
            msisdn = self._normalize_msisdn(arg_after('/reset conversation').split()[0])
            if not msisdn:
                await send("Provide a WhatsApp number.")
                return
            await self.db.delete_session(msisdn)
            await self.db.delete_conversation_history(msisdn)
            await send("Conversation reset.")
            return

        if low.startswith('/services'):
            items = await self.db.list_providers(limit=200)
            st = []
            for d in items:
                try:
                    val = (d.get('service_type') or '').strip()
                    if val and val not in st:
                        st.append(val)
                except Exception:
                    pass
            await send("Services:\n" + ("\n".join(st) if st else "None"), "admin_services")
            return

        if low.startswith('/stats'):
            now = datetime.utcnow()
            window = None
            if ' today' in low:
                window = (now.replace(hour=0, minute=0, second=0, microsecond=0), None)
            elif ' week' in low:
                window = (now - timedelta(days=7), None)
            b_total = await self.db.count_bookings(*(window or (None, None)))
            b_completed = await self.db.count_bookings_by_status('completed', *(window or (None, None)))
            prov_active = await self.db.count_providers('active')
            users = await self.db.count_users(*(window or (None, None)))
            await send(f"Stats:\nBookings: {b_total}\nCompleted: {b_completed}\nActive providers: {prov_active}\nNew users: {users}", "admin_stats")
            return

        if low.startswith('/ai status'):
            await send(f"AI: {'paused' if getattr(self, 'ai_paused', False) else 'active'}", "admin_ai")
            return
        if low.startswith('/ai pause'):
            self.ai_paused = True
            await send("AI paused.", "admin_ai")
            return
        if low.startswith('/ai resume'):
            self.ai_paused = False
            await send("AI resumed.", "admin_ai")
            return

        if low.startswith('/panic booking'):
            bid = arg_after('/panic booking').split()[0]
            ok = await self.db.update_booking_fields(bid, {'status': 'panic', 'flagged': True})
            await send("Flagged." if ok else "No change.")
            return

        if low.startswith('/block user'):
            msisdn = self._normalize_msisdn(arg_after('/block user').split()[0])
            if not msisdn:
                await send("Provide a WhatsApp number.")
                return
            await self.db.update_user(msisdn, {'opted_out': True, 'consent_transactional': False})
            await send("User blocked.")
            return

        if low.startswith('/announce admins'):
            admins = self._admin_numbers()
            if not admins:
                await send("No admin numbers configured.")
                return
            lines = [
                "Hello! You have been added as a Hustlr Admin to help vet and verify service providers, manage bookings, and ensure quality.",
                "",
                "Your WhatsApp admin privileges:",
                "• Providers: list/view, approve/reject, suspend/reinstate/blacklist, edit details.",
                "• Bookings: list/view, assign/reassign provider, cancel (with reason), complete, panic/flag.",
                "• Conversations: view recent history, reset a user conversation.",
                "• Services: list current service types.",
                "• Stats & AI: quick stats; pause/resume AI.",
                "• Safety: block/opt-out a user.",
                "",
                "Type /help for the full command list.",
                "Operate professionally and do not book services as a customer.",
            ]
            body = "\n".join(lines)
            sent = 0
            for a in admins:
                try:
                    await self._log_and_send_response(a, body, "admin_announcement")
                    sent += 1
                except Exception:
                    pass
            await send(f"Announcement sent to {sent} admins.", "admin_announce_done")
            return

        await send("Unknown admin command. Type /help.", "admin_unknown")

    async def _send_admin_help_via_ai(self, user_number: str) -> None:
        if getattr(self, 'ai_paused', False):
            msg = (
                "Admin commands:\n"
                "/providers [/pending|<service>]\n"
                "/provider <id|phone>\n"
                "/approve <phone> | /reject <phone>\n"
                "/suspend <id|phone> | /reinstate <id|phone>\n"
                "/edit provider <id|phone> key=\"val\"\n"
                "/bookings [today|week] | /booking <id>\n"
                "/assign booking <id> provider <id|phone>\n"
                "/reassign booking <id> provider <id|phone>\n"
                "/cancel booking <id> reason=\"...\" | /complete booking <id>\n"
                "/conversation <msisdn> | /reset conversation <msisdn>\n"
                "/services | /stats [today|week]\n"
                "/ai [status|pause|resume] | /block user <msisdn> | /blacklist provider <id|phone>"
            )
            await self._log_and_send_response(user_number, msg, "admin_help")
            return
        try:
            commands = (
                "Admin: /providers [/pending|<service>] | /provider <id|phone> | /approve <phone> | /reject <phone> | "
                "/suspend <id|phone> | /reinstate <id|phone> | /edit provider <id|phone> key=\"val\"... | /bookings [today|week] | "
                "/booking <id> | /assign booking <id> provider <id|phone> | /reassign booking <id> provider <id|phone> | "
                "/cancel booking <id> reason=\"...\" | /complete booking <id> | /conversation <msisdn> | /reset conversation <msisdn> | "
                "/services | /stats [today|week] | /ai [status|pause|resume] | /block user <msisdn> | /blacklist provider <id|phone>"
            )
            prompt = (
                "Format the admin command list into a clean WhatsApp help message with short lines. "
                "Requirements: PLAIN TEXT ONLY, ASCII ONLY, NO EMOJIS, NO MARKDOWN. "
                "Start with 'Admin Commands' on its own line, then three sections: 'Providers:', 'Bookings:', 'System:'. "
                "Under each section, list concise items with '-' bullets. Keep <= 12 total lines. "
                "Do not add any commands not provided. Return JSON with status=\"ASK\", field=\"admin_help\", data={}, and assistantMessage only.\n\n"
                f"Commands:\n{commands}"
            )
            raw = await self.lambda_service.invoke_question_answerer(prompt, user_context={"session_state": "admin_help", "known_fields": {}})
            text = (raw or "").strip()
            if text.startswith("```"):
                parts = text.split("```")
                if len(parts) >= 3:
                    inner = parts[1].strip()
                    if inner.lower().startswith("json"):
                        inner = inner[4:].lstrip("\n\r ")
                    text = inner
            msg = None
            try:
                payload = json.loads(text)
                if isinstance(payload, dict):
                    msg = (payload.get("assistantMessage") or "").strip()
            except Exception:
                msg = None
            if not msg:
                msg = (
                    "Admin Commands\n\n"
                    "Providers:\n"
                    "- /providers [pending|<service>]\n"
                    "- /provider <id|phone>\n"
                    "- /approve <phone> | /reject <phone>\n"
                    "- /suspend <id|phone> | /reinstate <id|phone>\n"
                    "- /edit provider <id|phone> key=\"val\"\n\n"
                    "Bookings:\n"
                    "- /bookings [today|week] | /booking <id>\n"
                    "- /assign booking <id> provider <id|phone> | /reassign booking <id> provider <id|phone>\n"
                    "- /cancel booking <id> reason=\"...\" | /complete booking <id>\n\n"
                    "System:\n"
                    "- /conversation <msisdn> | /reset conversation <msisdn>\n"
                    "- /services | /stats [today|week]\n"
                    "- /ai [status|pause|resume] | /block user <msisdn> | /blacklist provider <id|phone>"
                )
            await self._log_and_send_response(user_number, msg, "admin_help_ai")
        except Exception:
            fallback = (
                "Admin Commands\n\n"
                "Providers:\n"
                "- /providers [pending|<service>]\n"
                "- /provider <id|phone>\n"
                "- /approve <phone> | /reject <phone>\n"
                "- /suspend <id|phone> | /reinstate <id|phone>\n"
                "- /edit provider <id|phone> key=\"val\"\n\n"
                "Bookings:\n"
                "- /bookings [today|week] | /booking <id>\n"
                "- /assign booking <id> provider <id|phone> | /reassign booking <id> provider <id|phone>\n"
                "- /cancel booking <id> reason=\"...\" | /complete booking <id>\n\n"
                "System:\n"
                "- /conversation <msisdn> | /reset conversation <msisdn>\n"
                "- /services | /stats [today|week]\n"
                "- /ai [status|pause|resume] | /block user <msisdn> | /blacklist provider <id|phone>"
            )
            await self._log_and_send_response(user_number, fallback, "admin_help")

    async def handle_admin_natural_language(self, user_number: str, message_text: str, session: Dict) -> bool:
        """Admin NL handler powered by Claude. Returns True if handled."""
        actor = self._normalize_msisdn(user_number)
        user_ctx = {
            'role': 'admin',
            'adminLevel': 'super',
            'system_prompt_override': getattr(settings, 'HUSTLR_ADMIN_PROMPT_V1', None),
            'prompt_version': 'hustlr_admin_prompt_v1',
            'user_name': actor,
            'known_fields': (session.get('admin_state') or {}),
        }
        try:
            raw = await self.lambda_service.invoke_question_answerer(
                message_text,
                user_context=user_ctx,
                conversation_history=None,
            )
        except Exception as e:
            await self._log_and_send_response(user_number, "Sorry, admin assistant is unavailable now.", "admin_ai_error")
            return True

        text = (raw or '').strip()
        if text.startswith('```'):
            parts = text.split('```')
            if len(parts) >= 3:
                inner = parts[1].strip()
                if inner.lower().startswith('json'):
                    inner = inner[4:].lstrip('\n\r ')
                text = inner
        try:
            payload = json.loads(text)
        except Exception:
            # Treat as plain advice if not JSON
            await self._log_and_send_response(user_number, text, "admin_ai_plain")
            return True

        if not isinstance(payload, dict):
            await self._log_and_send_response(user_number, "I couldn't parse that.", "admin_ai_parse_error")
            return True

        # Prefer assistantMessage; tolerate legacy 'response'; fallback to a clarification question
        assistant_msg = (payload.get('assistantMessage') or payload.get('response') or '').strip()
        clar_q = (payload.get('clarificationQuestion') or payload.get('clarification') or payload.get('question') or '').strip()
        if assistant_msg:
            await self._log_and_send_response(user_number, assistant_msg, "admin_ai_assistant")
        elif clar_q:
            await self._log_and_send_response(user_number, clar_q, "admin_ai_clarify")

        action = payload.get('action') or {}
        entities = payload.get('entities') or {}
        if not action or not isinstance(action, dict) or not action.get('type'):
            return True

        # Handle help/clarification intents explicitly without executing backend actions
        t_raw = str(action.get('type') or '')
        t_upper = t_raw.upper()
        # Backend safety: always require confirmation for dangerous deletes
        try:
            if t_upper == 'DELETE_ACCOUNT':
                action['requiresConfirmation'] = True
            # If hard-delete mode is explicitly requested, force confirmation too
            if t_upper == 'DELETE_ACCOUNT' and str((entities.get('mode') or '')).lower() == 'hard':
                action['requiresConfirmation'] = True
        except Exception:
            pass
        if t_raw.upper() == 'SHOW_HELP':
            # Admin help is delegated to Claude; no static command lists
            return True
        if t_raw.upper() in {'CLARIFICATION_NEEDED', 'NO_ACTION', 'SMALL_TALK'}:
            # Claude asked for clarification or signaled no backend action
            return True

        # Confirmation flow
        requires = bool(action.get('requiresConfirmation'))
        if requires:
            session.setdefault('admin_state', {})
            session['admin_state']['pending_action'] = action
            session['admin_state']['pending_entities'] = entities
            # If Claude did not include a confirmation text, send a generic one
            if not assistant_msg:
                await self._log_and_send_response(user_number, "Please CONFIRM or CANCEL.", "admin_confirm")
            return True

        ok, result_msg = await self._execute_admin_action(actor, action, entities)
        try:
            audit_doc = {
                'admin': actor,
                'action': action.get('type'),
                'entities': entities,
                'result': 'ok' if ok else 'failed',
                'prompt_version': 'hustlr_admin_prompt_v1',
            }
            # Add common fields for easier querying/audits
            try:
                audit_doc['target'] = (entities.get('target') or '').strip()
                audit_doc['identifier'] = (entities.get('identifier') or entities.get('phone') or entities.get('id') or '').strip()
                audit_doc['reason'] = (entities.get('reason') or '').strip()
                audit_doc['mode'] = (entities.get('mode') or '').strip()
            except Exception:
                pass
            await self.db.log_admin_audit(audit_doc)
        except Exception:
            pass
        # On failure, always show backend error unless it's an unknown action and Claude already spoke.
        if not ok:
            if (str(result_msg or '').lower().startswith('unknown action')) and (assistant_msg or clar_q):
                return True
            await self._log_and_send_response(user_number, result_msg or "Failed.", "admin_action_result")
            return True
        # Success path: avoid duplicate wording if Claude already provided assistantMessage,
        # except for list-type actions where the backend returns the actual data (e.g., bookings).
        t_upper = (action.get('type') or '').upper()
        send_even_if_assistant = t_upper in {'BOOKING_LIST', 'LIST_BOOKINGS'}
        if assistant_msg and not send_even_if_assistant:
            return True
        await self._log_and_send_response(user_number, result_msg or "Done.", "admin_action_result")
        return True

    async def _execute_admin_action(self, actor: str, action: Dict[str, Any], entities: Dict[str, Any]) -> (bool, str):
        t = (action.get('type') or '').upper()
        # Providers: list
        if t == 'PROVIDER_LIST' or t == 'LIST_PROVIDERS':
            status = (entities.get('status') or '').lower() or None
            service = (entities.get('service') or '').lower() or None
            items = await self.db.list_providers(status=status, service_type=service, limit=20)
            if not items:
                return True, "No providers found."
            lines = [f"{str(d.get('_id'))[-6:]} | {d.get('name')} | {d.get('service_type')} | {d.get('status')} | {d.get('whatsapp_number')}" for d in items]
            return True, "Providers:\n" + "\n".join(lines)
        # Users: list
        if t == 'LIST_USERS':
            status = (entities.get('status') or '').lower() or None
            items = await self.db.list_users(status=status, limit=20)
            if not items:
                return True, "No users found."
            def _fmt_date(doc):
                dt = doc.get('registered_at') or doc.get('created_at')
                return str(dt) if dt else ''
            lines = [
                f"{str(d.get('_id'))[-6:]} | {d.get('whatsapp_number') or d.get('phone','')} | {d.get('status','')} | {_fmt_date(d)}"
                for d in items
            ]
            return True, "Users:\n" + "\n".join(lines)
        # Provider lookups
        async def _find_provider(token: str) -> Optional[Dict[str, Any]]:
            if not token:
                return None
            if re.fullmatch(r"[0-9a-f]{24}", token):
                return await self.db.get_provider_by_id(token)
            pn = self._normalize_msisdn(token)
            return await self.db.get_provider_by_phone(pn) if pn else None
        # Approve / Reinstate / Reject / Suspend / Blacklist
        # Guard: Only the designated superadmin can perform role/status changes
        if t in {'PROVIDER_APPROVE','PROVIDER_REINSTATE','PROVIDER_REJECT','PROVIDER_SUSPEND','PROVIDER_BLACKLIST'}:
            try:
                sup = getattr(settings, 'SUPERADMIN_WHATSAPP_NUMBER', '') or ''
                superadmin = self._normalize_msisdn(sup) if sup else ''
            except Exception:
                superadmin = ''
            try:
                actor_norm = self._normalize_msisdn(actor)
            except Exception:
                actor_norm = actor
            if superadmin and actor_norm != superadmin:
                return False, "Only the designated superadmin can change roles."
            token = (entities.get('provider_id') or entities.get('phone') or entities.get('id') or '').strip()
            prov = await _find_provider(token)
            if not prov:
                return False, "Provider not found."
            pid = str(prov.get('_id'))
            if t in {'PROVIDER_APPROVE','PROVIDER_REINSTATE'}:
                ok = await self.db.update_provider_status(pid, 'active')
                if ok:
                    try:
                        await self._log_and_send_response(prov.get('whatsapp_number'), "Your provider account is now active.", "provider_approved")
                    except Exception:
                        pass
                return ok, f"Provider approved: {prov.get('name')}"
            if t == 'PROVIDER_REJECT':
                ok = await self.db.update_provider_status(pid, 'rejected')
                if ok:
                    try:
                        await self._log_and_send_response(prov.get('whatsapp_number'), "Your provider registration was rejected.", "provider_rejected")
                    except Exception:
                        pass
                return ok, f"Provider rejected: {prov.get('name')}"
            if t == 'PROVIDER_SUSPEND':
                ok = await self.db.update_provider_status(pid, 'suspended')
                return ok, f"Provider suspended: {prov.get('name')}"
            if t == 'PROVIDER_BLACKLIST':
                ok = await self.db.update_provider_status(pid, 'blacklisted')
                return ok, f"Provider blacklisted: {prov.get('name')}"

        # Account management (users & providers): suspend/reactivate/delete/view
        if t in {'SUSPEND_ACCOUNT', 'REACTIVATE_ACCOUNT', 'DELETE_ACCOUNT', 'VIEW_ACCOUNT_DETAILS'}:
            # Superadmin gate for destructive changes
            try:
                sup = getattr(settings, 'SUPERADMIN_WHATSAPP_NUMBER', '') or ''
                superadmin = self._normalize_msisdn(sup) if sup else ''
            except Exception:
                superadmin = ''
            try:
                actor_norm = self._normalize_msisdn(actor)
            except Exception:
                actor_norm = actor
            if t in {'SUSPEND_ACCOUNT','REACTIVATE_ACCOUNT','DELETE_ACCOUNT'} and superadmin and actor_norm != superadmin:
                return False, "Only the designated superadmin can change roles."

            target = (entities.get('target') or '').strip().lower()
            ident = (entities.get('identifier') or entities.get('id') or entities.get('phone') or '').strip()
            if not target or not ident:
                return False, "Missing target or identifier."

            # Helpers to locate accounts
            async def _find_user(token: str) -> Optional[Dict[str, Any]]:
                if not token:
                    return None
                if re.fullmatch(r"[0-9a-f]{24}", token):
                    return await self.db.get_user_by_id(token)
                pn = self._normalize_msisdn(token)
                return await self.db.get_user(pn) if pn else None

            now = datetime.utcnow()

            if target == 'provider':
                prov = await _find_provider(ident)
                if not prov:
                    return False, "Provider not found."
                pid = str(prov.get('_id'))
                if t == 'VIEW_ACCOUNT_DETAILS':
                    ver = prov.get('verification') or {}
                    lines = [
                        f"Name: {prov.get('name')}",
                        f"Phone: {prov.get('whatsapp_number')}",
                        f"Service: {prov.get('service_type')}",
                        f"Status: {prov.get('status')}",
                        f"Verified: {ver.get('verified', False)}",
                        f"JobsCompleted: {prov.get('jobsCompleted') or prov.get('jobs_completed') or 0}",
                        f"Rating: {prov.get('rating') or ''}",
                        f"LastActive: {prov.get('lastActiveAt') or prov.get('last_active_at') or ''}",
                    ]
                    return True, "Provider details:\n" + "\n".join(lines)
                if t == 'SUSPEND_ACCOUNT':
                    reason = (entities.get('reason') or '').strip() or None
                    if not reason:
                        return False, "Provide a suspension reason."
                    updates = {
                        'status': 'suspended',
                        'suspendedAt': now,
                        'suspendedBy': actor_norm,
                        'suspensionReason': reason,
                        'updated_at': now,
                    }
                    ok = await self.db.update_provider_fields(pid, updates)
                    return ok, ("Suspended." if ok else "No change.")
                if t == 'REACTIVATE_ACCOUNT':
                    updates = {
                        'status': 'active',
                        'suspendedAt': None,
                        'suspendedBy': None,
                        'suspensionReason': None,
                        'deletedAt': None,
                        'deletedBy': None,
                        'updated_at': now,
                    }
                    ok = await self.db.update_provider_fields(pid, updates)
                    return ok, ("Reactivated." if ok else "No change.")
                if t == 'DELETE_ACCOUNT':
                    mode = (entities.get('mode') or 'soft').lower()
                    if mode == 'hard':
                        ok = await self.db.delete_provider_by_id(pid)
                        return ok, ("Deleted (hard)." if ok else "No change.")
                    updates = {
                        'status': 'deleted',
                        'deletedAt': now,
                        'deletedBy': actor_norm,
                        'updated_at': now,
                    }
                    ok = await self.db.update_provider_fields(pid, updates)
                    return ok, ("Deleted (soft)." if ok else "No change.")

            if target == 'user':
                usr = await _find_user(ident)
                if not usr:
                    return False, "User not found."
                u_phone = usr.get('whatsapp_number') or usr.get('phone')
                if t == 'VIEW_ACCOUNT_DETAILS':
                    lines = [
                        f"Phone: {u_phone}",
                        f"Status: {usr.get('status','')}",
                        f"Created: {usr.get('registered_at') or usr.get('created_at') or ''}",
                        f"Updated: {usr.get('updated_at') or ''}",
                    ]
                    return True, "User details:\n" + "\n".join(lines)
                if t == 'SUSPEND_ACCOUNT':
                    reason = (entities.get('reason') or '').strip() or None
                    if not reason:
                        return False, "Provide a suspension reason."
                    updates = {
                        'status': 'suspended',
                        'suspendedAt': now,
                        'suspendedBy': actor_norm,
                        'suspensionReason': reason,
                        'updated_at': now,
                    }
                    ok = await self.db.update_user(u_phone, updates)
                    return ok, ("Suspended." if ok else "No change.")
                if t == 'REACTIVATE_ACCOUNT':
                    updates = {
                        'status': 'active',
                        'suspendedAt': None,
                        'suspendedBy': None,
                        'suspensionReason': None,
                        'deletedAt': None,
                        'deletedBy': None,
                        'updated_at': now,
                    }
                    ok = await self.db.update_user(u_phone, updates)
                    return ok, ("Reactivated." if ok else "No change.")
                if t == 'DELETE_ACCOUNT':
                    mode = (entities.get('mode') or 'soft').lower()
                    if mode == 'hard':
                        if not u_phone:
                            return False, "Cannot hard-delete: missing phone."
                        ok = await self.db.delete_user_and_data(u_phone)
                        return ok, ("Deleted (hard)." if ok else "No change.")
                    updates = {
                        'status': 'deleted',
                        'deletedAt': now,
                        'deletedBy': actor_norm,
                        'updated_at': now,
                    }
                    ok = await self.db.update_user(u_phone, updates)
                    return ok, ("Deleted (soft)." if ok else "No change.")
        # Bookings: list/info
        if t in {'BOOKING_LIST', 'LIST_BOOKINGS'}:
            window = (entities.get('window') or '').lower()
            now = datetime.utcnow()
            start = None
            if window == 'today':
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif window == 'week':
                start = now - timedelta(days=7)
            items = await self.db.list_bookings(limit=20, start=start, end=None)
            if not items:
                return True, "No bookings found."
            lines = [f"{b.get('booking_id','')} | {b.get('service_type','')} | {b.get('status','')} | {b.get('user_whatsapp_number','')} -> {b.get('provider_whatsapp_number','')}" for b in items]
            return True, "Bookings:\n" + "\n".join(lines)
        if t == 'BOOKING_INFO':
            bid = (entities.get('booking_id') or '').strip()
            b = await self.db.get_booking_by_id(bid)
            if not b:
                return False, "Booking not found."
            return True, f"Booking {b.get('booking_id')}\nService: {b.get('service_type')}\nStatus: {b.get('status')}\nUser: {b.get('user_whatsapp_number')}\nProvider: {b.get('provider_whatsapp_number')}\nTime: {b.get('date_time')}"
        if t in {'BOOKING_CANCEL','BOOKING_COMPLETE'}:
            bid = (entities.get('booking_id') or '').strip()
            if not bid:
                return False, "Missing booking_id."
            if t == 'BOOKING_CANCEL':
                reason = (entities.get('reason') or '').strip()
                ok = await self.db.update_booking_fields(bid, {'status': 'cancelled', 'cancel_reason': reason})
                return ok, "Cancelled." if ok else "No change."
            ok = await self.db.update_booking_fields(bid, {'status': 'completed'})
            return ok, "Completed." if ok else "No change."
        # Assign/Reassign
        if t in {'BOOKING_ASSIGN','BOOKING_REASSIGN'}:
            bid = (entities.get('booking_id') or '').strip()
            token = (entities.get('provider_id') or entities.get('phone') or entities.get('id') or '').strip()
            if not bid or not token:
                return False, "Missing booking_id or provider."
            prov = None
            if re.fullmatch(r"[0-9a-f]{24}", token):
                prov = await self.db.get_provider_by_id(token)
            else:
                pn = self._normalize_msisdn(token)
                prov = await self.db.get_provider_by_phone(pn) if pn else None
            if not prov:
                return False, "Provider not found."
            updates = {
                'provider_id': str(prov.get('_id')),
                'provider_whatsapp_number': prov.get('whatsapp_number'),
                'status': 'assigned'
            }
            ok = await self.db.update_booking_fields(bid, updates)
            return ok, "Assigned." if ok else "No change."
        # Conversations
        if t == 'CONVERSATION_VIEW':
            msisdn = self._normalize_msisdn((entities.get('msisdn') or '').strip())
            if not msisdn:
                return False, "Provide a WhatsApp number."
            msgs = await self.db.get_conversation_history(msisdn, limit=10)
            if not msgs:
                return True, "No recent messages."
            lines = [f"{m['role']}: {m['text'][:120]}" for m in msgs]
            return True, "Conversation:\n" + "\n".join(lines)
        if t == 'CONVERSATION_RESET':
            msisdn = self._normalize_msisdn((entities.get('msisdn') or '').strip())
            if not msisdn:
                return False, "Provide a WhatsApp number."
            await self.db.delete_session(msisdn)
            await self.db.delete_conversation_history(msisdn)
            return True, "Conversation reset."
        # Stats
        if t == 'STATS':
            now = datetime.utcnow()
            window = (entities.get('window') or '').lower()
            win = None
            if window == 'today':
                win = (now.replace(hour=0, minute=0, second=0, microsecond=0), None)
            elif window == 'week':
                win = (now - timedelta(days=7), None)
            b_total = await self.db.count_bookings(*(win or (None, None)))
            b_completed = await self.db.count_bookings_by_status('completed', *(win or (None, None)))
            prov_active = await self.db.count_providers('active')
            users = await self.db.count_users(*(win or (None, None)))
            return True, f"Stats:\nBookings: {b_total}\nCompleted: {b_completed}\nActive providers: {prov_active}\nNew users: {users}"
        # AI controls
        if t == 'AI_STATUS':
            return True, f"AI: {'paused' if getattr(self, 'ai_paused', False) else 'active'}"
        if t == 'AI_PAUSE':
            self.ai_paused = True
            return True, "AI paused."
        if t == 'AI_RESUME':
            self.ai_paused = False
            return True, "AI resumed."
        # Block user
        if t == 'USER_BLOCK':
            msisdn = self._normalize_msisdn((entities.get('msisdn') or '').strip())
            if not msisdn:
                return False, "Provide a WhatsApp number."
            await self.db.update_user(msisdn, {'opted_out': True, 'consent_transactional': False})
            return True, "User blocked."
        return False, "Unknown action."

    async def _list_providers_for_selection(self, user_number: str, service_type: str, raw_location: str, session: Dict, user: Dict) -> None:
        """Helper to fetch and display a list of providers for selection."""
        try:
            # Normalize to our known service areas
            from app.utils.location_extractor import get_location_extractor
            location_extractor = get_location_extractor()
            norm_location = location_extractor.normalize_user_location(raw_location) if raw_location else None

            providers: List[Dict[str, Any]] = []
            if service_type:
                if norm_location:
                    providers = await self.db.get_providers_by_service(service_type, norm_location)
                else:
                    providers = await self.db.get_providers_by_service(service_type)

            if not providers and service_type:
                all_for_service = await self.db.get_providers_by_service(service_type)
                if norm_location:
                    providers = location_extractor.filter_providers_by_location(all_for_service, norm_location)
                else:
                    providers = all_for_service

            if not providers:
                await self._log_and_send_response(
                    user_number,
                    self._short(
                        f"Sorry, no {service_type or 'provider'}s available right now.",
                        "Sorry, no providers available right now."
                    ),
                    "ai_no_providers_for_confirm",
                )
                return

            session.setdefault("data", {})
            session["data"]["service_type"] = service_type
            session["data"]["providers"] = providers
            if norm_location:
                session["data"]["location"] = norm_location

            buttons: List[Dict[str, Any]] = []
            for p in providers[:3]:
                buttons.append({
                    "id": f"provider_{p.get('whatsapp_number') or p.get('_id')}",
                    "title": f"{p.get('name') or 'Provider'}",
                })

            header_loc = norm_location or (user or {}).get("location") or "your area"
            await self._log_and_send_interactive(
                user_number,
                f"Available {service_type}s in {header_loc}",
                self._build_friendly_provider_body(service_type or 'provider', header_loc, len(providers), session),
                buttons,
                self._friendly_footer(),
            )

            session["state"] = ConversationState.PROVIDER_SELECTION
        except Exception as e:
            logger.error(f"Error while listing providers: {e}")
            await self._log_and_send_response(user_number, "Sorry, I couldn't find providers right now. Please try again.", "provider_list_error")

    async def handle_cancel_existing_booking_confirm(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handles user decision on cancelling an existing booking to create a new one."""
        text = message_text.strip().lower()

        if text in ['yes', 'y', 'ok', 'confirm']:
            conflicting_booking_id = session.get("data", {}).get("_conflicting_booking_id")
            pending_request = session.get("data", {}).get("_pending_booking_request")

            if conflicting_booking_id:
                await self.db.update_booking_status(conflicting_booking_id, "cancelled")
                await self._log_and_send_response(user_number, "Your previous booking has been cancelled.", "booking_cancelled")

            if session.get("data"):
                session["data"].pop("_conflicting_booking_id", None)
                session["data"].pop("_pending_booking_request", None)

            if pending_request:
                await self._list_providers_for_selection(
                    user_number,
                    pending_request.get("service_type"),
                    pending_request.get("location"),
                    session,
                    user
                )
            else:
                session['state'] = ConversationState.SERVICE_SEARCH
                await self._log_and_send_response(user_number, "Please tell me what service you are looking for.", "service_search_prompt")

        elif text in ['no', 'n', 'cancel']:
            if session.get("data"):
                session["data"].pop("_conflicting_booking_id", None)
                session["data"].pop("_pending_booking_request", None)
            session['state'] = ConversationState.SERVICE_SEARCH
            await self._log_and_send_response(user_number, "Okay, I've kept your existing booking. What else can I help you with?", "booking_kept")
        else:
            await self._log_and_send_response(user_number, "Please reply with 'yes' to cancel the old booking and create a new one, or 'no' to keep your existing booking.", "clarification_prompt")

    async def handle_ai_response(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        if getattr(self, 'ai_paused', False):
            await self._log_and_send_response(user_number, "AI is currently paused. Please try again later or use HELP.", "ai_paused")
            return
        sp_override = None
        prompt_version = None
        try:
            actor = self._normalize_msisdn(user_number)
            is_admin = actor in set(self._admin_numbers())
        except Exception:
            is_admin = False
        # Fetch provider status once
        try:
            prov = await self.db.get_provider_by_whatsapp(user_number)
        except Exception:
            prov = None
        mode_override = str((session.get('mode_override') or '')).lower()
        if mode_override == 'admin' and is_admin:
            sp_override = getattr(settings, 'HUSTLR_ADMIN_PROMPT_V1', None)
            prompt_version = 'hustlr_admin_prompt_v1'
        elif mode_override == 'provider' and prov and str(prov.get('status', '')).lower() == 'active':
            sp_override = getattr(settings, 'HUSTLR_PROVIDER_PROMPT_V1', None)
            prompt_version = 'hustlr_provider_prompt_v1'
        elif mode_override == 'user':
            sp_override = None
            prompt_version = 'hustlr_client_prompt_v1'
        else:
            if is_admin:
                sp_override = getattr(settings, 'HUSTLR_ADMIN_PROMPT_V1', None)
                prompt_version = 'hustlr_admin_prompt_v1'
            elif prov and str(prov.get('status', '')).lower() == 'active':
                sp_override = getattr(settings, 'HUSTLR_PROVIDER_PROMPT_V1', None)
                prompt_version = 'hustlr_provider_prompt_v1'
            else:
                sp_override = None
                prompt_version = 'hustlr_client_prompt_v1'

        # --- Pre-computation: Check service availability before calling AI ---
        precomputed_service_type = self.extract_service_type(message_text)
        service_available = None
        if precomputed_service_type:
            # Check if we have any providers for this service
            providers = await self.db.get_providers_by_service(precomputed_service_type)
            service_available = bool(providers)

        user_context: Dict[str, Any] = {
            "user_name": (user or {}).get("name"),
            "user_location": (user or {}).get("location"),
            "session_state": str(session.get("state")),
            "known_fields": (session.get("data") or {}),
            "system_prompt_override": sp_override,
            "prompt_version": prompt_version,
        }

        # Add pre-computed context for the AI
        if service_available is not None:
            user_context['service_availability'] = {
                "service_type": precomputed_service_type,
                "available": service_available
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
                service_type = (data.get("service_type") or (session.get("data") or {}).get("service_type") or "").strip().lower()
                raw_location = (data.get("location") or (session.get("data") or {}).get("location") or (user or {}).get("location") or "").strip()

                # Check for existing active bookings for a similar service type
                active_bookings = await self.db.get_active_bookings_for_user(user_number)
                if active_bookings:
                    synonyms_map = {
                        "website": ["web", "developer", "engineer", "frontend", "fullstack", "wordpress", "shopify", "wix", "site"],
                        "web": ["website", "developer", "frontend", "fullstack"],
                        "developer": ["engineer", "programmer", "software"],
                        "software": ["developer", "engineer", "fullstack"],
                        "app": ["mobile", "android", "ios", "flutter", "react", "native"],
                        "fitness": ["gym", "trainer", "personal"],
                        "gym": ["fitness", "trainer"],
                        "trainer": ["fitness", "gym"],
                        "plumber": ["plumbing"],
                        "electrician": ["electrical", "electricity"],
                        "cleaner": ["cleaning"],
                    }
                    new_service_tokens = set(re.findall(r"[a-z0-9]+", service_type.lower()))
                    for token in list(new_service_tokens):
                        new_service_tokens.update(synonyms_map.get(token, []))

                    for booking in active_bookings:
                        existing_service_type = (booking.get("service_type") or "").lower()
                        existing_service_tokens = set(re.findall(r"[a-z0-9]+", existing_service_type))
                        for token in list(existing_service_tokens):
                            existing_service_tokens.update(synonyms_map.get(token, []))
                        
                        if new_service_tokens.intersection(existing_service_tokens):
                            session["data"]["_pending_booking_request"] = {
                                "service_type": service_type,
                                "location": raw_location,
                            }
                            session["data"]["_conflicting_booking_id"] = booking.get("booking_id")
                            session["state"] = ConversationState.CANCEL_EXISTING_BOOKING_CONFIRM
                            await self._log_and_send_response(
                                user_number,
                                f"You already have an active booking for a similar service ({existing_service_type}). Do you want to cancel the existing booking and create a new one? (yes/no)",
                                "cancel_existing_booking_prompt"
                            )
                            return

                await self._list_providers_for_selection(user_number, service_type, raw_location, session, user)

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

            # Create a booking when Claude completes the booking contract
            if status == "COMPLETE" and field == "booking":
                try:
                    # Compose a date_time string
                    date_time = (
                        (data or {}).get("date_time")
                        or (data or {}).get("scheduled_time")
                        or (f"{(data or {}).get('date')} {(data or {}).get('time')}" if ((data or {}).get('date') and (data or {}).get('time')) else None)
                    )
                    # Resolve provider WhatsApp number if an id is present
                    prov_id = (data or {}).get("provider_id") or (data or {}).get("provider") or (session.get("data") or {}).get("selected_provider", {}).get("_id")
                    provider_phone = None
                    if prov_id:
                        provider = await self.db.get_provider(prov_id)
                        if provider:
                            provider_phone = provider.get("whatsapp_number")

                    booking_doc = {
                        "service_type": (data or {}).get("service_type"),
                        "customer_whatsapp_number": user_number,
                        "provider_whatsapp_number": provider_phone,
                        "booking_time": date_time,
                        "status": "pending",
                        "problem_description": (data or {}).get("problem_description"),
                    }

                    # Add optional fields
                    cust_phone = (data or {}).get('customer_phone')
                    if cust_phone:
                        booking_doc['customer_phone'] = cust_phone
                    cust_name = (data or {}).get('customer_name')
                    if cust_name:
                        booking_doc['customer_name'] = cust_name
                    loc = (data or {}).get('location') or (session.get('data') or {}).get('location') or (user or {}).get('location')
                    if loc:
                        booking_doc['location'] = loc

                    booking_doc = await self.db.create_booking(booking_doc)
                    logger.info(f"Booking created from AI response: {booking_doc.get('_id')}")
                    # Send confirmation to user
                    await self._log_and_send_response(
                        user_number,
                        f"Booking confirmed! Details sent to your WhatsApp.",
                        "ai_booking_confirmation"
                    )
                except Exception as e:
                    logger.error(f"Failed to create booking from AI response: {e}")

                # Clear session data after booking is complete
                if session.get("data"):
                    keys_to_clear = [
                        '_pending_booking', 'selected_provider_index', '_bookings_list',
                        '_cancel_booking_id', '_reschedule_booking_id', '_reschedule_new_time',
                        'service_type', 'providers', 'selected_provider', 'selected_providers',
                        'booking_time', 'location', 'issue', 'problem_description', 'date', 'time'
                    ]
                    for k in keys_to_clear:
                        session["data"].pop(k, None)
                session["state"] = ConversationState.SERVICE_SEARCH
                return

    def extract_service_type(self, message_text: str) -> Optional[str]:
        """Extract service type from message text using keyword matching."""
        message_text = message_text.lower()
        # Expanded service map
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
            'landscaping': 'gardener',
            'builder': 'builder',
            'building': 'builder',
            'construction': 'builder',
            'welder': 'welder',
            'welding': 'welder',
            'driver': 'driver',
            'driving': 'driver',
            'transport': 'driver',
            'tiler': 'tiler',
            'tiling': 'tiler',
            'technician': 'technician',
            'gadget': 'technician',
            'appliance': 'technician',
            'dstv': 'technician',
            'cctv': 'technician',
            'solar': 'technician',
            'inverter': 'technician',
            'fridge': 'technician',
            'refridgeration': 'technician',
            'aircon': 'technician',
            'air con': 'technician',
            'air-con': 'technician',
            'airconditioner': 'technician',
            'air conditioner': 'technician',
            'air-conditioner': 'technician',
            'laundry': 'laundry',
            'washing': 'laundry',
            'ironing': 'laundry',
            'gas': 'gas',
            'refill': 'gas',
            'tutor': 'tutor',
            'tution': 'tutor',
            'lessons': 'tutor',
            'extra lessons': 'tutor',
            'catering': 'catering',
            'caterer': 'catering',
            'food': 'catering',
            'events': 'catering',
            'chef': 'catering',
            'cook': 'catering',
            'photography': 'photography',
            'photographer': 'photography',
            'photos': 'photography',
            'videos': 'photography',
            'video': 'photography',
            'videographer': 'photography',
            'designer': 'designer',
            'graphic': 'designer',
            'web': 'designer',
            'developer': 'designer',
            'tailor': 'tailor',
            'dressmaker': 'tailor',
            'sewing': 'tailor',
            'alterations': 'tailor',
            'beautician': 'beautician',
            'beauty': 'beautician',
            'nails': 'beautician',
            'manicure': 'beautician',
            'pedicure': 'beautician',
            'makeup': 'beautician',
            'artist': 'beautician',
            'hair': 'beautician',
            'stylist': 'beautician',
            'barber': 'beautician',
            'salon': 'beautician',
            'massage': 'massage',
            'therapist': 'massage',
            'therapy': 'massage',
            'fitness': 'fitness',
            'trainer': 'fitness',
            'gym': 'fitness',
            'coach': 'fitness',
            'health': 'fitness',
            'nutritionist': 'fitness',
            'dietician': 'fitness',
            'accountant': 'accountant',
            'accounting': 'accountant',
            'bookkeeper': 'accountant',
            'tax': 'accountant',
            'consultant': 'consultant',
            'business': 'consultant',
            'strategy': 'consultant',
            'marketing': 'consultant',
            'legal': 'legal',
            'lawyer': 'legal',
            'attorney': 'legal',
            'paralegal': 'legal',
            'security': 'security',
            'guard': 'security',
            'bouncer': 'security',
            'alarm': 'security',
            'fumigation': 'fumigation',
            'pest': 'fumigation',
            'control': 'fumigation',
            'fumigator': 'fumigation',
            'interior': 'interior',
            'decorator': 'interior',
            'design': 'interior',
            'furniture': 'interior',
            'upholstery': 'interior',
            'courier': 'courier',
            'delivery': 'courier',
            'errands': 'courier',
            'logistics': 'courier',
            'car': 'car',
            'rental': 'car',
            'hire': 'car',
            'vehicle': 'car',
            'event': 'event',
            'planner': 'event',
            'planning': 'event',
            'mc': 'event',
            'dj': 'event',
            'sound': 'event',
            'hire': 'event',
            'real': 'real',
            'estate': 'real',
            'agent': 'real',
            'property': 'real',
            'realtor': 'real',
            ' borehole': 'borehole',
            'drilling': 'borehole',
            'water': 'borehole',
            'survey': 'borehole',
            'casing': 'borehole',
            'installation': 'borehole',
            'pump': 'borehole',
            'tank': 'borehole',
            'stand': 'borehole',
            'recruiter': 'recruiter',
            'recruitment': 'recruiter',
            'hiring': 'recruiter',
            'staffing': 'recruiter',
        }

        for keyword, service in services.items():
            if keyword in message_text:
                return service
        
        return None

    def _parse_relative_time(self, time_str: str) -> Optional[datetime]:
        """Parse a relative time string into a datetime object."""
        now = datetime.now()
        t_raw = (time_str or '').strip().lower()
        t = ' ' + t_raw + ' '

        # "in 5 minutes", "in 2 hours", "in 3 days", "in 1 week"
        m = re.search(r"\s(in|for)\s+(\d+)\s+(minute|hour|day|week)s?(\s|$)", t)
        if m:
            n = int(m.group(2))
            unit = m.group(3)
            if unit == 'minute':
                return now + timedelta(minutes=n)
            if unit == 'hour':
                return now + timedelta(hours=n)
            if unit == 'day':
                return now + timedelta(days=n)
            if unit == 'week':
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

    def _canonicalize_booking_time(self, time_str: str) -> Optional[datetime]:
        """Parse a human-readable time string into a canonical datetime object."""
        return self._parse_relative_time(time_str)

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

    def _generate_booking_id(self) -> str:
        """Generate a compact, unique-ish booking id without extra imports."""
        try:
            # Example: B20251218T112233123456 (UTC timestamp-based)
            return 'B' + datetime.utcnow().strftime('%Y%m%d%H%M%S%f')
        except Exception:
            # Last resort
            return f"B{int(datetime.utcnow().timestamp()*1000)}"

    async def _ai_action_create_booking(self, user_number: str, payload: Dict, session: Dict, user: Dict) -> None:
        """Handle booking creation from a structured AI payload."""
        s_type = payload.get('service_type')
        prov_idx = payload.get('provider_index')
        time_text = payload.get('time_text')
        issue = payload.get('issue')

        if not s_type or not prov_idx or not time_text:
            await self._log_and_send_response(user_number, "I'm missing some details to make the booking. Could you please clarify the service, provider, and time?", "booking_payload_incomplete")
            return

        providers = (session.get('data') or {}).get('providers') or []
        if not (isinstance(prov_idx, int) and 1 <= prov_idx <= len(providers)):
            await self._log_and_send_response(user_number, "That's not a valid provider selection. Please choose a number from the list.", "booking_provider_index_invalid")
            return

        provider = providers[prov_idx - 1]
        booking_time_dt = self._canonicalize_booking_time(time_text)
        if not booking_time_dt:
            await self._log_and_send_response(user_number, "I couldn't understand that time. Please try something like 'tomorrow at 10am' or 'Dec 20 14:30'.", "booking_time_invalid")
            return

        booking_time = booking_time_dt.strftime('%Y-%m-%d %H:%M')
        location = (session.get('data') or {}).get('location') or (user or {}).get('location') or ''

        # Final check: does this provider offer this service?
        provider_service = (provider.get('service_type') or '').lower()
        request_service = (s_type or '').lower()
        # Basic check, can be improved with synonyms
        assert request_service in provider_service or provider_service in request_service, f"Provider {provider.get('_id')} does not offer {request_service}"

        booking_doc = {
            'booking_id': self._generate_booking_id(),
            'customer_whatsapp_number': user_number,
            'provider_whatsapp_number': provider.get('whatsapp_number'),
            'service_type': s_type,
            'booking_time': booking_time,
            'location': location,
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat(),
            'problem_description': issue or (session.get('data') or {}).get('issue') or ''
        }

        await self.db.create_booking(booking_doc)
        await self._notify_booking_other_party(user_number, booking_doc['booking_id'], 'new')

        msg = (
            f"Booking confirmed!\n"
            f"{provider['name']} will assist you with {s_type.title()}.\n"
            f"Location: {location}\n"
            f"Date: {booking_time}"
        )
        await self._log_and_send_response(user_number, msg, "booking_creation_confirmed")

        # Hard reset of session data after booking to prevent contamination
        session['state'] = ConversationState.SERVICE_SEARCH
        session['data'] = {}

            return None

        # Direct number match (e.g., '1', '2')
        if text.isdigit():
            idx = int(text)
            if 1 <= idx <= len(providers):
                return idx

        # Ordinal words ('first', 'second', 'the one', etc.)
        ordinals = {
            'first': 1, '1st': 1, 'one': 1,
            'second': 2, '2nd': 2, 'two': 2,
            'third': 3, '3rd': 3, 'three': 3,
            'fourth': 4, '4th': 4, 'four': 4,
            'fifth': 5, '5th': 5, 'five': 5,
        }
        for word, idx in ordinals.items():
            if word in text:
                if 1 <= idx <= len(providers):
                    return idx

        # Match by provider name
        for i, p in enumerate(providers, start=1):
            name = (p.get('name') or '').lower()
            if name and name in text:
                return i

        return None

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

    async def handle_booking_resume_decision(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        text = (message_text or '').strip().lower()
        yes_vals = {'yes', 'y', 'resume', 'continue', 'ok', 'okay', 'sure'}
        no_vals = {'no', 'n', 'new', 'start new', 'start over', 'cancel', 'stop'}

        if text in yes_vals:
            prev_state_val = (session.get('data') or {}).get('previous_state')
            if prev_state_val:
                try:
                    session['state'] = ConversationState(prev_state_val)
                except Exception:
                    session['state'] = ConversationState.SERVICE_SEARCH
            else:
                session['state'] = ConversationState.SERVICE_SEARCH

            await self._log_and_send_response(
                user_number,
                self._short(
                    "Okay, resuming your previous booking. Please continue where we left off.",
                    "Resuming your booking."
                ),
                "booking_resume_yes",
            )
            session.setdefault('data', {}).pop('previous_state', None)
            return

        if text in no_vals:
            session['state'] = ConversationState.SERVICE_SEARCH
            try:
                sd = session.setdefault('data', {})
                for k in [
                    'service_type', 'providers', 'selected_provider', 'selected_provider_index',
                    'booking_time', '_pending_booking', 'all_providers', 'location',
                    '_bookings_list', '_cancel_booking_id', '_reschedule_booking_id', '_reschedule_new_time',
                    'issue', 'previous_state'
                ]:
                    sd.pop(k, None)
            except Exception:
                pass
            await self._log_and_send_response(
                user_number,
                self._short("No problem. Let's start fresh. What service do you need?", "Starting new. What service?"),
                "booking_resume_no",
            )
            return

        await self._log_and_send_response(
            user_number,
            "Please reply 'yes' to continue your previous booking or 'no' to start a new one.",
            "booking_resume_prompt_repeat",
        )

    async def handle_cancel_booking_select(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        items = session.get('data', {}).get('_bookings_list') or []
        selected = None
        # Accept number anywhere in text
        num_match = re.search(r"\b(\d+)\b", str(message_text))
        if num_match:
            i = int(num_match.group(1))
            if 1 <= i <= len(items):
                selected = items[i-1]
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
