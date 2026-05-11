from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str
    db_min_connections: int = 2
    db_max_connections: int = 10

    # Gemini
    gemini_api_key: str
    gemini_model: str = "gemini-2.0-flash"
    agent_max_rounds: int = 5
    agent_max_tokens: int = 1024

    # Twilio master credentials (platform fallback)
    # Per-business credentials are loaded from the DB at runtime.
    master_twilio_account_sid: str = ""
    master_twilio_auth_token: str = ""

    # App
    base_url: str = "https://formalert.in"
    environment: str = "production"
    log_level: str = "INFO"

    # Observability
    otlp_endpoint: str = "http://otel-collector:4318"
    service_name: str = "booking-agent"


settings = Settings()
