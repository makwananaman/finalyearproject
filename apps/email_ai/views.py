from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseServerError
from django.shortcuts import redirect, render
from django.urls import reverse
from email.utils import parseaddr
from googleapiclient.errors import HttpError

from apps.ai_engine.email_ai_engine import (
    detect_intent,
    generate_email_search_query,
    process_user_query,
    should_reuse_existing_email_context,
)
from .models import GmailCredential
from .services.gmail_auth import exchange_code_for_tokens, get_authorization_url
from .services.gmail_reader import (
    fetch_emails_by_query,
    fetch_recent_emails,
    get_gmail_service,
)
from .services.gmail_sender import send_email

def email_dashboard(request):
    """
    Render the Email AI dashboard and any temporary UI state kept in session.

    The dashboard itself does not perform Gmail or AI work. It only displays
    data produced by the dedicated action views.
    """
    context = {
        "user_input": request.session.pop("email_chat_user_input", ""),
        "ai_response": request.session.pop("email_chat_ai_response", ""),
        "intent": request.session.pop("email_chat_intent", ""),
        "requires_action": request.session.pop("email_chat_requires_action", False),
        "success_message": request.session.pop("email_chat_success_message", ""),
        "error_message": request.session.pop("email_chat_error_message", ""),
        "draft_text": request.session.pop("email_chat_draft_text", ""),
        "latest_email": request.session.get("email_chat_latest_email"),
    }
    return render(request, "email_ai/dashboard.html", context)


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


@login_required
def email_chat_view(request):
    """
    Route the user prompt to Email AI and fetch Gmail context only when needed.

    The view detects intent first. New-email composition skips Gmail fetching,
    while summarize/question/reply requests load the latest email before
    delegating reasoning to the AI engine.
    """
    if request.method != "POST":
        return redirect("email_dashboard")

    user_input = request.POST.get("user_input", "").strip()
    if not user_input:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": "Enter a message before sending it to Email AI."},
        )

    intent = detect_intent(user_input)
    search_query = generate_email_search_query(user_input)
    latest_email = request.session.get("email_chat_latest_email")
    emails = None
    reuse_existing_context = should_reuse_existing_email_context(
        user_input,
        has_existing_email_context=bool(latest_email),
    )

    if intent == "fetch_emails":
        credential = GmailCredential.objects.filter(user=request.user).first()
        if not credential:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": "Gmail not connected.", "user_input": user_input},
            )

        try:
            service = get_gmail_service(credential)
            emails = fetch_emails_by_query(service, search_query, max_results=5)
        except ValueError as exc:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": str(exc), "user_input": user_input},
            )
        except HttpError as exc:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": f"Gmail API request failed: {exc}", "user_input": user_input},
            )

        request.session.pop("email_chat_latest_email", None)
        request.session.pop("email_chat_composed_email", None)
        request.session["email_chat_user_input"] = user_input
        request.session["email_chat_ai_response"] = ""
        request.session["email_chat_intent"] = intent
        request.session["email_chat_requires_action"] = False
        request.session["email_chat_draft_text"] = ""

        return render(
            request,
            "email_ai/dashboard.html",
            {
                "user_input": user_input,
                "intent": intent,
                "requires_action": False,
                "emails": emails,
            },
        )

    if intent != "compose_new_email" and not reuse_existing_context:
        credential = GmailCredential.objects.filter(user=request.user).first()
        if not credential:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": "Gmail not connected.", "user_input": user_input},
            )

        try:
            service = get_gmail_service(credential)
            emails = fetch_emails_by_query(service, search_query, max_results=5)
        except ValueError as exc:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": str(exc), "user_input": user_input},
            )
        except HttpError as exc:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": f"Gmail API request failed: {exc}", "user_input": user_input},
            )

        if not emails:
            return render(
                request,
                "email_ai/dashboard.html",
                {"error_message": "No matching emails found.", "user_input": user_input},
            )

        latest_email = emails[0]

    ai_result = process_user_query(
        user_input,
        latest_email,
        detected_intent=intent,
        search_query=search_query,
    )

    if latest_email:
        request.session["email_chat_latest_email"] = latest_email
    else:
        request.session.pop("email_chat_latest_email", None)

    if ai_result.get("intent") == "compose_new_email":
        request.session["email_chat_composed_email"] = ai_result.get("email_data", {})
    else:
        request.session.pop("email_chat_composed_email", None)

    request.session["email_chat_user_input"] = user_input
    request.session["email_chat_ai_response"] = ai_result.get("response", "")
    request.session["email_chat_intent"] = ai_result.get("intent", "")
    request.session["email_chat_requires_action"] = ai_result.get("requires_action", False)
    request.session["email_chat_draft_text"] = ai_result.get("response", "")

    return render(
        request,
        "email_ai/dashboard.html",
        {
            "user_input": user_input,
            "ai_response": ai_result.get("response", ""),
            "intent": ai_result.get("intent", ""),
            "requires_action": ai_result.get("requires_action", False),
            "draft_text": ai_result.get("response", ""),
            "latest_email": latest_email,
        },
    )


@login_required
def send_email_view(request):
    """
    Send the AI-generated draft through Gmail for the connected user.

    The draft text comes from the form, while the latest email metadata is read
    from session so the reply can be addressed to the sender of the email that
    was just analyzed in the chat flow.
    """
    if request.method != "POST":
        return redirect("email_dashboard")

    draft_text = request.POST.get("draft_text", "").strip()
    if not draft_text:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": "Draft text is required to send an email."},
        )

    credential = GmailCredential.objects.filter(user=request.user).first()
    if not credential:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": "Gmail not connected.", "draft_text": draft_text},
        )

    composed_email = request.session.get("email_chat_composed_email") or {}
    latest_email = request.session.get("email_chat_latest_email")

    if composed_email:
        recipient_address = composed_email.get("to", "").strip()
        subject = composed_email.get("subject", "").strip() or "New Email"
    elif latest_email:
        recipient_address = parseaddr(latest_email.get("sender", ""))[1]
        subject = latest_email.get("subject", "").strip()
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        elif not subject:
            subject = "Re: Your email"
    else:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": "No email context available for sending.", "draft_text": draft_text},
        )

    if not recipient_address:
        return render(
            request,
            "email_ai/dashboard.html",
            {
                "error_message": "Could not determine the recipient email address.",
                "draft_text": draft_text,
            },
        )

    try:
        send_email(credential, recipient_address, subject, draft_text)
    except ValueError as exc:
        return render(
            request,
            "email_ai/dashboard.html",
            {"error_message": str(exc), "draft_text": draft_text},
        )
    except HttpError as exc:
        return render(
            request,
            "email_ai/dashboard.html",
            {
                "error_message": (
                    f"Email sending failed: {exc}. "
                    "If you connected Gmail before send access was added, reconnect Gmail and try again."
                ),
                "draft_text": draft_text,
            },
        )

    request.session["email_chat_success_message"] = "Email sent successfully."
    request.session["email_chat_draft_text"] = ""
    request.session["email_chat_requires_action"] = False
    request.session.pop("email_chat_composed_email", None)
    return redirect("email_dashboard")
