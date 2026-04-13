from email.utils import parseaddr

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseServerError, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from googleapiclient.errors import HttpError

from apps.ai_engine.email_ai_engine import (
    detect_intent,
    generate_email_search_query,
    process_user_query,
    should_reuse_existing_draft_context,
    should_reuse_existing_email_context,
)

from .models import GmailCredential
from .services.gmail_auth import exchange_code_for_tokens, get_authorization_url
from .services.gmail_reader import fetch_emails_by_query, fetch_recent_emails, get_gmail_service
from .services.gmail_sender import send_email


CHAT_TURNS_SESSION_KEY = "email_chat_messages"
CHAT_TURNS_LIMIT = 20
CHAT_SESSION_EXPIRY_SECONDS = 60 * 60 * 4
MODEL_CHAT_CONTEXT_LIMIT = 6


def _get_chat_turns(request):
    """
    Return the transient chat turns stored in the user's session.

    These turns exist only for UI continuity. They are not reused as full
    conversation context for the model.
    """
    turns = request.session.get(CHAT_TURNS_SESSION_KEY, [])
    return turns if isinstance(turns, list) else []


def _save_chat_turns(request, turns):
    """
    Persist a capped list of display-only chat turns in the session.

    The cap avoids unbounded session growth while still preserving a short,
    useful visible history for the user.
    """
    request.session[CHAT_TURNS_SESSION_KEY] = turns[-CHAT_TURNS_LIMIT:]
    request.session.set_expiry(CHAT_SESSION_EXPIRY_SECONDS)


def _append_chat_turn(request, role, content):
    """
    Append one user or assistant turn to the session-backed chat list.

    The stored turns are used only to render the chat message list.
    """
    turns = _get_chat_turns(request)
    turns.append({"role": role, "content": content})
    _save_chat_turns(request, turns)


def _get_model_chat_context(request):
    """
    Return a short slice of the current chat for in-chat memory only.

    This is scoped to the active chat session and is cleared by `New Chat` or
    session expiry. It is intentionally capped to avoid bloating prompts.
    """
    return _get_chat_turns(request)[-MODEL_CHAT_CONTEXT_LIMIT:]


def _clear_chat_state(request):
    """
    Remove all transient chat-related session state for a fresh conversation.

    This resets the visible message list, the last email context, and any
    composed draft state without affecting the user's authentication session.
    """
    request.session.pop(CHAT_TURNS_SESSION_KEY, None)
    request.session.pop("email_chat_latest_email", None)
    request.session.pop("email_chat_composed_email", None)
    request.session.pop("email_chat_requires_action", None)
    request.session.pop("email_chat_success_message", None)
    request.session.pop("email_chat_error_message", None)
    request.session.pop("email_chat_draft_text", None)


def _draft_modal_context(request, body_fallback: str = "") -> dict[str, str]:
    """
    Build To / Subject / Body for the draft modal from session-backed state.

    Composed new-email flows store structured fields; reply drafts rely on the
    latest Gmail context plus the generated body text.
    """
    body_fallback = (body_fallback or "").strip()
    composed = request.session.get("email_chat_composed_email") or {}
    latest = request.session.get("email_chat_latest_email") or {}

    if composed:
        return {
            "draft_to": str(composed.get("to", "")).strip(),
            "draft_subject": str(composed.get("subject", "")).strip(),
            "draft_body": str(composed.get("body", "")).strip() or body_fallback,
        }

    if latest:
        recipient = parseaddr(latest.get("sender", ""))[1]
        subject = str(latest.get("subject", "")).strip()
        if subject and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        elif not subject:
            subject = "Re: Your email"
        return {
            "draft_to": recipient,
            "draft_subject": subject,
            "draft_body": body_fallback,
        }

    return {"draft_to": "", "draft_subject": "", "draft_body": body_fallback}


def _is_ajax_request(request):
    """
    Detect whether the frontend expects an incremental JSON response.

    The dashboard JavaScript uses this to append only the new message turn
    instead of reloading the whole page.
    """
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _chat_response(request, payload, status=200):
    """
    Return JSON for AJAX chat requests or render the dashboard otherwise.

    This keeps the chat endpoint usable both with JavaScript enabled and as a
    normal form post fallback.
    """
    if _is_ajax_request(request):
        ajax_payload = dict(payload)
        if ajax_payload.get("requires_action"):
            ajax_payload.update(
                _draft_modal_context(request, ajax_payload.get("draft_text", ""))
            )
        return JsonResponse(ajax_payload, status=status)

    requires = bool(payload.get("requires_action", False))
    draft_text_val = payload.get("draft_text", "")
    draft_ctx = (
        _draft_modal_context(request, draft_text_val)
        if requires
        else {"draft_to": "", "draft_subject": "", "draft_body": ""}
    )
    context = {
        "chat_turns": _get_chat_turns(request),
        "requires_action": requires,
        "draft_to": draft_ctx["draft_to"],
        "draft_subject": draft_ctx["draft_subject"],
        "draft_body": draft_ctx["draft_body"],
        "success_message": payload.get("success_message", ""),
        "error_message": payload.get("error_message", ""),
    }
    return render(request, "email_ai/dashboard.html", context, status=status)


