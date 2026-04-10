import os

from google_auth_oauthlib.flow import Flow


GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_oauth_flow(redirect_uri=None, state=None):
    """
    Build and return the Google OAuth flow used for Gmail authorization.

    The flow is created from environment variables so secrets stay outside
    the codebase. The optional redirect URI and state are injected by the
    caller because they depend on the current request lifecycle.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

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
    """
    Generate the Google consent-screen URL, state token, and PKCE verifier.

    The returned state and code verifier must be saved by the caller and reused
    during the callback. Google expects the same verifier during token exchange
    when PKCE is enabled for the authorization request.
    """
    flow = get_oauth_flow(redirect_uri=redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return authorization_url, state, flow.code_verifier


def exchange_code_for_tokens(code, redirect_uri, state=None, code_verifier=None):
    """
    Exchange the callback authorization code for Google access tokens.

    The caller provides the redirect URI used during authorization so Google
    can validate the request. The original PKCE code verifier must also be
    provided so the token request matches the earlier authorization request.
    The returned credentials object contains the access token, refresh token,
    and expiry metadata needed for persistence.
    """
    flow = get_oauth_flow(redirect_uri=redirect_uri, state=state)
    flow.code_verifier = code_verifier
    try:
        flow.fetch_token(code=code)
    except Exception as e:
        raise ValueError(f"Token exchange failed: {str(e)}")

    return flow.credentials
