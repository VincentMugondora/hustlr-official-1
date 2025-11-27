import json
import boto3
from typing import Dict, Any

def lambda_handler(event, context):
    """
    AWS Lambda function for AI-powered question answering
    Handles user questions and provides contextual responses for the Hustlr chatbot
    """
    
    # Parse input
    try:
        body = json.loads(event.get('body', '{}')) if isinstance(event.get('body'), str) else event.get('body', {})
        user_message = body.get('user_message', '').lower().strip()
        user_context = body.get('user_context', {})
    except Exception as e:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid input format'})
        }
    
    # Generate response based on message content
    response = generate_response(user_message, user_context)
    
    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json'
        },
        'body': json.dumps({'response': response})
    }

def generate_response(message: str, context: Dict[str, Any]) -> str:
    """
    Generate contextual responses based on user message and context
    """
    
    # Greeting responses
    if any(greeting in message for greeting in ['hello', 'hi', 'hey', 'good morning', 'good afternoon']):
        if context.get('name'):
            return f"Hello {context['name']}! I'm your Hustlr assistant. How can I help you find a service provider today?"
        return "Hello! I'm your Hustlr assistant. I can help you find local service providers like plumbers, electricians, and more. What service are you looking for?"
    
    # Service search responses
    if any(service in message for service in ['plumber', 'plumbing']):
        return "I can help you find a plumber! Do you have a specific plumbing issue (leak, drain, installation) and what's your location?"
    
    if any(service in message for service in ['electrician', 'electrical', 'electricity']):
        return "I can connect you with electricians for wiring, repairs, or installations. What electrical work do you need and where are you located?"
    
    if any(service in message for service in ['carpenter', 'carpentry', 'wood']):
        return "I can find carpenters for furniture, repairs, or custom work. What carpentry service do you need?"
    
    # Booking related responses
    if any(keyword in message for keyword in ['book', 'booking', 'appointment', 'schedule']):
        return "To make a booking, I'll need to know: 1) What service you need, 2) Your location, and 3) Preferred date/time. Can you provide these details?"
    
    # Provider registration
    if any(keyword in message for keyword in ['register', 'become provider', 'join']):
        return "Great! To register as a service provider, please provide: 1) Your full name, 2) Service type (plumber, electrician, etc.), and 3) Your service area/location."
    
    # Help and information
    if any(keyword in message for keyword in ['help', 'what can you do', 'services', 'how']):
        return """I can help you with:
🔧 Find local service providers (plumbers, electricians, carpenters, etc.)
📅 Schedule appointments and bookings
⏰ Set up reminders for your appointments
👥 Register as a service provider

Just tell me what you're looking for!"""
    
    # Location-based queries
    if any(keyword in message for keyword in ['location', 'area', 'near me', 'where']):
        return "I can find providers in your area! What service do you need and what's your location or neighborhood?"
    
    # Pricing inquiries
    if any(keyword in message for keyword in ['price', 'cost', 'how much', 'charge']):
        return "Pricing varies by provider and service type. Once you tell me what you need and your location, I can connect you with providers who can give you specific quotes."
    
    # Emergency situations
    if any(keyword in message for keyword in ['emergency', 'urgent', 'asap', 'immediately']):
        return "For emergency services, I'll prioritize finding available providers quickly. What's the emergency and your location? I'll connect you with the nearest available provider."
    
    # Default/fallback response
    if len(message) < 3:
        return "I'm not sure I understand. Could you tell me more about what you need help with?"
    
    # Attempt to understand the request
    if 'find' in message or 'need' in message or 'looking for' in message:
        return "I can help you find what you need! Could you be more specific about the service type and your location?"
    
    return "I'm here to help you find local service providers. Try telling me what service you need (like 'plumber', 'electrician') and your location, or say 'help' to see what I can do!"
