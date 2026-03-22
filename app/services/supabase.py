"""Lazy-initialised Supabase client (service-role for server-side CRUD)."""

import logging

from supabase import create_client, Client

from app.config import settings

log = logging.getLogger(__name__)

_client: Client | None = None


def get_supabase() -> Client:
    global _client
    if _client is None:
        if not settings.supabase_url or not settings.supabase_service_key:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")
        _client = create_client(settings.supabase_url, settings.supabase_service_key)
        log.info("Supabase client initialised")
    return _client
