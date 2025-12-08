import os
import boto3
import json
import logging
from typing import Dict, Any, Optional
from botocore.exceptions import ClientError
from botocore.config import Config
from config import settings

logger = logging.getLogger(__name__)

class AWSLambdaService:
    """Service for interacting with AWS Lambda functions"""
    
    def __init__(self):
        aws_access_key_id = settings.AWS_ACCESS_KEY_ID
        aws_secret_access_key = settings.AWS_SECRET_ACCESS_KEY
        aws_region = settings.AWS_REGION

        client_config = Config(read_timeout=5, connect_timeout=3, retries={"max_attempts": 2})

        self.lambda_client = boto3.client(
            'lambda',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            region_name=aws_region,
            config=client_config,
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
                config=client_config,
            )
    
    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict] = None, conversation_history: Optional[Any] = None) -> str:
        """Invoke Claude Sonnet via Bedrock for AI-powered question answering.

        All conversational logic and reasoning is delegated to Claude. This method
        simply forwards the user message (and optional context) to Bedrock and
        returns Claude's raw text response.
        """
        if not (self.use_bedrock_intent and self.bedrock_client and self.bedrock_model_id):
            raise RuntimeError("Bedrock intent model is not configured.")

        return await self._invoke_bedrock(user_message, user_context or {}, conversation_history=conversation_history)

    async def _invoke_bedrock(self, user_message: str, user_context: Optional[Dict[str, Any]] = None, conversation_history: Optional[Any] = None) -> str:
        if not self.bedrock_client or not self.bedrock_model_id:
            raise RuntimeError("Bedrock client or model ID is not configured.")

        try:
            body = self._build_bedrock_body(user_message, user_context or {}, conversation_history=conversation_history)
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

    def _build_bedrock_body(self, user_message: str, user_context: Dict[str, Any], conversation_history: Optional[Any] = None) -> Dict[str, Any]:
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
        # Compact conversation history if provided (last few exchanges)
        history_text = ""
        try:
            if conversation_history:
                # Expect list of {role: 'user'|'assistant', 'text': '...'} or similar
                lines = []
                # Keep last 6 messages max
                tail = conversation_history[-6:] if isinstance(conversation_history, list) else []
                for h in tail:
                    role = (h.get('role') or h.get('sender') or '').lower()
                    text = h.get('text') or h.get('message') or h.get('content') or ''
                    if not text:
                        continue
                    if 'user' in role:
                        lines.append(f"User: {text}")
                    elif 'assistant' in role or 'bot' in role:
                        lines.append(f"Assistant: {text}")
                if lines:
                    history_text = "Recent chat history:\n" + "\n".join(lines)
        except Exception:
            history_text = ""

        # Include last tool result (e.g., provider list) so the model can reason on it
        tool_result = user_context.get('tool_result')
        tool_text = f"Tool result available:\n{tool_result}" if tool_result else ""
        context_text = "\n".join([p for p in ["\n".join(context_parts), history_text, tool_text] if p])
        user_text = f"User message: {user_message}"
        if context_text:
            combined = context_text + "\n" + user_text
        else:
            combined = user_text
        # Choose system prompt based on LLM-controlled mode
        if getattr(settings, 'LLM_CONTROLLED_CONVERSATION', False):
            system_prompt = (
                "You are Hustlr, a WhatsApp assistant.\n"
                "Lead the conversation with short, helpful replies (max 2 sentences).\n"
                "Ask one clarifying question at a time when needed. Prefer the user's saved location if available.\n"
                "You control the flow. When you need the backend to act, emit a SINGLE line at the END of your reply starting with '>>> ' containing a JSON object describing the action.\n\n"
                "Available actions (emit exactly one per turn when needed):\n"
                "- list_providers: {\"action\":\"list_providers\", \"service_type\":\"plumber\"} (backend will show provider options to the user; then WAIT for the user's pick).\n"
                "- create_booking: {\"action\":\"create_booking\", \"service_type\":\"plumber\", \"issue\":\"...\", \"time_text\":\"tomorrow 10am\", \"provider_index\":1} (provider_index corresponds to the options previously shown).\n"
                "- register_provider: {\"action\":\"register_provider\", \"name\":\"...\", \"service_type\":\"...\", \"location\":\"...\", \"business_name\":\"...\", \"contact\":\"...\"}\n\n"
                "Required fields you must collect before create_booking: service_type, issue (short), time_text, provider_index (from shown options). Location is the saved one if available; do not ask again.\n"
                "Required fields for register_provider: name, service_type, location, contact; business_name optional.\n"
                "Do not invent provider names; rely on backend provider options.\n"
                "Return a normal human-friendly message for the user, THEN the '>>> {json}' line on the last line only when you need the backend to act.\n"
            )
        else:
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
            "max_tokens": 200,
            "temperature": 0.3,
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
