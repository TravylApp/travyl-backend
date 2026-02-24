from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""
    
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    supabase_jwt_secret: str
    allowed_origins: List[str]
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
