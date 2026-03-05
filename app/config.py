from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""
    
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    supabase_jwt_secret: str
    allowed_origins: List[str]
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