def _format_email_results_for_chat(emails):
    """
    Convert fetched Gmail results into one assistant chat message.

    This keeps the interface chat-only, so retrieval results appear as a
    normal assistant response rather than a separate email panel.
    """
    if not emails:
        return "No matching emails found."

    lines = ["I found these emails:"]
    for index, email in enumerate(emails, start=1):
        sender = email.get("sender", "Unknown sender")
        subject = email.get("subject", "No subject")
        snippet = email.get("snippet", "")
        lines.append(f"{index}. From: {sender}")
        lines.append(f"   Subject: {subject}")
        if snippet:
            lines.append(f"   Snippet: {snippet}")

    return "\n".join(lines)


def email_dashboard(request):
    """
    Render the Email AI dashboard with display-only chat state.

    The dashboard itself does not perform Gmail or AI work. It only displays
    chat turns, current draft state, and transient success or error messages.
    """
    requires_action = bool(request.session.pop("email_chat_requires_action", False))
    draft_stored = request.session.pop("email_chat_draft_text", "")
    draft_ctx = (
        _draft_modal_context(request, draft_stored)
        if requires_action
        else {"draft_to": "", "draft_subject": "", "draft_body": ""}
    )
    context = {
        "chat_turns": _get_chat_turns(request),
        "requires_action": requires_action,
        "draft_to": draft_ctx["draft_to"],
        "draft_subject": draft_ctx["draft_subject"],
        "draft_body": draft_ctx["draft_body"],
        "success_message": request.session.pop("email_chat_success_message", ""),
        "error_message": request.session.pop("email_chat_error_message", ""),
    }
    return render(request, "email_ai/dashboard.html", context)


