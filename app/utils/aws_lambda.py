import os
import boto3
import json
import logging
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError
from config import settings

logger = logging.getLogger(__name__)

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
        """Invoke Claude Sonnet via Bedrock for AI-powered question answering.

        All conversational logic and reasoning is delegated to Claude. This method
        simply forwards the user message (and optional context) to Bedrock and
        returns Claude's raw text response.
        """
        if not (self.use_bedrock_intent and self.bedrock_client and self.bedrock_model_id):
            raise RuntimeError("Bedrock intent model is not configured.")

        return await self._invoke_bedrock(user_message, user_context or {})

    async def _invoke_bedrock(self, user_message: str, user_context: Optional[Dict[str, Any]] = None) -> str:
        if not self.bedrock_client or not self.bedrock_model_id:
            raise RuntimeError("Bedrock client or model ID is not configured.")

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

            # Extract main text from Claude response
            final_text: Optional[str] = None

            content = parsed.get("content")
            if isinstance(content, list):
                texts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        t = item.get("text") or ""
                        if t:
                            texts.append(t)
                if texts:
                    final_text = "\n".join(texts).strip()

            if not final_text:
                text = parsed.get("output_text") or parsed.get("completion") or ""
                if text:
                    final_text = str(text).strip()

            if not final_text:
                raise RuntimeError("Claude response did not contain any text content.")

            # Log whenever Claude successfully answers a customer
            safe_user = (user_context or {}).get("name") or "unknown user"
            logger.info(
                f"[CLAUDE RESPONSE] User: {safe_user}, "
                f"Question: {user_message[:120]}..., "
                f"Answer: {final_text[:200]}..."
            )

            return final_text
        except ClientError as e:
            # Surface Bedrock errors to the caller; do not generate local responses
            print(f"Bedrock invocation error: {e}")
            raise
        except Exception as e:
            # Surface unexpected errors to the caller
            print(f"Unexpected error in Bedrock service: {e}")
            raise

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
            "1. RESPOND TO EVERYTHING: Answer all messages warmly and helpfully, even casual greetings, jokes, or off-topic small talk.\n"
            "2. GENTLY STEER TO BOOKING: After a short friendly reply, naturally guide the conversation toward finding or booking a service.\n"
            "3. BE CONVERSATIONAL: Feel like a helpful friend, not a bot. Use natural language.\n"
            "4. PROVIDE GUIDANCE: Always give clear next steps for what the user can do.\n\n"
            "RESPONSE PATTERNS:\n"
            "- Greeting (Hi, Hello, Morning): Respond warmly, maybe with a brief friendly comment, then ask what service they need.\n"
            "  Example: 'Morning! Hope your day is going well. What service can I help you find today?'\n"
            "- Small talk (How are you, how is your day, light chit chat): Respond genuinely with one short friendly sentence, then gently bring it back to how you can help them.\n"
            "  Example: 'I'm doing great, thanks for asking! How are you doing? If you need help with any service, I can find someone for you.'\n"
            "- Service inquiry: Respond enthusiastically and guide them to booking.\n"
            "  Example: 'Perfect! I can help you find a plumber. Just tell me what the issue is and I'll show you available providers.'\n"
            "- Random question: Answer helpfully, then mention how Hustlr can help with services.\n"
            "  Example: 'Good question! By the way, if you ever need a service provider, I'm here to help you find one.'\n"
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
