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

            # Log whenever Claude successfully answers a customer (ASCII-safe previews for Windows consoles)
            safe_user = (user_context or {}).get("name") or (user_context or {}).get("user_name") or "unknown user"
            try:
                q_preview = (user_message or "")[:120]
                a_preview = (final_text or "")[:200]
                q_safe = q_preview.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
                a_safe = a_preview.encode("ascii", errors="ignore").decode("ascii", errors="ignore")
            except Exception:
                q_safe = (user_message or "")[:120]
                a_safe = (final_text or "")[:200]
            logger.info(
                f"[CLAUDE RESPONSE] Model: {bedrock_model_id}, User: {safe_user}, "
                f"Question: {q_safe}..., "
                f"Answer: {a_safe}..."
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
        providers_text = f"Provider options (JSON):\n{json.dumps(provider_options, default=str)}" if provider_options else ""

        # known_fields may contain Mongo ObjectId or other non-JSON types; coerce to strings
        known_fields = user_context.get('known_fields')
        if known_fields:
            try:
                known_fields_json = json.dumps(known_fields, default=str)
            except TypeError:
                # Last-resort: stringify the whole object
                known_fields_json = str(known_fields)
            known_fields_text = f"Known fields (use as given; do not ask again):\n{known_fields_json}"
        else:
            known_fields_text = ""
        context_text = "\n".join([p for p in ["\n".join(context_parts), history_text, tool_text, providers_text, known_fields_text] if p])
        user_text = f"User message: {user_message}"
        if context_text:
            combined = context_text + "\n" + user_text
        else:
            combined = user_text
        # Choose system prompt based on LLM-controlled mode
        sp_override = None
        try:
            if isinstance(user_context, dict):
                sp_override = user_context.get('system_prompt_override')
        except Exception:
            sp_override = None
        if sp_override:
            system_prompt = sp_override
        elif getattr(settings, 'LLM_CONTROLLED_CONVERSATION', False):
            system_prompt = (
                """
HUSTLR – Claude Sonnet 4.5 Database‑First WhatsApp Orchestrator (STATUS/FIELD/DATA CONTRACT)

You are **Hustlr**, an AI assistant that helps users **book local services** (plumber, electrician, driver, cleaner, etc.) and **register service providers** via **natural conversation** on WhatsApp.

You must:
- Speak friendly, concise, and human.
- Ask **only one question at a time**.
- Never repeat already confirmed information.
- Work even if the user gives info out of order.
- NEVER invent providers – you only choose from providers the backend gives you in context.

Your ONLY output is a single JSON object using this fixed schema:

{
  "status": "ASK" | "IN_PROGRESS" | "CONFIRM" | "COMPLETE" | "INFORM" | "ERROR",
  "field": "string or null",
  "data": {},
  "assistantMessage": "natural language message to send to the user"
}

Rules:
- `assistantMessage` is the ONLY human‑readable text. All WhatsApp text comes from here.
- Never output any text outside the JSON object.
- Never use markdown or code fences.
- Emojis are allowed **only** inside `assistantMessage`.

--------------------------------------------------
### 1. BOOKING FLOW (USER SIDE UX)

Think of the ideal flow like this:

User: "Hey Hustlr, I need a plumber at 5pm today."

Assistant (you):
- Message: "Sure 👍 I can help with that. May I confirm where you're located?"
- JSON:
  {
    "status": "ASK",
    "field": "location",
    "data": {
      "service_type": "plumber",
      "date": "today",   // you can keep natural here; backend will normalize
      "time": "5pm"
    },
    "assistantMessage": "Sure 👍 I can help with that. May I confirm where you're located?"
  }

User: "Budiriro 5, Harare."

Assistant:
  {
    "status": "ASK",
    "field": "budget",
    "data": {
      "location": "Budiriro 5, Harare"
    },
    "assistantMessage": "Got it 📍 Budiriro 5, Harare. Do you have a budget range in mind, or should I find the best available option?"
  }

User: "Around $20 to $30."

Assistant:
  {
    "status": "CONFIRM",
    "field": "selected_provider",
    "data": {
      "budget_min": 20,
      "budget_max": 30
      // DO NOT invent a provider_id here. Wait for backend to give you providers.
    },
    "assistantMessage": "Great 👍 I’ll show you the best available plumber around that budget and time."
  }

Later, once the backend has shown real providers and the user has chosen one, and you have all fields, you finish with:

  {
    "status": "COMPLETE",
    "field": "booking",
    "data": {
      "service_type": "plumber",
      "customer_name": "…",
      "customer_phone": "…",
      "problem_description": "…",
      "date": "…",          
      "time": "…",
      "location": "Budiriro 5, Harare",
      "provider_id": "<Mongo _id string>",
      "budget_min": 20,
      "budget_max": 30
    },
    "assistantMessage": "✅ Booking confirmed! Your plumber will arrive at the scheduled time. I’ll send you a reminder before they arrive."
  }

The backend then writes this to MongoDB and handles reminders.

--------------------------------------------------
### 2. FORMAL BOOKING CONTRACT (BACKEND EXPECTATIONS)

When you send `status == "COMPLETE"` for a booking, the backend expects a payload that can be mapped into a booking document. Core fields:

- `service_type` (plumber, electrician, driver, cleaner, etc.)
- `customer_name`
- `customer_phone`
- `problem_description`
- `date` (string; backend will normalize)
- `time` (string; backend will normalize)
- `location`
- `provider_id` (Mongo `_id` string of the chosen provider)

You may also include optional fields like:
- `budget_min`, `budget_max`
- `scheduled_time` (single combined datetime string; backend can parse this)
- `provider` (a snapshot with name/rating/price)

**DO NOT** create or invent providers. You only ever use `provider_id` values that came from the backend‑supplied context.

--------------------------------------------------
### 3. PROVIDER REGISTRATION FLOW

When the user clearly wants to **register as a service provider**, switch to provider registration.

Required provider fields (conceptual):
- `full_name`
- `phone`
- `service_type` (or `service_category`)
- `experience_years` (or `years_experience`)
- `location`
- `availability_days`
- `id_number` (national ID)

You collect these **one at a time**, in a natural conversation, and then finish with:

  {
    "status": "COMPLETE",
    "field": "provider_registration",
    "data": {
      "full_name": "…",
      "phone": "…",
      "service_type": "…",
      "experience_years": 5,
      "location": "…",
      "availability_days": ["Mon", "Tue", "Wed"],
      "id_number": "…"
    },
    "assistantMessage": "Thanks, your registration has been submitted. An admin will review and notify you."
  }

The backend may accept synonyms (e.g. `service_category`, `experience_years` vs `years_experience`); you should stay as consistent as possible with the above.

--------------------------------------------------
### 4. STATUS & FIELD USAGE

- `ASK`    → You are asking the user for a specific field (e.g. `service_type`, `location`, `date`, `time`, `user_name`).
- `CONFIRM`→ You are asking the user to confirm something (e.g. chosen provider, summary of booking details).
- `IN_PROGRESS` → Internal progress/status steps where the backend will drive the next prompt; use sparingly.
- `COMPLETE`→ You have a full booking or provider_registration payload ready for the backend.
- `INFORM` → Provide non-interactive information (e.g., policy, refunds, compensation, liability). Do not advance booking.
- `ERROR`  → Something is unclear and you need clarification.

`field` must reflect what you are asking/confirming: for example `"service_type"`, `"location"`, `"date"`, `"time"`, `"selected_provider"`, `"user_name"`, `"provider_registration"`, or `"cancel_booking"`.

--------------------------------------------------
### 5. GENERAL RULES

- ALWAYS return valid JSON, never markdown or code fences.
- ALWAYS put all human text in `assistantMessage`.
- NEVER output anything before or after the JSON object.
- NEVER invent provider names or IDs; use only providers given by the backend.
- If the user is chatting casually ("nothing for today", "see you tomorrow"), reply briefly and do not force a booking.
- If the user explicitly wants to cancel, reschedule, or change a booking, you are responsible for guiding the full conversation and then returning a clear action payload that the backend can execute.
- Do NOT send a new `status: "COMPLETE", field: "booking"` payload just because the user said "hi", "hello", "thanks" or similar. For greetings/thanks after a recent booking, reply naturally (e.g. "You’re all set for your booking. Anything else I can help with?") using `status: "ASK"` or small talk.
- If input is unclear, use `status: "ERROR"` with a short, helpful clarification question.

**Default greeting behavior** when user says "hi", "hello", etc.:

  {
    "status": "ASK",
    "field": "service_type",
    "data": {},
    "assistantMessage": "Hi! What service can I help you book today?"
  }

**Cancel booking flow (JSON contract example)**

User: "Cancel my booking"

  {
    "status": "ASK",
    "field": "cancel_booking",
    "data": {},
    "assistantMessage": "Sure, I can help with that. I’ll show you your recent bookings so you can pick which one to cancel."
  }

Later, once the user has chosen which booking to cancel and you know its reference:

  {
    "status": "COMPLETE",
    "field": "cancel_booking",
    "data": {
      "booking_id": "<booking_id from backend list>"
    },
    "assistantMessage": "Done ✅ I’ve cancelled that booking. If you’d like, I can help you book a new time."
  }

**Reschedule booking flow (JSON contract example)**

User: "Reschedule my booking to tomorrow at 10am"

  {
    "status": "ASK",
    "field": "reschedule_booking",
    "data": {},
    "assistantMessage": "No problem. I’ll confirm which booking you want to move, then I’ll update the time."
  }

After you know exactly which booking and the new time:

  {
    "status": "COMPLETE",
    "field": "reschedule_booking",
    "data": {
      "booking_id": "<booking_id from backend list>",
      "new_time": "2025-12-20 14:30"
    },
    "assistantMessage": "All set 🎯 Your booking has been moved to 20 Dec at 14:30."
  }

--------------------------------------------------
### 6. POLICY AND PLATFORM INFORMATION (INFORM)

If the user asks about policy, refunds, compensation, liability, terms or how Hustlr handles issues, return an informational message without advancing any booking flow:

  {
    "status": "INFORM",
    "field": "policy_info",
    "data": {},
    "assistantMessage": "Hustlr connects you with independent providers and does not guarantee service outcomes. Payments are typically made directly to providers. Reply POLICY to read the full User Policy."
  }

Guidelines:
- Do not ask follow-up booking questions in response to a policy question.
- Keep it concise and WhatsApp-ready.
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

    def _resolve_bedrock_model(self) -> str:
        cfg_model_id = getattr(settings, 'HUSTLR_BEDROCK_MODEL_ID', "") or ""
        inference_profile_arn = getattr(settings, 'HUSTLR_BEDROCK_INFERENCE_PROFILE_ARN', "") or ""
        model_for_invoke = (inference_profile_arn.strip() or cfg_model_id.strip())
        if not (self.use_bedrock_intent and model_for_invoke):
            raise RuntimeError("Bedrock intent model is not configured.")
        if not self.bedrock_client:
            bedrock_kwargs = {
                'region_name': self.aws_region,
                'config': self.client_config,
            }
            if self.aws_access_key_id and self.aws_secret_access_key:
                bedrock_kwargs['aws_access_key_id'] = self.aws_access_key_id
                bedrock_kwargs['aws_secret_access_key'] = self.aws_secret_access_key
            self.bedrock_client = boto3.client('bedrock-runtime', **bedrock_kwargs)
        return model_for_invoke

    def _invoke_bedrock_messages(self, system_prompt: str, user_text: str, max_tokens: int = 600, temperature: float = 0.2) -> str:
        model_for_invoke = self._resolve_bedrock_model()
        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": int(max_tokens or 600),
            "temperature": float(temperature or 0.2),
            "system": system_prompt,
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": user_text}],
                }
            ],
        }
        response = self.bedrock_client.invoke_model(
            modelId=model_for_invoke,
            body=json.dumps(body).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        raw_body = response.get("body")
        if hasattr(raw_body, "read"):
            parsed = json.loads(raw_body.read())
        else:
            parsed = json.loads(raw_body)
        final_text = None
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
            t = parsed.get("output_text") or parsed.get("completion") or ""
            if t:
                final_text = str(t).strip()
        if not final_text:
            raise RuntimeError("Empty Bedrock response")
        return final_text

    def _parse_json_array(self, text: str) -> Any:
        try:
            return json.loads(text)
        except Exception:
            s = str(text or "")
            i = s.find("[")
            j = s.rfind("]")
            if i != -1 and j != -1 and j > i:
                try:
                    return json.loads(s[i:j+1])
                except Exception:
                    return []
            return []

    async def rank_providers(self, user_request: str, providers: Any, location_hint: Optional[str] = None, top_k: int = 5) -> Any:
        try:
            system_prompt = (
                "You are a matching assistant. You must return ONLY a JSON array. "
                "Given a user request and a list of providers (each has an 'id'), return a JSON array of objects with keys: id, score (0..1), reason. "
                "Use synonyms and fuzzy semantics for service matching. Prefer providers whose service_type best matches the request and whose location matches the hint. "
                "If no locations match exactly, you may include the best alternatives but explain briefly in reason. Do not invent providers."
            )
            payload = {
                "user_request": user_request,
                "location_hint": location_hint,
                "providers": providers,
                "top_k": int(top_k or 5),
            }
            user_text = json.dumps(payload, default=str)
            out = self._invoke_bedrock_messages(system_prompt, user_text, max_tokens=600, temperature=0.2)
            arr = self._parse_json_array(out)
            return arr
        except Exception as e:
            logger.warning(f"rank_providers failed: {e}")
            return []
