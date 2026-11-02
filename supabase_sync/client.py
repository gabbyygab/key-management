"""A small Supabase REST (PostgREST) client built on urllib.

No third-party dependency on purpose: the project adds packages only with the
owner's approval, and the mirror needs just three calls (upsert, probe,
select). If you later prefer the official `supabase` package, replace this
class; services.py only uses the methods below.
"""
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .conf import get_config

RETRY_STATUSES = {429, 500, 502, 503, 504}


class SupabaseError(Exception):
    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code

    @property
    def table_missing(self):
        # PGRST205: table not in the schema cache; 42P01: undefined_table.
        return self.code in {"PGRST205", "42P01"} or self.status == 404


class SupabaseClient:
    def __init__(self, config=None, *, retries=2, backoff=0.5):
        self.config = config or get_config()
        problems = self.config.problems()
        if problems:
            raise SupabaseError("Supabase is not configured: " + " ".join(problems))
        self.base = f"{self.config.url}/rest/v1"
        self.retries = retries
        self.backoff = backoff

    # -- public API ---------------------------------------------------------

    def upsert(self, table, rows, *, on_conflict="id"):
        """Insert-or-update rows by primary key. Returns the row count sent."""
        if not rows:
            return 0
        self._request("POST", table, params={"on_conflict": on_conflict}, body=rows,
                      prefer="resolution=merge-duplicates,return=minimal")
        return len(rows)

    def select(self, table, *, columns="*", limit=None, **filters):
        """GET rows. Filters use PostgREST syntax, e.g. status="eq.AVAILABLE"."""
        params = {"select": columns, **filters}
        if limit is not None:
            params["limit"] = limit
        return self._request("GET", table, params=params) or []

    def probe(self, table):
        """True if the table exists and the key can read it; raises otherwise."""
        self.select(table, columns="id", limit=1)
        return True

    # -- internals ----------------------------------------------------------

    def _headers(self, method, prefer=None):
        key = self.config.write_key
        headers = {"apikey": key, "Accept": "application/json"}
        # Legacy keys (service_role) are JWTs and go in Authorization too. The
        # newer sb_secret_... keys are not JWTs: the gateway accepts them in
        # `apikey` alone and mints the JWT itself.
        if key.startswith("eyJ"):
            headers["Authorization"] = f"Bearer {key}"
        if method != "GET":
            headers["Content-Type"] = "application/json"
        if self.config.schema != "public":
            headers["Accept-Profile"] = self.config.schema
            headers["Content-Profile"] = self.config.schema
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(self, method, table, *, params=None, body=None, prefer=None):
        url = f"{self.base}/{quote(table)}"
        if params:
            url += "?" + urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        attempt = 0
        while True:
            req = Request(url, data=data, method=method, headers=self._headers(method, prefer))
            try:
                with urlopen(req, timeout=self.config.timeout) as resp:  # noqa: S310 (scheme checked in conf)
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except HTTPError as e:
                err = _error_from_http(e)
                if e.code in RETRY_STATUSES and attempt < self.retries:
                    attempt += 1
                    time.sleep(self.backoff * attempt)
                    continue
                raise err from None
            except (URLError, TimeoutError, OSError) as e:
                if attempt < self.retries:
                    attempt += 1
                    time.sleep(self.backoff * attempt)
                    continue
                reason = getattr(e, "reason", e)
                raise SupabaseError(f"Could not reach Supabase: {reason}") from None


def _error_from_http(e):
    code = None
    message = f"HTTP {e.code}"
    try:
        payload = json.loads(e.read() or b"{}")
        code = payload.get("code")
        message = payload.get("message") or payload.get("error") or message
        if payload.get("hint"):
            message += f" (hint: {payload['hint']})"
    except (ValueError, AttributeError):
        pass
    return SupabaseError(message, status=e.code, code=code)
