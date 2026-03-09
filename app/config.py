from pydantic_settings import BaseSettings
from typing import List, Optional


class Settings(BaseSettings):

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_jwt_secret: str = ""
    allowed_origins: List[str] = ["http://localhost:3000"]

    # AWS / Bedrock
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    aws_bearer_token: Optional[str] = None

    # Budget tier base rates (USD/day before cost-of-living adjustment)
    budget_base_usd: int
    moderate_base_usd: int
    comfortable_base_usd: int
    luxury_base_usd: int

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


settings = Settings()
