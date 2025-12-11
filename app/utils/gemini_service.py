import logging
from typing import Any, Dict, Optional

import google.generativeai as genai
from starlette.concurrency import run_in_threadpool

from config import settings


logger = logging.getLogger(__name__)


class GeminiService:
    """Service for interacting with Google Gemini for testing.

    This mirrors the AWSLambdaService interface so it can be swapped in
    for AI responses without changing the rest of the application.
    """

    def __init__(self) -> None:
        api_key = getattr(settings, "GEMINI_API_KEY", "")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        genai.configure(api_key=api_key)
        self.model_name: str = getattr(settings, "GEMINI_MODEL_NAME", "gemini-1.5-flash")

    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict[str, Any]] = None, conversation_history: Optional[Any] = None) -> str:
        """Invoke Gemini to generate a response for the user message.

        Runs the synchronous Gemini client in a threadpool so the FastAPI
        event loop is not blocked.
        """
        context = user_context or {}
        return await run_in_threadpool(self._invoke_sync, user_message, context, conversation_history)

    def _invoke_sync(self, user_message: str, user_context: Dict[str, Any], conversation_history: list = None) -> str:
        # Strict JSON-only contract compatible with backend handler
        system_prompt = (
            "You are Hustlr’s AI assistant. Respond ONLY with valid JSON. No other text.\n"
            "Return one of the following objects:\n"
            "1) {\"status\": \"IN_PROGRESS\", \"next_question\": \"warm, friendly guidance (3-5 short sentences) asking for exactly one missing detail\"}\n"
            "2) {\"status\": \"COMPLETE\", \"type\": \"booking\" | \"provider_registration\", \"data\": { ... }}\n"
            "For bookings (type = 'booking') the data object MUST be:\n"
            "  {\"service_type\": string,\n"
            "   \"service_provider_id\": string,  // MUST be one of provider_options[].id\n"
            "   \"date\": string,                 // e.g. '2025-12-11' or 'tomorrow'\n"
            "   \"time\": string,                 // e.g. '10:00' or '10am'\n"
            "   \"additional_notes\": string optional }\n"
            "Do NOT set status='COMPLETE' for a booking until all of: service_type, service_provider_id, date, and time are known.\n"
            "Use status='IN_PROGRESS' with next_question to politely ask for exactly one missing field at a time.\n"
            "Tone & Style:\n"
            "- Be warm, friendly, and helpful; use a natural WhatsApp tone.\n"
            "- Gently guide the user and provide context so it feels human and caring.\n"
            "- Keep each message to 3–5 short sentences (no walls of text).\n"
            "Rules:\n"
            "- Ask exactly one thing at a time (IN_PROGRESS).\n"
            "- Do not repeat questions for known_fields.\n"
            "- Never invent providers; use provider_options if present in context, and always pick service_provider_id from those options.\n"
            "- No emojis.\n"
        )

        # Compose simple context text (no heavy logic, just concatenation)
        context_parts = []
        name = user_context.get("name")
        if name:
            context_parts.append(f"User name: {name}")
        location = user_context.get("location")
        if location:
            context_parts.append(f"User location: {location}")
        history = user_context.get("booking_history")
        if history:
            context_parts.append(f"Booking history: {history}")
        # Include provider options and known_fields if provided by backend
        provider_options = user_context.get("provider_options")
        if provider_options:
            context_parts.append(f"Provider options: {provider_options}")
        known_fields = user_context.get("known_fields")
        if known_fields:
            context_parts.append(f"Known fields: {known_fields}")

        context_text = "\n".join(context_parts)
        if context_text:
            user_text = context_text + "\n" + f"User message: {user_message}"
        else:
            user_text = f"User message: {user_message}"

        # Configure the model with a system instruction. The content we send
        # is a single user message; Gemini roles must be either "user" or
        # "model", so we do not send an explicit "system" role.
        model = genai.GenerativeModel(
            self.model_name,
            system_instruction=system_prompt,
            generation_config={
                "response_mime_type": "application/json",
                "temperature": 0.2,
                "max_output_tokens": 4000,
            },
        )

        # Build the message history for Gemini.
        # If conversation_history is provided, convert it to Gemini format and append current message.
        messages = []
        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role", "user")
                text = msg.get("text", "")
                # Gemini expects "user" or "model" roles
                gemini_role = "model" if role == "assistant" else "user"
                messages.append({"role": gemini_role, "parts": [text]})
        
        # Append the current user message
        messages.append({"role": "user", "parts": [user_text]})

        # Send all messages to Gemini so it has full context
        response = model.generate_content(messages)

        text: Optional[str] = getattr(response, "text", None)

        # Fallback: try to extract text from candidates if needed
        if not text:
            try:
                fragments = []
                for cand in getattr(response, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in getattr(content, "parts", []) or []:
                        part_text = getattr(part, "text", None)
                        if part_text:
                            fragments.append(part_text)
                if fragments:
                    text = "\n".join(fragments)
            except Exception:
                # If we cannot parse structured content, fall back to string repr
                text = str(response)

        if not text:
            raise RuntimeError("Gemini did not return any text content.")

        logger.info(
            "[GEMINI RESPONSE] Question: %s..., Answer: %s...",
            user_message[:120],
            text[:200],
        )

        return text
