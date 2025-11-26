import os
import httpx
from typing import Dict, Any
from app.models.message import WhatsAppMessage

class WhatsAppService:
    """Service for sending messages via WhatsApp Cloud API"""
    
    def __init__(self):
        self.api_url = os.getenv('WHATSAPP_API_URL')
        self.access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
    
    async def send_message(self, to_number: str, message_text: str) -> bool:
        """
        Send a text message via WhatsApp Cloud API
        
        Args:
            to_number: Recipient's WhatsApp number
            message_text: Message content
            
        Returns:
            True if successful, False otherwise
        """
        if not all([self.api_url, self.access_token, self.phone_number_id]):
            print("WhatsApp API configuration missing")
            return False
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'text',
            'text': {
                'body': message_text
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    print(f"Message sent successfully to {to_number}")
                    return True
                else:
                    print(f"Failed to send message: {response.status_code} - {response.text}")
                    return False
                    
        except httpx.RequestError as e:
            print(f"HTTP request error: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error sending message: {e}")
            return False
    
    async def send_interactive_message(self, to_number: str, header_text: str, body_text: str, options: list) -> bool:
        """
        Send an interactive message with buttons
        
        Args:
            to_number: Recipient's WhatsApp number
            header_text: Message header
            body_text: Message body
            options: List of button options [{"id": "opt1", "title": "Option 1"}, ...]
            
        Returns:
            True if successful, False otherwise
        """
        if not all([self.api_url, self.access_token, self.phone_number_id]):
            return False
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'interactive',
            'interactive': {
                'type': 'button',
                'header': {
                    'type': 'text',
                    'text': header_text
                },
                'body': {
                    'text': body_text
                },
                'action': {
                    'buttons': [
                        {
                            'type': 'reply',
                            'reply': {
                                'id': opt['id'],
                                'title': opt['title']
                            }
                        } for opt in options[:3]  # WhatsApp limits to 3 buttons
                    ]
                }
            }
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=10.0
                )
                
                return response.status_code == 200
                
        except Exception as e:
            print(f"Error sending interactive message: {e}")
            return False
