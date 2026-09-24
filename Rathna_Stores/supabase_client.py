"""
Supabase client initialization for Django
"""
import os
from threading import Lock

from supabase import Client, ClientOptions, create_client


# Most page reads use the publishable/anonymous client. Constructing a new
# Supabase client for every query throws away httpx's connection pool, which
# makes one page render pay for several fresh TCP/TLS connections. Keep one
# anonymous client per configured project. Clients with a customer token are
# intentionally not shared because their Authorization header is mutable.
_anonymous_client: Client | None = None
_anonymous_client_config: tuple[str, str] | None = None
_anonymous_client_lock = Lock()


def _new_client(supabase_url: str, supabase_key: str) -> Client:
    """Create a client with bounded timeouts for interactive page requests."""
    # Interactive page requests should fail promptly when the remote project
    # is unavailable instead of holding the whole Django response open.
    timeout = float(os.getenv("SUPABASE_REQUEST_TIMEOUT_SECONDS", "6"))
    options = ClientOptions(
        postgrest_client_timeout=timeout,
        storage_client_timeout=int(timeout),
        function_client_timeout=min(timeout, 5),
    )
    return create_client(supabase_url, supabase_key, options)


def _get_anonymous_client(supabase_url: str, supabase_key: str) -> Client:
    """Return the reusable unauthenticated client and its HTTP connection pool."""
    global _anonymous_client, _anonymous_client_config
    config = (supabase_url, supabase_key)
    with _anonymous_client_lock:
        if _anonymous_client is None or _anonymous_client_config != config:
            _anonymous_client = _new_client(supabase_url, supabase_key)
            _anonymous_client_config = config
        return _anonymous_client

def get_supabase_client(access_token: str = "", refresh_token: str = "") -> Client:
    """
    Initialize and return Supabase client.
    Uses environment variables SUPABASE_URL and SUPABASE_KEY from .env.local
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env.local")
    
    if not access_token:
        return _get_anonymous_client(supabase_url, supabase_key)

    client = _new_client(supabase_url, supabase_key)
    auth_response = client.auth.set_session(access_token, refresh_token)
    session = getattr(auth_response, "session", None)
    bearer_token = getattr(session, "access_token", None) or access_token
    authorization = f"Bearer {bearer_token}"
    # set_session validates the token, but supabase-py copies headers into
    # each transport when it is created. Update those copies too.
    client.options.headers["Authorization"] = authorization
    client.storage._headers["Authorization"] = authorization
    client.storage._client.headers["Authorization"] = authorization
    client.postgrest.auth(bearer_token)
    return client
