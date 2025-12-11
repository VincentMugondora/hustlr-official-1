from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    MONGODB_URI: str = "mongodb://localhost:27017"
    MONGODB_DB_NAME: str = "hustlr"

    # WhatsApp Cloud API
    WHATSAPP_API_URL: str = ""
    WHATSAPP_ACCESS_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_BUSINESS_ACCOUNT_ID: str = ""

    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    USE_BEDROCK_INTENT: bool = True
    # Legacy field (may be populated by old env var BEDROCK_MODEL_ID). We won't rely on it.
    BEDROCK_MODEL_ID: str = ""
    # Canonical Bedrock model ID for Hustlr; use this going forward
    HUSTLR_BEDROCK_MODEL_ID: str = "anthropic.claude-sonnet-4-20250514-v1:0"
    # Optional: If provided, use this inference profile ARN/ID instead of model ID
    HUSTLR_BEDROCK_INFERENCE_PROFILE_ARN: str = ""
    AWS_LAMBDA_QUESTION_ANSWERER_FUNCTION_NAME: str = ""

    # Gemini (Google Generative AI) testing configuration
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_NAME: str = "gemini-1.5-flash"
    USE_GEMINI_INTENT: bool = False

    # Google Places API (for importing providers)
    GOOGLE_PLACES_API_KEY: str = ""

    # Bot response style
    USE_CONCISE_RESPONSES: bool = False
    # LLM-led conversation mode
    LLM_CONTROLLED_CONVERSATION: bool = True


settings = Settings()
