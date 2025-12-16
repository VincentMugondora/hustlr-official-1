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
    HUSTLR_BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20240620-v1:0"
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
    # LLM output length (tokens) for Bedrock Claude responses
    LLM_MAX_TOKENS: int = 1500

    # Full WhatsApp-friendly User Policy text used when a user sends POLICY
    USER_POLICY_TEXT: str = (
        "Hustlr WhatsApp User Policy\n\n"
        "Effective Date: 15 December 2025\n\n"
        "Welcome to Hustlr. This User Policy explains how Hustlr operates on WhatsApp, what information we collect, and the rules for using our service. "
        "By messaging Hustlr on WhatsApp, you agree to this policy.\n\n"
        "1. What is Hustlr?\n\n"
        "Hustlr is a WhatsApp-based assistant that helps users connect with trusted local service providers such as plumbers, electricians, drivers, cleaners, and similar professionals.\n\n"
        "You can use Hustlr via:\n"
        "- WhatsApp text messages\n"
        "- Voice calls or voice notes\n\n"
        "No mobile app download is required.\n\n"
        "2. How Hustlr Works\n\n"
        "When you message Hustlr:\n"
        "1) You describe the service you need in natural language\n"
        "2) Hustlr asks a few follow-up questions (such as location, time, and budget)\n"
        "3) Hustlr recommends an available service provider\n"
        "4) With your confirmation, Hustlr completes the booking and sends reminders\n\n"
        "Hustlr acts as a facilitator, not the service provider.\n\n"
        "3. Information We Collect\n\n"
        "We only collect information necessary to provide the service.\n\n"
        "Automatically Collected:\n"
        "- Your WhatsApp phone number\n"
        "- Message timestamps\n\n"
        "Information You May Be Asked For:\n"
        "- First name\n"
        "- Location of service\n\n"
        "- Type of service requested\n"
        "- Preferred time and date\n"
        "- Budget range\n\n"
        "We do not require:\n"
        "- Email address\n"
        "- Passwords\n"
        "- National ID numbers\n"
        "- Date of birth\n\n"
        "4. How Your Information Is Used\n\n"
        "Your information is used to:\n"
        "- Match you with suitable service providers\n"
        "- Confirm and manage bookings\n"
        "- Send reminders and updates\n"
        "- Improve service quality\n\n"
        "We do not sell your personal information to third parties.\n\n"
        "5. Service Providers\n\n"
        "Service providers on Hustlr:\n"
        "- Register voluntarily\n"
        "- Provide their service details and availability\n"
        "- May be marked as verified after internal checks\n\n"
        "Hustlr does not guarantee the outcome of services provided. Any agreement or payment is strictly between you and the service provider unless explicitly stated otherwise.\n\n"
        "6. Payments\n\n"
        "- Payments are typically made directly to the service provider\n"
        "- Hustlr does not currently process payments unless clearly communicated\n"
        "- Prices are estimates and may vary depending on job complexity\n\n"
        "7. User Responsibilities\n\n"
        "By using Hustlr, you agree to:\n"
        "- Provide accurate information\n"
        "- Treat service providers respectfully\n"
        "- Use the service for lawful purposes only\n\n"
        "You must not:\n"
        "- Send abusive, harmful, or misleading messages\n"
        "- Make prank or fake bookings\n"
        "- Attempt to bypass or manipulate the system\n\n"
        "Accounts that violate these rules may be restricted or blocked.\n\n"
        "8. WhatsApp & Third-Party Services\n\n"
        "Hustlr operates using WhatsApp and third-party communication services.\n\n"
        "Your use of WhatsApp is also subject to:\n"
        "- WhatsApp’s Terms of Service\n"
        "- WhatsApp’s Privacy Policy\n\n"
        "Hustlr is not responsible for WhatsApp service interruptions.\n\n"
        "9. Message Frequency & Opt-Out\n\n"
        "- You will only receive messages related to your requests and bookings\n"
        "- You may stop receiving messages at any time by replying STOP\n"
        "- After opting out, Hustlr will no longer contact you unless you message again\n\n"
        "10. Data Security & Retention\n\n"
        "- We take reasonable measures to protect your information\n"
        "- Data is stored securely and retained only as long as necessary\n"
        "- You may request deletion of your data by messaging DELETE MY DATA\n\n"
        "11. Limitation of Liability\n\n"
        "Hustlr:\n"
        "- Is not responsible for the quality of services delivered\n"
        "- Is not liable for disputes between users and providers\n"
        "- Does not guarantee provider availability\n\n"
        "Use of Hustlr is at your own discretion.\n\n"
        "12. Policy Updates\n\n"
        "This policy may be updated from time to time. Continued use of Hustlr after updates means you accept the revised policy.\n\n"
        "13. Contact\n\n"
        "If you have questions or concerns, contact Hustlr by replying HELP on WhatsApp.\n\n"
        "Thank you for using Hustlr. Hustlr — Local services, made simple on WhatsApp."
    )

    ADMIN_WHATSAPP_NUMBERS: list[str] = [
        "+263783961640",
        "+263775251636",
        "+263777530322",
        "+16509965727",
    ]


settings = Settings()
