"""
Supabase client initialization for Django
"""
import os
from supabase import create_client, Client

def get_supabase_client(access_token: str = "", refresh_token: str = "") -> Client:
    """
    Initialize and return Supabase client.
    Uses environment variables SUPABASE_URL and SUPABASE_KEY from .env.local
    """
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY")
    
    if not supabase_url or not supabase_key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env.local")
    
    client = create_client(supabase_url, supabase_key)
    if access_token:
        client.auth.set_session(access_token, refresh_token)
    return client
