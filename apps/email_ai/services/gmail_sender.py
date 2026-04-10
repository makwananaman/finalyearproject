import base64
import os
from email.message import EmailMessage

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .gmail_auth import GMAIL_SCOPES


def send_email(credential, to_address, subject, body):
    """
    Send an email with the Gmail API using the stored OAuth credentials.

    The sender service rebuilds Google credentials from the GmailCredential
    model, refreshes the token when needed, and sends a plain-text message to
    the target recipient.
    """
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Google OAuth credentials are not configured.")

    credentials = Credentials(
        token=credential.access_token,
        refresh_token=credential.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GMAIL_SCOPES,
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        credential.access_token = credentials.token
        credential.token_expiry = credentials.expiry
        if credentials.refresh_token:
            credential.refresh_token = credentials.refresh_token
        credential.save(update_fields=["access_token", "refresh_token", "token_expiry", "updated_at"])

    service = build("gmail", "v1", credentials=credentials)

    message = EmailMessage()
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
