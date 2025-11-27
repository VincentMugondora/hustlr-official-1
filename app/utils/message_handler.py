from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
import re

class ConversationState(Enum):
    NEW = "new"
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_LOCATION = "onboarding_location"
    ONBOARDING_PRIVACY = "onboarding_privacy"
    SERVICE_SEARCH = "service_search"
    PROVIDER_SELECTION = "provider_selection"
    BOOKING_TIME = "booking_time"
    BOOKING_CONFIRM = "booking_confirm"
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
            # Start onboarding
            await self.whatsapp_api.send_text_message(
                user_number,
                "👋 Welcome to Hustlr! I'll help you find local service providers.\n\n"
                "Let's get you set up. What's your name?"
            )
            session['state'] = ConversationState.ONBOARDING_NAME
        
        elif state == ConversationState.ONBOARDING_NAME:
            # Collect name
            name = message_text.title()
            session['data']['name'] = name
            
            await self.whatsapp_api.send_text_message(
                user_number,
                f"Nice to meet you, {name}! 📍\n\n"
                "What's your location or neighborhood? This helps me find providers near you."
            )
            session['state'] = ConversationState.ONBOARDING_LOCATION
        
        elif state == ConversationState.ONBOARDING_LOCATION:
            # Collect location
            location = message_text.title()
            session['data']['location'] = location
            
            # Present privacy policy
            privacy_text = (
                "🔒 **Privacy Policy Summary:**\n\n"
                "• We store your name, location, and booking history\n"
                "• We share your info with service providers you choose\n"
                "• We never sell your data to third parties\n"
                "• You can request data deletion anytime\n\n"
                "Do you agree to this privacy policy? (Yes/No)"
            )
            
            await self.whatsapp_api.send_text_message(user_number, privacy_text)
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
                    await self.whatsapp_api.send_text_message(
                        user_number,
                        f"✅ Perfect! You're all set up, {session['data']['name']}!\n\n"
                        "Now you can:\n"
                        "• Search for service providers\n"
                        "• Book appointments\n"
                        "• Get reminders\n\n"
                        "What service are you looking for today?"
                    )
                    session['state'] = ConversationState.SERVICE_SEARCH
                else:
                    await self.whatsapp_api.send_text_message(
                        user_number,
                        "❌ Sorry, there was an issue setting up your account. Please try again later."
                    )
            else:
                await self.whatsapp_api.send_text_message(
                    user_number,
                    "❌ You need to agree to the privacy policy to use Hustlr.\n\n"
                    "Type 'yes' to agree, or 'no' to decline."
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
        
        # Handle service search
        if state == ConversationState.SERVICE_SEARCH:
            await self.handle_service_search(user_number, message_text, session, user)
        elif state == ConversationState.PROVIDER_SELECTION:
            await self.handle_provider_selection(user_number, message_text, session, user)
        elif state == ConversationState.BOOKING_TIME:
            await self.handle_booking_time(user_number, message_text, session, user)
        else:
            # Use AI to understand the message
            await self.handle_ai_response(user_number, message_text, user)
    
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
            await self.whatsapp_api.send_text_message(user_number, ai_response)
            return
        
        # Search for providers
        user_location = user.get('location', '')
        providers = await self.db.get_providers_by_service(service_type, user_location)
        
        if not providers:
            await self.whatsapp_api.send_text_message(
                user_number,
                f"❌ No {service_type} found in your area. Try:\n"
                "• Expanding your search area\n"
                "• Trying a different service type\n"
                "• Type 'help' for more options"
            )
            return
        
        # Present providers as interactive buttons
        buttons = []
        for i, provider in enumerate(providers[:3]):  # Limit to 3 providers
            buttons.append({
                'id': f"provider_{provider['whatsapp_number']}",
                'title': f"{provider['name']} - {provider.get('location', 'Unknown')}"
            })
        
        await self.whatsapp_api.send_interactive_buttons(
            user_number,
            f"🔧 {service_type.title()} Providers",
            f"Found {len(providers)} provider(s) near {user_location}. Select one to book:",
            buttons,
            "Reply with the provider number or choose from options above"
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
            await self.whatsapp_api.send_text_message(
                user_number,
                "❌ Please select a valid provider number from the list above."
            )
            return
        
        # Ask for booking time
        session['data']['selected_provider'] = selected_provider
        session['state'] = ConversationState.BOOKING_TIME
        
        await self.whatsapp_api.send_text_message(
            user_number,
            f"✅ Selected: {selected_provider['name']}\n\n"
            f"When would you like to book this service? Please provide:\n"
            f"• Date (e.g., 'tomorrow', 'Dec 15', 'next Monday')\n"
            f"• Time (e.g., '2pm', 'morning', 'after 5pm')"
        )
    
    async def handle_booking_time(self, user_number: str, message_text: str, session: Dict, user: Dict) -> None:
        """Handle booking time confirmation"""
        # Parse date/time (simplified - you'd want more robust parsing)
        booking_time = self.parse_datetime(message_text)
        
        if not booking_time:
            await self.whatsapp_api.send_text_message(
                user_number,
                "❌ I couldn't understand that time format. Please try:\n"
                "• 'tomorrow at 2pm'\n"
                "• 'Dec 15 morning'\n"
                "• 'next Monday afternoon'"
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
            await self.whatsapp_api.send_text_message(
                user_number,
                f"✅ **Booking Confirmed!**\n\n"
                f"📅 Service: {session['data']['service_type'].title()}\n"
                f"👨‍🔧 Provider: {provider_name}\n"
                f"⏰ Time: {booking_time}\n\n"
                f"You'll receive a reminder before your appointment."
            )
            
            # Reset session
            session['state'] = ConversationState.SERVICE_SEARCH
            session['data'] = {}
        else:
            await self.whatsapp_api.send_text_message(
                user_number,
                "❌ Sorry, there was an issue creating your booking. Please try again."
            )
    
    async def handle_provider_registration(self, user_number: str, message_text: str, session: Dict) -> None:
        """Handle service provider registration"""
        state = session['state']
        
        if state == ConversationState.PROVIDER_REGISTER:
            await self.whatsapp_api.send_text_message(
                user_number,
                "👨‍🔧 **Provider Registration**\n\n"
                "Let's get you registered as a service provider.\n\n"
                "What's your full name?"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_NAME
        
        elif state == ConversationState.PROVIDER_REGISTER_NAME:
            session['data']['name'] = message_text.title()
            await self.whatsapp_api.send_text_message(
                user_number,
                f"Great, {session['data']['name']}! 🛠️\n\n"
                "What service do you provide? (e.g., plumber, electrician, carpenter, etc.)"
            )
            session['state'] = ConversationState.PROVIDER_REGISTER_SERVICE
        
        elif state == ConversationState.PROVIDER_REGISTER_SERVICE:
            service_type = message_text.lower()
            session['data']['service_type'] = service_type
            
            await self.whatsapp_api.send_text_message(
                user_number,
                f"Perfect! 📍\n\n"
                "What area or neighborhood do you serve?"
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
                await self.whatsapp_api.send_text_message(
                    user_number,
                    f"✅ **Registration Submitted!**\n\n"
                    f"👨‍🔧 Name: {session['data']['name']}\n"
                    f"🛠️ Service: {session['data']['service_type']}\n"
                    f"📍 Area: {message_text.title()}\n\n"
                    f"Your registration is pending review. We'll notify you once approved!\n\n"
                    f"You can start receiving bookings once approved."
                )
                
                # Reset session
                session['state'] = ConversationState.SERVICE_SEARCH
                session['data'] = {}
            else:
                await self.whatsapp_api.send_text_message(
                    user_number,
                    "❌ Sorry, there was an issue with your registration. Please try again."
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
        
        await self.whatsapp_api.send_text_message(user_number, ai_response)
    
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
        
        await self.whatsapp_api.send_text_message(user_number, help_text)
    
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
