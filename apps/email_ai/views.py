from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseServerError
from django.shortcuts import redirect, render
from django.urls import reverse
from googleapiclient.errors import HttpError

from .models import GmailCredential
from .services.gmail_auth import exchange_code_for_tokens, get_authorization_url
from .services.gmail_reader import fetch_recent_emails, get_gmail_service

def email_dashboard(request):
    # Email AI dashboard
    return render(request, 'email_ai/dashboard.html')


@login_required
def connect_gmail(request):
    """
    Start the Gmail OAuth flow and redirect the user to Google consent.

    The generated OAuth state is stored in the session so the callback can
    verify that the response belongs to the same authenticated browser flow.
    """
    try:
        redirect_uri = request.build_absolute_uri(reverse("gmail_callback"))
        authorization_url, state, code_verifier = get_authorization_url(redirect_uri)
    except ValueError as exc:
        return HttpResponseServerError(str(exc))

    request.session["gmail_oauth_state"] = state
    request.session["gmail_oauth_code_verifier"] = code_verifier
    return redirect(authorization_url)


@login_required
def gmail_callback(request):
    """
    Complete the Gmail OAuth flow and persist the user's tokens.

    This view validates the OAuth state, exchanges the authorization code for
    tokens, and stores the latest token set on the user's GmailCredential row.
    """
    returned_state = request.GET.get("state")
    stored_state = request.session.get("gmail_oauth_state")
    code_verifier = request.session.get("gmail_oauth_code_verifier")
    code = request.GET.get("code")
    oauth_error = request.GET.get("error")

    if oauth_error:
        return HttpResponseBadRequest(f"Google OAuth failed: {oauth_error}")

    if not code:
        return HttpResponseBadRequest("Missing authorization code.")

    if not stored_state or returned_state != stored_state:
        return HttpResponseBadRequest("Invalid OAuth state.")

    if not code_verifier:
        return HttpResponseBadRequest("Missing OAuth code verifier.")

    try:
        redirect_uri = request.build_absolute_uri(reverse("gmail_callback"))
        credentials = exchange_code_for_tokens(
            code=code,
            redirect_uri=redirect_uri,
            state=stored_state,
            code_verifier=code_verifier,
        )
    except ValueError as exc:
        return HttpResponseServerError(str(exc))
    finally:
        request.session.pop("gmail_oauth_state", None)
        request.session.pop("gmail_oauth_code_verifier", None)

    existing_credential = GmailCredential.objects.filter(user=request.user).first()
    refresh_token = credentials.refresh_token
    if not refresh_token and existing_credential:
        refresh_token = existing_credential.refresh_token

    GmailCredential.objects.update_or_create(
        user=request.user,
        defaults={
            "access_token": credentials.token,
            "refresh_token": refresh_token or "",
            "token_expiry": credentials.expiry,
        },
    )

    return redirect("email_dashboard")


@login_required
def fetch_emails_view(request):
    """
    Load the current user's Gmail credentials and fetch recent emails.

    This view keeps the Gmail API logic in the service layer. If the user has
    not connected Gmail yet, the dashboard is rendered with a helpful message
    instead of attempting an API call.
    """
    credential = GmailCredential.objects.filter(user=request.user).first()
    if not credential:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": "Connect your Gmail account before fetching emails."},
        )

    try:
        service = get_gmail_service(credential)
        emails = fetch_recent_emails(service)
    except ValueError as exc:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": str(exc)},
        )
    except HttpError as exc:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": f"Gmail API request failed: {exc}"},
        )

    return render(request, "email_ai/dashboard.html", {"emails": emails})
