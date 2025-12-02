import os
import boto3
import json
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError
from config import settings

class AWSLambdaService:
    """Service for interacting with AWS Lambda functions"""
    
    def __init__(self):
        aws_access_key_id = settings.AWS_ACCESS_KEY_ID
        aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
        aws_region = settings.AWS_REGION

        self.lambda_client = boto3.client(
            'lambda',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=aws_region,
        )
        self.question_answerer_function = settings.AWS_LAMBDA_QUESTION_ANSWERER_FUNCTION_NAME or None
        self.use_bedrock_intent = bool(getattr(settings, 'USE_BEDROCK_INTENT', False))
        self.bedrock_model_id = getattr(settings, 'BEDROCK_MODEL_ID', "") or None
        self.bedrock_client = None
        if self.use_bedrock_intent and self.bedrock_model_id:
            self.bedrock_client = boto3.client(
                'bedrock-runtime',
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=aws_region,
            )
    
    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict] = None) -> str:
        """
        Invoke Lambda function for AI-powered question answering
        
        Args:
            user_message: The message from the user
            user_context: Optional user context (name, location, booking history)
            
        Returns:
            AI-generated response
        """
        # Try Bedrock first if enabled
        if self.use_bedrock_intent and self.bedrock_client and self.bedrock_model_id:
            try:
                return await self._invoke_bedrock(user_message, user_context or {})
            except Exception as e:
                print(f"Bedrock failed, falling back to local handler: {e}")
                return self._generate_local_response(user_message, user_context or {})
        
        # Try Lambda if configured
        if self.question_answerer_function:
            try:
                return await self._invoke_lambda(user_message, user_context or {})
            except Exception as e:
                print(f"Lambda failed, falling back to local handler: {e}")
                return self._generate_local_response(user_message, user_context or {})
        
        # Default to local response generation
        return self._generate_local_response(user_message, user_context or {})
        
        try:
            payload = {
                'user_message': user_message,
                'user_context': user_context or {}
            }
            
            response = self.lambda_client.invoke(
                FunctionName=self.question_answerer_function,
                InvocationType='RequestResponse',
                Payload=json.dumps(payload)
            )
            
            result = json.loads(response['Payload'].read())
            
            if 'errorMessage' in result:
                return f"Sorry, I'm having trouble understanding. Could you try rephrasing?"
            
            return result.get('response', 'I apologize, but I cannot help with that request.')
            
        except ClientError as e:
            print(f"Lambda invocation error: {e}")
            return "I'm experiencing technical difficulties. Please try again later."
        except Exception as e:
            print(f"Unexpected error in Lambda service: {e}")
            return "An unexpected error occurred. Please try again."

    async def _invoke_bedrock(self, user_message: str, user_context: Optional[Dict[str, Any]] = None) -> str:
        if not self.bedrock_client or not self.bedrock_model_id:
            return "AI service not configured. Please contact support."
        try:
            body = self._build_bedrock_body(user_message, user_context or {})
            response = self.bedrock_client.invoke_model(
                modelId=self.bedrock_model_id,
                body=json.dumps(body).encode("utf-8"),
                contentType="application/json",
                accept="application/json",
            )
            raw_body = response.get("body")
            if hasattr(raw_body, "read"):
                parsed = json.loads(raw_body.read())
            else:
                parsed = json.loads(raw_body)
            content = parsed.get("content")
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        t = item.get("text") or ""
                        if t:
                            texts.append(t)
                if texts:
                    return "\n".join(texts).strip()
            text = parsed.get("output_text") or parsed.get("completion") or ""
            if text:
                return str(text).strip()
            return "I apologize, but I cannot help with that request."
        except ClientError as e:
            print(f"Bedrock invocation error: {e}")
            return "I'm experiencing technical difficulties. Please try again later."
        except Exception as e:
            print(f"Unexpected error in Bedrock service: {e}")
            return "An unexpected error occurred. Please try again."

    def _build_bedrock_body(self, user_message: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
        context_parts = []
        name = user_context.get('name')
        if name:
            context_parts.append(f"User name: {name}")
        location = user_context.get('location')
        if location:
            context_parts.append(f"User location: {location}")
        history = user_context.get('booking_history')
        if history:
            context_parts.append(f"Booking history: {history}")
        context_text = "\n".join(context_parts)
        user_text = f"User message: {user_message}"
        if context_text:
            combined = context_text + "\n" + user_text
        else:
            combined = user_text
        system_prompt = (
            "You are Hustlr, a WhatsApp assistant that helps users find and book local service providers "
            "like plumbers, electricians, carpenters, cleaners, and more.\n\n"
            "IMPORTANT GUIDELINES:\n"
            "1. RESPOND TO EVERYTHING: Answer all messages warmly and helpfully, even casual greetings or off-topic messages.\n"
            "2. GENTLY STEER TO BOOKING: In every response, naturally guide the conversation toward finding or booking a service.\n"
            "3. BE CONVERSATIONAL: Feel like a helpful friend, not a bot. Use natural language.\n"
            "4. PROVIDE GUIDANCE: Always give clear next steps for what the user can do.\n\n"
            "RESPONSE PATTERNS:\n"
            "- Greeting (Hi, Hello, Morning): Respond warmly, then ask what service they need.\n"
            "  Example: 'Morning! Great to hear from you. What service can I help you find today?'\n"
            "- Small talk (How are you, etc): Respond genuinely, then pivot to helping them.\n"
            "  Example: 'Doing great, thanks for asking! By the way, do you need any service help?'\n"
            "- Service inquiry: Respond enthusiastically and guide them to booking.\n"
            "  Example: 'Perfect! I can help you find a plumber. Just tell me what the issue is and I'll show you available providers.'\n"
            "- Random question: Answer helpfully, then mention how Hustlr can help.\n"
            "  Example: 'Good question! Speaking of which, if you ever need a service provider, I'm here to help.'\n"
            "- Confusion or unclear message: Ask clarifying questions and offer service options.\n"
            "  Example: 'I'm not sure I understood. Are you looking for a service? I can help you find plumbers, electricians, carpenters, and more!'\n\n"
            "TONE & STYLE:\n"
            "- Friendly and warm, like texting a helpful friend\n"
            "- No emojis\n"
            "- Short and concise (WhatsApp-friendly)\n"
            "- Natural and conversational\n"
            "- Always helpful and encouraging\n\n"
            "BOOKING SERVICES AVAILABLE:\n"
            "Plumber, Electrician, Carpenter, Cleaner, Painter, Mechanic, Locksmith, and more.\n"
            "Always be ready to help users find any service they need in their area."
        )
        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 512,
            "temperature": 0.4,
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": combined,
                        }
                    ],
                }
            ],
        }
