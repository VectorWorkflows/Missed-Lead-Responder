# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PORT: int = 8000
    PUBLIC_BASE_URL: str
    
    TWILIO_ACCOUNT_SID: str
    TWILIO_AUTH_TOKEN: str
    
    MONGO_URI: str
    GOOGLE_SERVICE_ACCOUNT_JSON: str
    TELEGRAM_BOT_TOKEN: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()