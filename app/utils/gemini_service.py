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

    async def invoke_question_answerer(self, user_message: str, user_context: Optional[Dict[str, Any]] = None) -> str:
        """Invoke Gemini to generate a response for the user message.

        Runs the synchronous Gemini client in a threadpool so the FastAPI
        event loop is not blocked.
        """
        context = user_context or {}
        return await run_in_threadpool(self._invoke_sync, user_message, context)

    def _invoke_sync(self, user_message: str, user_context: Dict[str, Any]) -> str:
        # Build a system prompt similar to the Claude Bedrock prompt
        system_prompt = (
            "You are Hustlr, a WhatsApp assistant that helps users find and book local service providers "
            "like plumbers, electricians, carpenters, cleaners, and more.\n\n"
            "IMPORTANT GUIDELINES:\n"
            "1. RESPOND TO EVERYTHING: Answer all messages warmly and helpfully, even casual greetings or off-topic messages.\n"
            "2. GENTLY STEER TO BOOKING: In every response, naturally guide the conversation toward finding or booking a service.\n"
            "3. BE CONVERSATIONAL: Feel like a helpful friend, not a bot. Use natural language.\n"
            "4. PROVIDE GUIDANCE: Always give clear next steps for what the user can do.\n"
            "5. No emojis.\n"
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

        context_text = "\n".join(context_parts)
        if context_text:
            user_text = context_text + "\n" + f"User message: {user_message}"
        else:
            user_text = f"User message: {user_message}"

        model = genai.GenerativeModel(self.model_name)
        response = model.generate_content(
            [
                {"role": "system", "parts": [system_prompt]},
                {"role": "user", "parts": [user_text]},
            ]
        )

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
