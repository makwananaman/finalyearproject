import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def get_gmail_service(credential):
    """
    Build and return an authenticated Gmail API service for the user.

    The service is created from the tokens stored in the GmailCredential model.
    If the access token is expired and a refresh token is available, the token
    is refreshed and the updated values are saved back to the same model row.
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
        scopes=[GMAIL_READONLY_SCOPE],
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        credential.access_token = credentials.token
        credential.token_expiry = credentials.expiry
        if credentials.refresh_token:
            credential.refresh_token = credentials.refresh_token
        credential.save(update_fields=["access_token", "refresh_token", "token_expiry", "updated_at"])

    return build("gmail", "v1", credentials=credentials)


def fetch_recent_emails(service, max_results=5):
    """
    Fetch the latest emails from Gmail and return a simplified list.

    Each returned email contains only the sender, subject, and snippet so the
    view layer can render recent messages without persisting them in the local
    database.
    """
    response = service.users().messages().list(userId="me", maxResults=max_results).execute()
    messages = response.get("messages", [])
    emails = []

    for message in messages:
        message_data = service.users().messages().get(
            userId="me",
            id=message["id"],
            format="metadata",
            metadataHeaders=["From", "Subject"],
        ).execute()

        headers = message_data.get("payload", {}).get("headers", [])
        sender = ""
        subject = ""

        for header in headers:
            name = header.get("name", "")
            if name == "From":
                sender = header.get("value", "")
            elif name == "Subject":
                subject = header.get("value", "")

        emails.append(
            {
                "sender": sender,
                "subject": subject,
                "snippet": message_data.get("snippet", ""),
            }
        )

    return emails
