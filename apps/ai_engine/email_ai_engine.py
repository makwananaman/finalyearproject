"""LLM logic for email intent detection and response generation."""

from __future__ import annotations

import json
import re
from typing import Any

from .groq_client import get_groq_client

GROQ_MODEL = "llama-3.1-8b-instant"
SUPPORTED_INTENTS = {
    "summarize",
    "question_answering",
    "draft_reply",
    "compose_new_email",
    "fetch_emails",
    "conversation_intent",
}


def _call_groq(system_prompt: str, user_prompt: str) -> str:
    """
    Send a prompt to Groq and return the raw model output.

    This helper centralizes the Groq chat-completion call so intent detection,
    query generation, and response generation all use the same model interface.
    """
    client = get_groq_client()
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
    except Exception:
        return ""

    return response.choices[0].message.content.strip()


def _build_email_context(latest_email: dict[str, Any]) -> str:
    """
    Format one email into a reusable plain-text prompt context block.

    The AI engine receives lightweight metadata from the Gmail layer, so this
    helper converts that metadata into a stable text representation.
    """
    subject = str(latest_email.get("subject", "")).strip()
    sender = str(latest_email.get("sender", "")).strip()
    snippet = str(latest_email.get("snippet", "")).strip()

    return (
        f"Subject: {subject or 'N/A'}\n"
        f"Sender: {sender or 'N/A'}\n"
        f"Snippet: {snippet or 'N/A'}"
    )


