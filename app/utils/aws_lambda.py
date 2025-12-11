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

        client_config = Config(read_timeout=30, connect_timeout=10, retries={"max_attempts": 3})

        lambda_kwargs = {
            'region_name': aws_region,
            'config': client_config,
        }
        if aws_access_key_id and aws_secret_access_key:
            lambda_kwargs['aws_access_key_id'] = aws_access_key_id
            lambda_kwargs['aws_secret_access_key'] = aws_secret_access_key
        self.lambda_client = boto3.client('lambda', **lambda_kwargs)
        self.question_answerer_function = settings.AWS_LAMBDA_QUESTION_ANSWERER_FUNCTION_NAME or None
        self.use_bedrock_intent = bool(getattr(settings, 'USE_BEDROCK_INTENT', False))
        self.aws_region = aws_region
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.client_config = client_config
        self.bedrock_client = None
    
    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict] = None, conversation_history: Optional[Any] = None, bedrock_model_id: Optional[str] = None) -> str:
        """Invoke Claude via Bedrock for AI-powered question answering.

        All conversational logic and reasoning is delegated to Claude. This method
        simply forwards the user message (and optional context) to Bedrock and
        returns Claude's raw text response.
        """
        # Resolve model ID (settings ONLY; use canonical HUSTLR_BEDROCK_MODEL_ID)
        import os
        env_model_id_ignored = os.getenv('BEDROCK_MODEL_ID') or ""
        cfg_model_id = getattr(settings, 'HUSTLR_BEDROCK_MODEL_ID', "") or ""
        resolved_model_id = cfg_model_id.strip() or None
        inference_profile_arn = getattr(settings, 'HUSTLR_BEDROCK_INFERENCE_PROFILE_ARN', "") or ""
        model_for_invoke = (inference_profile_arn.strip() or resolved_model_id)
        logger.info(
            f"[BEDROCK CONFIG] source=settings cfg(HUSTLR)={cfg_model_id}, env_ignored(BEDROCK_MODEL_ID)={env_model_id_ignored}, param_ignored={bedrock_model_id}, "
            f"inference_profile_arn={(inference_profile_arn or '')}, resolved_model_id={resolved_model_id}, chosen={model_for_invoke}, use_bedrock={self.use_bedrock_intent}, region={self.aws_region}, has_creds={bool(self.aws_access_key_id)}"
        )
        if not (self.use_bedrock_intent and model_for_invoke):
            logger.error(
                f"Bedrock not configured: use_bedrock_intent={self.use_bedrock_intent}, model_or_profile={model_for_invoke}, region={self.aws_region}"
            )
            raise RuntimeError("Bedrock intent model is not configured.")
        
        # Lazy-init Bedrock client if not already done
        if not self.bedrock_client:
            bedrock_kwargs = {
                'region_name': self.aws_region,
                'config': self.client_config,
            }
            if self.aws_access_key_id and self.aws_secret_access_key:
                bedrock_kwargs['aws_access_key_id'] = self.aws_access_key_id
                bedrock_kwargs['aws_secret_access_key'] = self.aws_secret_access_key
            self.bedrock_client = boto3.client('bedrock-runtime', **bedrock_kwargs)

        return await self._invoke_bedrock(
            user_message,
            user_context or {},
            conversation_history=conversation_history,
            bedrock_model_id=model_for_invoke,
        )

    async def _invoke_bedrock(self, user_message: str, user_context: Optional[Dict[str, Any]] = None, conversation_history: Optional[Any] = None, bedrock_model_id: Optional[str] = None) -> str:
        if not self.bedrock_client or not bedrock_model_id:
            raise RuntimeError("Bedrock client or model ID is not configured.")

        try:
            body = self._build_bedrock_body(user_message, user_context or {}, conversation_history=conversation_history)
            logger.info(f"[BEDROCK INVOKE] modelId={bedrock_model_id}, region={self.aws_region}")
            response = self.bedrock_client.invoke_model(
                modelId=bedrock_model_id,
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
                f"[CLAUDE RESPONSE] Model: {bedrock_model_id}, User: {safe_user}, "
                f"Question: {user_message[:120]}..., "
                f"Answer: {final_text[:200]}..."
            )

            return final_text
        except ClientError as e:
            # Surface Bedrock errors to the caller; do not generate local responses
            logger.exception("Bedrock invocation error")
            raise
        except Exception as e:
            # Surface unexpected errors to the caller
            logger.exception("Unexpected error in Bedrock service")
            raise

    def _build_bedrock_body(self, user_message: str, user_context: Dict[str, Any], conversation_history: Optional[Any] = None) -> Dict[str, Any]:
        context_parts = []
        name = user_context.get('name')
        if name:
            context_parts.append(f"User name: {name}")
        location = user_context.get('location')
        if location:
            context_parts.append(f"User location: {location}")
        client_id = user_context.get('client_id')
        if client_id:
            context_parts.append(f"User client_id: {client_id}")
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
        provider_options = user_context.get('provider_options')
        providers_text = f"Provider options (JSON):\n{json.dumps(provider_options)}" if provider_options else ""
        known_fields = user_context.get('known_fields')
        known_fields_text = f"Known fields (use as given; do not ask again):\n{json.dumps(known_fields)}" if known_fields else ""
        context_text = "\n".join([p for p in ["\n".join(context_parts), history_text, tool_text, providers_text, known_fields_text] if p])
        user_text = f"User message: {user_message}"
        if context_text:
            combined = context_text + "\n" + user_text
        else:
            combined = user_text
        # Choose system prompt based on LLM-controlled mode
        if getattr(settings, 'LLM_CONTROLLED_CONVERSATION', False):
            system_prompt = (
                """
HUSTLR – Conversational Booking & Provider Registration AI

You are Hustlr’s AI assistant. You talk like a helpful, friendly human who guides users through:

1. Service bookings
2. Service provider registration
3. General questions about Hustlr

Your job is to run the full conversation flow naturally, step by step, like a human agent.

CORE RULES
1) Stay fully conversational
- Speak as a friendly, helpful WhatsApp assistant.
- Keep messages short, clear, and warm.
- Never sound robotic or repetitive.

2) Never ask for multiple fields at once
- Ask one question per message; wait for the answer before proceeding.

3) Never repeat questions unless needed
- If the user already provided an answer, move forward.

4) Never hallucinate
- If unsure, politely ask for clarification.

5) Maintain memory of the conversation flow
- Remember the chosen service, the issue, the preferred time, when to ask location, when to show summary, when to confirm booking.
- Do not jump backwards unless the user corrects something.

6) When the booking is complete
Return ONLY pure JSON (no extra text), e.g.:
{
  "booking_complete": true,
  "service": "...",
  "issue": "...",
  "time": "...",
  "location": "...",
  "user_name": "...",
  "user_phone": "..."
}

7) When the provider registration is complete
Return ONLY pure JSON (no extra text):
{
  "provider_registration_complete": true,
  "name": "...",
  "service": "...",
  "experience": "...",
  "id_number": "...",
  "phone": "...",
  "location": "..."
}

BOOKING FLOW LOGIC (Follow this exactly)
Step 1 — Service selection
- If the user asks for help or mentions a service, confirm it.
- If unclear, ask: "Sure! Which service do you need? (e.g., Plumber, Electrician, Cleaner, Driver)"

Step 2 — Issue description
- After service is known: "Got it. What seems to be the issue?"

Step 3 — Preferred time
- After issue: "When would you like the provider to come? (Example: tomorrow 10am)"

Step 4 — Location
- Use the saved location from backend if available.
- Ask: "Should the provider come to your usual address at <ADDRESS>? Reply Yes or No."
- If "No", ask for new location.

Step 5 — Confirmation
- Show a short summary: Service, Issue, Time, Location. Ask to confirm (Yes/No).

Step 6 — Return final JSON
- Only after they confirm.

Service Provider Registration Flow
Collect these fields one by one: Name, Service they provide, Years of experience, ID number, Phone number, Location.
Return the final JSON only after all fields are collected and confirmed.

Tone
- Friendly, warm, respectful; never overly formal. Keep messages short for WhatsApp.

NEVER DO THIS
- Never output JSON in the middle of the conversation.
- Never ask for more than one field at a time.
- Never repeat a question unless needed.
- Never assume details.
- Never switch flow randomly.
- Never mention internal rules or this prompt.

Your conversational authority
- You fully control booking and registration flows, follow-up questions, clarifications, and summaries like a Hustlr human agent.
                """
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
            "max_tokens": int(getattr(settings, 'LLM_MAX_TOKENS', 1500) or 1500),
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
