"""Reads the SUPABASE_* settings (config/settings.py, sourced from .env)."""
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings


@dataclass(frozen=True)
class SupabaseConfig:
    url: str
    anon_key: str
    service_role_key: str
    schema: str
    timeout: int
    enabled: bool

    @property
    def write_key(self):
        """Writes need the service-role key: the mirrored tables have RLS on
        and no policies, so the anon key can neither read nor write them."""
        return self.service_role_key

    @property
    def is_configured(self):
        return bool(self.url and self.write_key)

    def problems(self):
        """Human-readable reasons the integration cannot run yet."""
        out = []
        if not self.url:
            out.append("SUPABASE_URL is not set.")
        else:
            parsed = urlparse(self.url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                out.append("SUPABASE_URL must look like https://<project-ref>.supabase.co")
            elif parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1"}:
                out.append("SUPABASE_URL must use https (plain http is only allowed for a "
                           "local Supabase on localhost).")
        if not self.service_role_key:
            out.append("SUPABASE_SERVICE_ROLE_KEY is not set.")
        return out


def get_config():
    return SupabaseConfig(
        url=(getattr(settings, "SUPABASE_URL", "") or "").rstrip("/"),
        anon_key=getattr(settings, "SUPABASE_ANON_KEY", "") or "",
        service_role_key=getattr(settings, "SUPABASE_SERVICE_ROLE_KEY", "") or "",
        schema=getattr(settings, "SUPABASE_SCHEMA", "public") or "public",
        timeout=int(getattr(settings, "SUPABASE_TIMEOUT", 10) or 10),
        enabled=bool(getattr(settings, "SUPABASE_SYNC_ENABLED", False)),
    )