def _build_chat_context(chat_context: list[dict[str, str]] | None) -> str:
    """
    Convert recent in-chat turns into a compact plain-text context block.

    This is short-lived working memory for the current chat only. It is not
    permanent history and should be limited to the active conversation.
    """
    if not chat_context:
        return ""

    lines: list[str] = []
    for turn in chat_context:
        role = "User" if turn.get("role") == "user" else "Assistant"
        content = str(turn.get("content", "")).strip()
        if not content:
            continue
        lines.append(f"{role}: {content}")

    return "\n".join(lines)


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    """
    Parse a JSON object from model output and return an empty dict on failure.

    The model may include extra wrapper text, so this helper extracts the
    first object-shaped block before attempting JSON parsing.
    """
    if not raw_text:
        return {}

    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if match:
        raw_text = match.group(0)

    try:
        parsed = json.loads(raw_text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def detect_intent(
    user_input: str, chat_context: list[dict[str, str]] | None = None
) -> str:
    """
    Detect the user's email-related intent using only the user message.

    The caller uses this result to decide whether to compose a new email,
    fetch relevant emails, or route an existing email into summarization,
    question answering, or reply drafting.
    """
    raw_response = _call_groq(
        system_prompt=(
            "You are an email intent classifier.\n"
            "Return ONLY valid JSON in this exact shape:\n"
            '{"intent": "fetch_emails"}\n'
            "Valid intents are:\n"
            "- summarize: summarize an existing email\n"
            "- question_answering: answer a question about an existing email\n"
            "- draft_reply: draft a reply to an existing email\n"
            "- compose_new_email: create a brand-new email not based on the latest email\n"
            "- fetch_emails: fetch or list relevant emails\n\n"
            "- conversation_intent: normal conversation not requiring Gmail or email actions\n\n"
            "Rules:\n"
            "- If the user asks to list, show, fetch, get, or read emails without asking for analysis, return fetch_emails.\n"
            "- If the user asks to draw up a schedule, create a plan, or manage tasks without explicitly asking to find or search for emails, return conversation_intent.\n"
            "- If the user asks for a summary, brief understanding, explanation, or what an email means, return summarize.\n"
            "- If the user asks a specific question about an email, return question_answering.\n"
            "- If the user asks for a reply to an existing email, return draft_reply.\n"
            '- If the user mentions a specific email address, "send email to", or "compose email", '
            "return compose_new_email only when the request is about creating a brand-new email.\n"
            "- Mentioning a sender email address to identify an existing email does NOT mean compose_new_email.\n"
            "- If the request is general conversation and does not clearly require Gmail or an email workflow, return conversation_intent.\n"
            '- Questions like "do you know anything other than these emails" or other meta-conversation about the assistant should return conversation_intent, not fetch_emails.\n'
            "- Do NOT pretend to create integrations or sync with external services like Google Calendar.\n"
            "Do not return any explanation or extra text."
        ),
        user_prompt=(
            f"Recent chat context:\n{_build_chat_context(chat_context) or 'None'}\n\n"
            f"User request:\n{user_input}"
        ),
    )

    parsed_response = _extract_json_object(raw_response)
    intent = str(parsed_response.get("intent", "")).strip()
    if intent not in SUPPORTED_INTENTS:
        return "conversation_intent"
    return intent


def generate_email_search_query(user_input: str) -> str:
    """
    Convert a natural-language request into a Gmail search query string.

    The model returns only the Gmail-compatible query so the email app can
    retrieve relevant messages without hardcoding query construction in Python.
    """
    raw_response = _call_groq(
        system_prompt=(
            "You convert user requests into Gmail search queries.\n"
            "Return ONLY the Gmail query string.\n"
            "Do not return JSON, explanations, markdown, or quotes.\n\n"
            "Use valid Gmail search operators only.\n"
            "For exact dates, use after:YYYY/MM/DD before:YYYY/MM/DD.\n"
            "Do NOT use date: because Gmail search does not support that operator.\n"
            "When the user names a sender email address, prefer from:exact@email.com.\n"
            "If the request includes both sender and date, combine them in one query.\n\n"
            "Examples:\n"
            "emails from last week -> newer_than:7d\n"
            "email from professor -> from:professor\n"
            "emails about internship -> internship\n"
            "emails from HR last month -> from:hr newer_than:30d\n"
            "email from student@internshala.com on 2025-02-10 -> from:student@internshala.com after:2025/02/09 before:2025/02/11\n"
            "brief understanding of email sent by student@internshala.com on 10/02/2025 -> from:student@internshala.com after:2025/02/09 before:2025/02/11\n"
        ),
        user_prompt=(
            "Generate the most relevant Gmail search query for this user request:\n\n"
            f"{user_input}"
        ),
    )
    return raw_response.strip().strip('"').strip("'")


def should_reuse_existing_email_context(
    user_input: str,
    has_existing_email_context: bool,
) -> bool:
    """
    Decide whether the user is referring to the already selected email.

    This prevents follow-up questions such as "explain more about it" from
    triggering a brand-new Gmail search when the user clearly means the email
    that was just summarized or discussed.
    """
    if not has_existing_email_context:
        return False

    raw_response = _call_groq(
        system_prompt=(
            "You decide whether a user message refers to the currently selected email context.\n"
            "Return ONLY valid JSON in this exact shape:\n"
            '{"reuse_existing_email_context": true}\n'
            "Rules:\n"
            "- Return true when the message is a follow-up about the same email, such as "
            '"explain more about it", "tell me more", "what does that mean", '
            '"summarize it again", or similar pronoun-based follow-ups.\n'
            "- Return false when the user is asking to fetch, list, or identify a different email.\n"
            "- Return false when the user gives new sender, date, topic, or search constraints.\n"
            "Do not return any explanation or extra text."
        ),
        user_prompt=f"User request:\n{user_input}",
    )

    parsed_response = _extract_json_object(raw_response)
    return bool(parsed_response.get("reuse_existing_email_context", False))


def should_reuse_existing_draft_context(
    user_input: str,
    has_existing_draft_context: bool,
) -> bool:
    """
    Decide whether the user is refining the currently composed email draft.

    This keeps draft follow-ups such as "add this line", "make it longer", or
    "give me the whole email in one" attached to the current draft instead of
    misrouting them into Gmail retrieval.
    """
    if not has_existing_draft_context:
        return False

    raw_response = _call_groq(
        system_prompt=(
            "You decide whether a user message is a follow-up refinement of the current email draft.\n"
            "Return ONLY valid JSON in this exact shape:\n"
            '{"reuse_existing_draft_context": true}\n'
            "Rules:\n"
            "- Return true when the user is modifying or refining the current draft, such as "
            '"add this line", "make it longer", "rewrite it", "give me the whole email", '
            '"change the subject", or similar follow-ups.\n'
            "- Return false when the user is asking about a different email or wants Gmail retrieval.\n"
            "- Return false when the user starts a clearly unrelated new task.\n"
            "Do not return any explanation or extra text."
        ),
        user_prompt=f"User request:\n{user_input}",
    )

    parsed_response = _extract_json_object(raw_response)
    return bool(parsed_response.get("reuse_existing_draft_context", False))


def summarize_email(email_text: str) -> str:
    """
    Generate a concise summary of an existing email.

    This handler focuses on the main message, key details, and requested
    action in the selected email context.
    """
    return _call_groq(
        system_prompt=(
            "You summarize emails clearly and concisely. "
            "Focus on the main message, important details, and requested action."
        ),
        user_prompt=f"Summarize this email:\n\n{email_text}",
    )


def handle_conversation(
    user_input: str, chat_context: list[dict[str, str]] | None = None
) -> str:
    """
    Answer a general conversation prompt without using Gmail context.

    This handler is the safe fallback for prompts that are not clearly asking
    for email retrieval, analysis, replying, or composition.
    """
    return _call_groq(
        system_prompt=(
            "You are a helpful assistant in a productivity app. "
            "Answer the user's message directly and naturally. "
            "Use recent chat context when it is relevant. "
            "Do not pretend to fetch emails or rely on Gmail context. "
            "Do NOT pretend to create integrations or sync with external services like Google Calendar."
        ),
        user_prompt=(
            f"Recent chat context:\n{_build_chat_context(chat_context) or 'None'}\n\n"
            f"User request:\n{user_input}"
        ),
    )


def answer_question(email_text: str, user_input: str) -> str:
    """
    Answer a user question using only the provided email context.

    This handler is used when the user asks about one retrieved email rather
    than requesting a new draft or a list of emails.
    """
    return _call_groq(
        system_prompt=(
            "You answer questions about an email using only the given email context. "
            "If the email does not contain the answer, say that clearly."
        ),
        user_prompt=(
            f"Email context:\n{email_text}\n\n"
            f"User question:\n{user_input}\n\n"
            "Answer the question based only on the email context."
        ),
    )


def draft_reply(email_text: str, user_input: str) -> str:
    """
    Draft a reply to an existing email.

    This handler uses the retrieved email context plus the user's instruction
    to produce a reply draft only. It does not send the email.
    """
    return _call_groq(
        system_prompt=(
            "You draft professional email replies. "
            "Write a clear reply based on the original email and the user's instruction."
        ),
        user_prompt=(
            f"Original email:\n{email_text}\n\n"
            f"User instruction:\n{user_input}\n\n"
            "Draft an appropriate reply email."
        ),
    )


def compose_new_email(
    user_input: str, chat_context: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    """
    Generate a brand-new email without relying on any retrieved email context.

    The model returns structured `to`, `subject`, and `body` fields so the UI
    and send flow can reuse the generated draft.
    """
    raw_response = _call_groq(
        system_prompt=(
            "You compose brand-new emails from user instructions.\n"
            "Return ONLY valid JSON in this exact shape:\n"
            "{\n"
            '  "to": "recipient@example.com",\n'
            '  "subject": "Subject line",\n'
            '  "body": "Email body"\n'
            "}\n"
            "If no recipient email address is present in the user request, set `to` to an empty string.\n"
            "Do not include markdown fences or explanations."
        ),
        user_prompt=(
            f"Recent chat context:\n{_build_chat_context(chat_context) or 'None'}\n\n"
            "Write a complete new email from scratch based on this instruction:\n\n"
            f"{user_input}"
        ),
    )

    parsed_response = _extract_json_object(raw_response)
    return {
        "to": str(parsed_response.get("to", "")).strip(),
        "subject": str(parsed_response.get("subject", "")).strip(),
        "body": str(parsed_response.get("body", "")).strip(),
    }


def revise_composed_email(
    existing_email_data: dict[str, str],
    user_input: str,
    chat_context: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Revise an existing drafted email based on the user's instruction.

    This allows the app to keep draft continuity without sending full chat
    history back to the model. Only the current draft and current instruction
    are used.
    """
    raw_response = _call_groq(
        system_prompt=(
            "You revise an existing drafted email based on the user's instruction.\n"
            "Return ONLY valid JSON in this exact shape:\n"
            "{\n"
            '  "to": "recipient@example.com",\n'
            '  "subject": "Subject line",\n'
            '  "body": "Email body"\n'
            "}\n"
            "Preserve useful existing content unless the user asks to replace it.\n"
            "Do not include markdown fences or explanations."
        ),
        user_prompt=(
            f"Recent chat context:\n{_build_chat_context(chat_context) or 'None'}\n\n"
            "Current draft:\n"
            f"To: {existing_email_data.get('to', '')}\n"
            f"Subject: {existing_email_data.get('subject', '')}\n"
            f"Body:\n{existing_email_data.get('body', '')}\n\n"
            f"User instruction:\n{user_input}"
        ),
    )

    parsed_response = _extract_json_object(raw_response)
    return {
        "to": str(parsed_response.get("to", existing_email_data.get("to", ""))).strip(),
        "subject": str(
            parsed_response.get("subject", existing_email_data.get("subject", ""))
        ).strip(),
        "body": str(
            parsed_response.get("body", existing_email_data.get("body", ""))
        ).strip(),
    }


def _format_composed_email(email_data: dict[str, str]) -> str:
    """
    Convert structured composed-email fields into one readable response string.

    The dashboard expects a single chat response string, so this helper formats
    the generated draft into a predictable To/Subject/Body block.
    """
    to_value = email_data.get("to", "")
    subject = email_data.get("subject", "")
    body = email_data.get("body", "")
    return (f"To: {to_value}\nSubject: {subject}\n\n{body}").strip()


def process_user_query(
    user_input: str,
    latest_email: dict[str, Any] | None = None,
    composed_email: dict[str, str] | None = None,
    chat_context: list[dict[str, str]] | None = None,
    detected_intent: str | None = None,
    search_query: str | None = None,
) -> dict[str, Any]:
    """
    Detect intent first, then route the request to the correct handler.

    `fetch_emails` returns an AI-generated Gmail query for retrieval.
    `compose_new_email` skips retrieved email context entirely.
    All other intents use the provided email context.
    """
    intent = detected_intent or detect_intent(user_input, chat_context=chat_context)
    resolved_search_query = (
        search_query
        if search_query is not None
        else generate_email_search_query(user_input)
    )

    if intent == "fetch_emails":
        return {
            "intent": "fetch_emails",
            "requires_action": False,
            "search_query": resolved_search_query,
        }

    if intent == "conversation_intent":
        response_text = handle_conversation(user_input, chat_context=chat_context)
        if not response_text:
            response_text = "I couldn't generate a response. Please try again."
        return {
            "intent": "conversation_intent",
            "response": response_text,
            "requires_action": False,
        }

    if intent == "compose_new_email":
        resolved_composed_email = (
            revise_composed_email(composed_email, user_input, chat_context=chat_context)
            if composed_email
            else compose_new_email(user_input, chat_context=chat_context)
        )
        response_text = _format_composed_email(resolved_composed_email)
        return {
            "intent": intent,
            "response": response_text,
            "requires_action": True,
            "email_data": resolved_composed_email,
        }

    if not latest_email:
        return {
            "intent": intent,
            "response": "No email context available.",
            "requires_action": False,
            "search_query": resolved_search_query,
        }

    email_text = _build_email_context(latest_email)

    if intent == "summarize":
        response_text = summarize_email(email_text)
    elif intent == "draft_reply":
        response_text = draft_reply(email_text, user_input)
    else:
        response_text = answer_question(email_text, user_input)
        intent = "question_answering"

    if not response_text:
        response_text = "I couldn't generate a response. Please try again."

    return {
        "intent": intent,
        "response": response_text,
        "requires_action": intent == "draft_reply",
        "search_query": resolved_search_query,
    }


def analyze_email_content(email_text: str, action: str) -> dict[str, Any]:
    """
    Preserve compatibility for older callers that still pass raw email text.

    The wrapper converts the raw email string into the lightweight email
    structure expected by `process_user_query`.
    """
    latest_email = {
        "subject": "",
        "sender": "",
        "snippet": email_text,
    }
    return process_user_query(action, latest_email)
