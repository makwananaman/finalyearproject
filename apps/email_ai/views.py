from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import redirect

from .services.gmail_auth import (
    exchange_code_for_tokens,
    get_authorization_url,
)


def connect_gmail(request):
    redirect_uri = settings.GOOGLE_REDIRECT_URI

    auth_url, state = get_authorization_url(redirect_uri)

    request.session["oauth_state"] = state

    return redirect(auth_url)


def gmail_callback(request):
    code = request.GET.get("code")
    state = request.GET.get("state")

    if not code:
        return JsonResponse({"error": "No code received"})

    credentials = exchange_code_for_tokens(
        code=code,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
        state=state,
    )

    # Save token in session (demo-safe)
    request.session["gmail_token"] = credentials.token

    return redirect("/email/")
