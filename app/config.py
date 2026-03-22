import json
import logging
import os
from pathlib import Path

from pydantic_settings import BaseSettings
from typing import List, Optional

log = logging.getLogger(__name__)

# load .env early so AWS_SECRET_NAME is available before Secrets Manager fetch
_env_file = Path(__file__).resolve().parent.parent / ".env"
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _load_aws_secrets() -> dict:
    """Pull config from AWS Secrets Manager. Returns {} on failure (local dev)."""
    secret_name = os.environ.get("AWS_SECRET_NAME")
    if not secret_name:
        return {}
    try:
        import boto3
        region = os.environ.get("AWS_REGION", "us-east-1")
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_name)
        return json.loads(resp["SecretString"])
    except Exception as e:
        log.warning("Secrets Manager unavailable, falling back to .env: %s", e)
        return {}


# inject secrets into env vars before pydantic reads them
_secrets = _load_aws_secrets()
for key, val in _secrets.items():
    os.environ.setdefault(key, val)


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
    budget_base_usd: int = 45
    moderate_base_usd: int = 105
    comfortable_base_usd: int = 225
    luxury_base_usd: int = 400

    # Redis / ElastiCache
    redis_url: str = "redis://localhost:6379/0"

    # Stage 2: Data Acquisition
    serpapi_key: str = ""
    openrouteservice_api_key: str = ""
    unsplash_access_key: str = ""
    mapbox_access_token: str = ""

    class Config:
        # AWS_SECRET_NAME in env (written by deploy) triggers Secrets Manager loading above.
        # No .env file needed — all config comes from Secrets Manager or env vars.
        extra = "ignore"


settings = Settings()
