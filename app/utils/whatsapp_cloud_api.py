import os
import httpx
import json
from typing import Dict, Any, List, Optional, Union
from enum import Enum
import base64
import mimetypes
from app.models.message import WhatsAppMessage

class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"
    LOCATION = "location"
    CONTACT = "contact"
    INTERACTIVE = "interactive"
    TEMPLATE = "template"

class WhatsAppCloudAPI:
    """Enhanced WhatsApp Cloud API service with advanced features"""
    
    def __init__(self):
        self.api_url = os.getenv('WHATSAPP_API_URL')
        self.access_token = os.getenv('WHATSAPP_ACCESS_TOKEN')
        self.phone_number_id = os.getenv('WHATSAPP_PHONE_NUMBER_ID')
        self.version = "v18.0"
        
        # Base headers for API requests
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json'
        }
    
    async def send_text_message(self, to_number: str, message: str, preview_url: bool = False) -> Dict[str, Any]:
        """Send text message with optional link preview"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'text',
            'text': {
                'body': message,
                'preview_url': preview_url
            }
        }
        return await self._send_request(payload)
    
    async def send_image_message(self, to_number: str, image_url: str, caption: str = None) -> Dict[str, Any]:
        """Send image message with optional caption"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'image',
            'image': {
                'link': image_url
            }
        }
        if caption:
            payload['image']['caption'] = caption
        return await self._send_request(payload)
    
    async def send_document_message(self, to_number: str, document_url: str, filename: str, caption: str = None) -> Dict[str, Any]:
        """Send document message"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'document',
            'document': {
                'link': document_url,
                'filename': filename
            }
        }
        if caption:
            payload['document']['caption'] = caption
        return await self._send_request(payload)
    
    async def send_location_message(self, to_number: str, latitude: float, longitude: float, name: str = None, address: str = None) -> Dict[str, Any]:
        """Send location message"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'location',
            'location': {
                'latitude': latitude,
                'longitude': longitude
            }
        }
        if name:
            payload['location']['name'] = name
        if address:
            payload['location']['address'] = address
        return await self._send_request(payload)
    
    async def send_contact_message(self, to_number: str, contacts: List[Dict]) -> Dict[str, Any]:
        """Send contact message(s)"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'contacts',
            'contacts': contacts
        }
        return await self._send_request(payload)
    
    async def send_interactive_buttons(self, to_number: str, header_text: str, body_text: str, buttons: List[Dict], footer_text: str = None) -> Dict[str, Any]:
        """Send interactive message with buttons"""
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
                                'id': btn['id'],
                                'title': btn['title']
                            }
                        } for btn in buttons[:3]  # WhatsApp limits to 3 buttons
                    ]
                }
            }
        }
        
        if footer_text:
            payload['interactive']['footer'] = {'text': footer_text}
            
        return await self._send_request(payload)
    
    async def send_interactive_list(self, to_number: str, header_text: str, body_text: str, button_text: str, sections: List[Dict], footer_text: str = None) -> Dict[str, Any]:
        """Send interactive list message"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'interactive',
            'interactive': {
                'type': 'list',
                'header': {
                    'type': 'text',
                    'text': header_text
                },
                'body': {
                    'text': body_text
                },
                'action': {
                    'button': button_text,
                    'sections': sections
                }
            }
        }
        
        if footer_text:
            payload['interactive']['footer'] = {'text': footer_text}
            
        return await self._send_request(payload)
    
    async def send_template_message(self, to_number: str, template_name: str, components: List[Dict] = None, lang: str = "en_US") -> Dict[str, Any]:
        """Send template message"""
        payload = {
            'messaging_product': 'whatsapp',
            'to': to_number,
            'type': 'template',
            'template': {
                'name': template_name,
                'language': {
                    'code': lang
                }
            }
        }
        
        if components:
            payload['template']['components'] = components
            
        return await self._send_request(payload)
    
    async def mark_message_as_read(self, message_id: str) -> Dict[str, Any]:
        """Mark message as read"""
        payload = {
            'messaging_product': 'whatsapp',
            'status': 'read',
            'message_id': message_id
        }
        return await self._send_request(payload)
    
    async def react_to_message(self, message_id: str, emoji: str) -> Dict[str, Any]:
        """React to a message with emoji"""
        payload = {
            'messaging_product': 'whatsapp',
            'type': 'reaction',
            'reaction': {
                'message_id': message_id,
                'emoji': emoji
            }
        }
        return await self._send_request(payload)
    
    async def upload_media(self, file_path: str, media_type: str) -> Dict[str, Any]:
        """Upload media file to WhatsApp servers"""
        # First, get the media upload URL
        upload_url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}/media"
        
        # Determine MIME type
        mime_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
        
        # Read file content
        with open(file_path, 'rb') as file:
            file_content = file.read()
        
        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': mime_type
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                upload_url,
                headers=headers,
                content=file_content
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                raise Exception(f"Upload failed: {response.status_code} - {response.text}")
    
    async def _send_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send request to WhatsApp Cloud API"""
        if not all([self.api_url, self.access_token, self.phone_number_id]):
            raise Exception("WhatsApp API configuration missing")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.api_url,
                    headers=self.headers,
                    json=payload,
                    timeout=30.0
                )
                
                response_data = response.json()
                
                if response.status_code == 200:
                    print(f"✅ Message sent successfully: {payload.get('type', 'unknown')}")
                    return response_data
                else:
                    print(f"❌ Failed to send message: {response.status_code}")
                    print(f"Error: {response_data}")
                    raise Exception(f"WhatsApp API error: {response.status_code}")
                    
        except httpx.RequestError as e:
            print(f"🔌 HTTP request error: {e}")
            raise Exception(f"Network error: {e}")
        except Exception as e:
            print(f"💥 Unexpected error: {e}")
            raise
    
    async def get_business_profile(self) -> Dict[str, Any]:
        """Get business profile information"""
        url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}"
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                headers={'Authorization': f'Bearer {self.access_token}'}
            )
            return response.json()
    
    async def update_business_profile(self, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Update business profile"""
        url = f"https://graph.facebook.com/{self.version}/{self.phone_number_id}"
        
        payload = {
            'messaging_product': 'whatsapp'
        }
        payload.update(profile_data)
        
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                url,
                headers={'Authorization': f'Bearer {self.access_token}', 'Content-Type': 'application/json'},
                json=payload
            )
            return response.json()