@login_required
def new_chat_view(request):
    """
    Start a brand-new chat by clearing the current session-backed chat state.

    This gives the user an explicit reset point so one conversation does not
    continue indefinitely across requests or server restarts.
    """
    if request.method != "POST":
        return redirect("email_dashboard")

    _clear_chat_state(request)

    if _is_ajax_request(request):
        return JsonResponse({"success": True})

    return redirect("email_dashboard")


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
    Fetch recent emails and show them as a normal dashboard render.

    This view remains available as a simple manual utility outside the chat
    workflow.
    """
    credential = GmailCredential.objects.filter(user=request.user).first()
    if not credential:
        return render(
            request,
            "email_ai/dashboard.html",
            {
                "chat_turns": _get_chat_turns(request),
                "error_message": "Connect your Gmail account before fetching emails.",
            },
        )

    try:
        service = get_gmail_service(credential)
        emails = fetch_recent_emails(service)
    except ValueError as exc:
        return render(
            request,
            "email_ai/dashboard.html",
            {"chat_turns": _get_chat_turns(request), "error_message": str(exc)},
        )
    except HttpError as exc:
        return render(
            request,
            "email_ai/dashboard.html",
            {
                "chat_turns": _get_chat_turns(request),
                "error_message": f"Gmail API request failed: {exc}",
            },
        )

    return render(
        request,
        "email_ai/dashboard.html",
        {
            "chat_turns": _get_chat_turns(request),
            "emails": emails,
        },
    )


@login_required
def email_chat_view(request):
    """
    Process one chat turn and append only the new assistant response.

    The model sees only the current prompt plus the resolved email context for
    this turn. The visible chat list is stored only for UI continuity.
    """
    if request.method != "POST":
        return redirect("email_dashboard")

    user_input = request.POST.get("user_input", "").strip()
    if not user_input:
        return _chat_response(
            request,
            {"error_message": "Enter a message before sending it to Email AI."},
            status=400,
        )

    prior_chat_context = _get_model_chat_context(request)
    _append_chat_turn(request, "user", user_input)

    intent = detect_intent(user_input, chat_context=prior_chat_context)
    search_query = generate_email_search_query(user_input)
    latest_email = request.session.get("email_chat_latest_email")
    composed_email = request.session.get("email_chat_composed_email") or {}
    reuse_existing_context = should_reuse_existing_email_context(
        user_input,
        has_existing_email_context=bool(latest_email),
    )
    reuse_existing_draft = should_reuse_existing_draft_context(
        user_input,
        has_existing_draft_context=bool(composed_email),
    )

    if reuse_existing_context and intent == "fetch_emails":
        intent = "question_answering"
    if reuse_existing_draft:
        intent = "compose_new_email"

    if intent == "conversation_intent":
        ai_result = process_user_query(
            user_input,
            chat_context=prior_chat_context,
            detected_intent=intent,
            search_query=search_query,
        )
        assistant_message = ai_result.get("response", "")
        request.session.pop("email_chat_latest_email", None)
        request.session.pop("email_chat_composed_email", None)
        request.session["email_chat_requires_action"] = False
        request.session["email_chat_draft_text"] = ""
        _append_chat_turn(request, "assistant", assistant_message)
        return _chat_response(
            request,
            {
                "assistant_turn": assistant_message,
                "intent": "conversation_intent",
                "requires_action": False,
                "draft_text": "",
            },
        )

    if intent == "fetch_emails":
        credential = GmailCredential.objects.filter(user=request.user).first()
        if not credential:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(
                request,
                {"error_message": "Gmail not connected."},
                status=400,
            )

        try:
            service = get_gmail_service(credential)
            emails = fetch_emails_by_query(service, search_query, max_results=5)
        except ValueError as exc:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(request, {"error_message": str(exc)}, status=400)
        except HttpError as exc:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(
                request,
                {"error_message": f"Gmail API request failed: {exc}"},
                status=400,
            )

        request.session.pop("email_chat_latest_email", None)
        request.session.pop("email_chat_composed_email", None)
        request.session["email_chat_requires_action"] = False
        request.session["email_chat_draft_text"] = ""

        assistant_message = _format_email_results_for_chat(emails)
        _append_chat_turn(request, "assistant", assistant_message)

        return _chat_response(
            request,
            {
                "assistant_turn": assistant_message,
                "intent": intent,
                "requires_action": False,
                "draft_text": "",
            },
        )

    if intent != "compose_new_email" and not reuse_existing_context:
        credential = GmailCredential.objects.filter(user=request.user).first()
        if not credential:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(
                request,
                {"error_message": "Gmail not connected."},
                status=400,
            )

        try:
            service = get_gmail_service(credential)
            emails = fetch_emails_by_query(service, search_query, max_results=5)
        except ValueError as exc:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(request, {"error_message": str(exc)}, status=400)
        except HttpError as exc:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(
                request,
                {"error_message": f"Gmail API request failed: {exc}"},
                status=400,
            )

        if not emails:
            turns = _get_chat_turns(request)[:-1]
            _save_chat_turns(request, turns)
            return _chat_response(
                request,
                {"error_message": "No matching emails found."},
                status=404,
            )

        latest_email = emails[0]

    ai_result = process_user_query(
        user_input,
        latest_email,
        composed_email=composed_email if reuse_existing_draft else None,
        chat_context=prior_chat_context,
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

    assistant_message = ai_result.get("response", "")
    request.session["email_chat_requires_action"] = ai_result.get("requires_action", False)
    request.session["email_chat_draft_text"] = assistant_message
    _append_chat_turn(request, "assistant", assistant_message)

    return _chat_response(
        request,
        {
            "assistant_turn": assistant_message,
            "intent": ai_result.get("intent", ""),
            "requires_action": ai_result.get("requires_action", False),
            "draft_text": assistant_message,
        },
    )


@login_required
def send_email_view(request):
    """
    Send the AI-generated draft through Gmail for the connected user.

    The send target comes either from a composed email draft or from the last
    email context currently kept for follow-up reply flows.
    """
    if request.method != "POST":
        return redirect("email_dashboard")

    draft_text = request.POST.get("draft_text", "").strip()
    if not draft_text:
        return _chat_response(
            request,
            {"error_message": "Draft text is required to send an email."},
            status=400,
        )

    credential = GmailCredential.objects.filter(user=request.user).first()
    if not credential:
        return _chat_response(
            request,
            {"error_message": "Gmail not connected."},
            status=400,
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
        return _chat_response(
            request,
            {"error_message": "No email context available for sending."},
            status=400,
        )

    if not recipient_address:
        return _chat_response(
            request,
            {"error_message": "Could not determine the recipient email address."},
            status=400,
        )

    try:
        send_email(credential, recipient_address, subject, draft_text)
    except ValueError as exc:
        return _chat_response(request, {"error_message": str(exc)}, status=400)
    except HttpError as exc:
        return _chat_response(
            request,
            {
                "error_message": (
                    f"Email sending failed: {exc}. "
                    "If you connected Gmail before send access was added, reconnect Gmail and try again."
                )
            },
            status=400,
        )

    success_message = "Email sent successfully."
    request.session["email_chat_success_message"] = success_message
    request.session["email_chat_draft_text"] = ""
    request.session["email_chat_requires_action"] = False
    request.session.pop("email_chat_composed_email", None)

    return _chat_response(
        request,
        {
            "success_message": success_message,
            "requires_action": False,
            "draft_text": "",
        },
    )
