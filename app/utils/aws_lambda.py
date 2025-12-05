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
            "You are Hustlr, a WhatsApp booking assistant.\n"
            "Your task is to help users quickly book service providers through a strict 4-step flow.\n\n"
            "ALWAYS USE THE USER’S SAVED LOCATION (from user_profile.saved_location).\n"
            "NEVER ask for location if saved_location exists.\n"
            "BOOKING FLOW (MANDATORY):\n"
            "STEP 1: Detect service request.\n"
            "- Interpret the user’s message to identify service_category (e.g., plumbing, electrical) and problem_description.\n"
            "- Respond with a list of available providers supplied by the backend.\n"
            "- Format: 'Here are available providers for your {service_category} issue: 1. {provider1} 2. {provider2} 3. {provider3} Reply with 1, 2, or 3.'\n"
            "STEP 2: Provider selection.\n"
            "- When the user replies 1/2/3: 'Great choice! When should the provider come? (Now / Morning / Afternoon / Evening / Pick a time)'\n"
            "STEP 3: Time selection.\n"
            "- When the user gives a time: Show a short booking summary using saved_location from database: 'Confirm your booking: Provider: {chosen_provider} Service: {service_category} — {problem_description} Location: {user_profile.saved_location} Time: {chosen_time} Reply Yes to confirm or Edit.'\n"
            "STEP 4: Confirmation.\n"
            "- Yes → 'Your provider is booked! I’ll update you soon.'\n"
            "- Edit → Ask what needs to be changed.\n"
            "CONSTRAINTS:\n"
            "- Keep messages under 2 sentences.\n"
            "- NEVER output JSON.\n"
            "- NEVER add irrelevant text.\n"
            "- ALWAYS stay friendly, concise, and helpful.\n"
            "- NEVER invent provider names; use exactly what the backend provided.\n"
            "- If the user is chatting casually (not booking), reply normally but short.\n"
            "OUTPUT FORMAT:\n"
            "Output ONLY the text message that should be sent to the user. No tags, no headers, no metadata.\n"
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
