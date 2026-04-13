import os

from django.conf import settings
from google_auth_oauthlib.flow import Flow

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]


def get_oauth_flow(redirect_uri=None, state=None):
    client_id = settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_CLIENT_SECRET

    if not client_id or not client_secret:
        raise ValueError("Google OAuth credentials are not configured.")

    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }

    flow = Flow.from_client_config(client_config, scopes=GMAIL_SCOPES, state=state)

    if redirect_uri:
        flow.redirect_uri = redirect_uri

    return flow


def get_authorization_url(redirect_uri):
    flow = get_oauth_flow(redirect_uri=redirect_uri)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    return authorization_url, state


def exchange_code_for_tokens(code, redirect_uri, state=None):
    flow = get_oauth_flow(redirect_uri=redirect_uri, state=state)
    flow.fetch_token(code=code)
    return flow.credentials
