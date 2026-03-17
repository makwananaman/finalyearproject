"""Audio transcription services for the meetings pipeline.

This module is intentionally limited to:
audio file -> Sarvam batch STT -> structured transcript
"""

from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import requests


SARVAM_BASE_URL = "https://api.sarvam.ai"
SARVAM_STT_JOB_URL = f"{SARVAM_BASE_URL}/speech-to-text/job/v1"
DEFAULT_MODEL = "saaras:v3"
DEFAULT_MODE = "transcribe"
DEFAULT_LANGUAGE_CODE = "unknown"
POLL_INTERVAL_SECONDS = 5
MAX_PROCESSING_SECONDS = 180
REQUEST_TIMEOUT_SECONDS = 30


class AudioProcessingError(Exception):
    """Raised when Sarvam batch transcription fails."""


def _get_api_key() -> str:
    """Load the Sarvam API key from environment variables."""
    api_key = os.getenv("SARVAM_API_KEY")
    if not api_key:
        raise AudioProcessingError("Missing required environment variable: SARVAM_API_KEY")
    return api_key


def _get_headers() -> dict[str, str]:
    """Build common API headers for Sarvam requests."""
    return {
        "api-subscription-key": _get_api_key(),
        "Content-Type": "application/json",
    }


def _request_json(method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    """Send an HTTP request and return a parsed JSON body."""
    try:
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Sarvam API request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise AudioProcessingError("Sarvam API returned a non-JSON response") from exc

    if not isinstance(data, dict):
        raise AudioProcessingError("Sarvam API returned an unexpected response structure")

    return data


def _extract_url(payload: Any) -> str | None:
    """Extract a usable URL from different Sarvam/Azure response shapes."""
    if isinstance(payload, str):
        return payload

    if isinstance(payload, dict):
        for key in ("url", "presigned_url", "signed_url", "download_url", "upload_url"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value

    return None


def _format_timestamp(total_seconds: Any) -> str:
    """Convert seconds to HH:MM:SS format."""
    try:
        seconds = max(float(total_seconds), 0.0)
    except (TypeError, ValueError):
        seconds = 0.0

    whole_seconds = int(seconds)
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _normalize_transcript(result_payload: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize Sarvam transcript JSON into the pipeline contract."""
    diarized_transcript = result_payload.get("diarized_transcript")
    diarized_entries = diarized_transcript.get("entries", []) if isinstance(diarized_transcript, dict) else []

    normalized_entries: list[dict[str, str]] = []

    if isinstance(diarized_entries, list) and diarized_entries:
        for entry in diarized_entries:
            if not isinstance(entry, dict):
                continue

            text = str(entry.get("transcript", "")).strip()
            if not text:
                continue

            speaker_id = str(entry.get("speaker_id", "0")).strip() or "0"
            normalized_entries.append(
                {
                    "speaker": f"speaker_{speaker_id}",
                    "timestamp": _format_timestamp(entry.get("start_time_seconds", 0)),
                    "text": text,
                }
            )

        if normalized_entries:
            return normalized_entries

    timestamps = result_payload.get("timestamps", {})
    if isinstance(timestamps, dict):
        words = timestamps.get("words", [])
        start_times = timestamps.get("start_time_seconds", [])

        if isinstance(words, list) and isinstance(start_times, list):
            for index, text in enumerate(words):
                text_value = str(text).strip()
                if not text_value:
                    continue

                start_time = start_times[index] if index < len(start_times) else 0
                normalized_entries.append(
                    {
                        "speaker": "speaker_1",
                        "timestamp": _format_timestamp(start_time),
                        "text": text_value,
                    }
                )

    if normalized_entries:
        return normalized_entries

    full_transcript = str(result_payload.get("transcript", "")).strip()
    if full_transcript:
        return [
            {
                "speaker": "speaker_1",
                "timestamp": "00:00:00",
                "text": full_transcript,
            }
        ]

    raise AudioProcessingError("Sarvam transcription result did not contain usable transcript data")


def _create_job() -> str:
    """Create a Sarvam batch STT job and return its job ID."""
    payload = {
        "job_parameters": {
            "model": DEFAULT_MODEL,
            "mode": DEFAULT_MODE,
            "language_code": DEFAULT_LANGUAGE_CODE,
            "with_diarization": True,
        }
    }

    response_data = _request_json(
        "POST",
        SARVAM_STT_JOB_URL,
        headers=_get_headers(),
        json=payload,
    )

    job_id = response_data.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        raise AudioProcessingError("Sarvam job creation response did not include a valid job_id")

    return job_id


def _upload_audio_file(job_id: str, file_path: str) -> None:
    """Upload the local audio file to the presigned URL returned by Sarvam."""
    audio_path = Path(file_path)
    if not audio_path.is_file():
        raise AudioProcessingError(f"Audio file not found: {file_path}")

    file_name = audio_path.name
    upload_response = _request_json(
        "POST",
        f"{SARVAM_STT_JOB_URL}/upload-files",
        headers=_get_headers(),
        json={"job_id": job_id, "files": [file_name]},
    )

    upload_urls = upload_response.get("upload_urls")
    if not isinstance(upload_urls, dict):
        raise AudioProcessingError("Sarvam upload response did not include upload_urls")

    upload_target = upload_urls.get(file_name)
    upload_url = _extract_url(upload_target)
    if not upload_url:
        raise AudioProcessingError(f"Sarvam upload URL missing for file: {file_name}")

    content_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

    try:
        with audio_path.open("rb") as audio_file:
            upload_response = requests.put(
                upload_url,
                data=audio_file,
                headers={"Content-Type": content_type},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            upload_response.raise_for_status()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to upload audio file to Sarvam storage: {exc}") from exc


def _start_job(job_id: str) -> None:
    """Trigger processing for an uploaded Sarvam batch job."""
    response_data = _request_json(
        "POST",
        f"{SARVAM_STT_JOB_URL}/{job_id}/start",
        headers=_get_headers(),
        json={},
    )

    job_state = str(response_data.get("job_state", "")).lower()
    if job_state == "failed":
        error_message = response_data.get("error_message") or "Sarvam failed to start the transcription job"
        raise AudioProcessingError(str(error_message))


def submit_audio_for_transcription(file_path: str) -> str:
    """Create, upload, and start an async Sarvam transcription job."""
    job_id = _create_job()
    _upload_audio_file(job_id, file_path)
    _start_job(job_id)
    return job_id


def check_transcription_status(job_id: str) -> str:
    """Check the current status of a Sarvam transcription job."""
    response_data = _request_json(
        "GET",
        f"{SARVAM_STT_JOB_URL}/{job_id}/status",
        headers={"api-subscription-key": _get_api_key()},
    )

    raw_state = str(response_data.get("job_state", "")).strip().lower()
    if raw_state in {"accepted", "pending", "running", "processing"}:
        return "processing"
    if raw_state == "completed":
        return "completed"
    if raw_state == "failed":
        return "failed"

    raise AudioProcessingError(f"Unexpected Sarvam job status: {response_data.get('job_state')}")


def fetch_transcription_result(job_id: str) -> list[dict[str, str]]:
    """Fetch and normalize the completed Sarvam transcription output."""
    status_response = _request_json(
        "GET",
        f"{SARVAM_STT_JOB_URL}/{job_id}/status",
        headers={"api-subscription-key": _get_api_key()},
    )

    job_details = status_response.get("job_details")
    if not isinstance(job_details, list) or not job_details:
        raise AudioProcessingError("Sarvam status response did not include job output details")

    output_files: list[str] = []
    for detail in job_details:
        if not isinstance(detail, dict):
            continue

        outputs = detail.get("outputs", [])
        if not isinstance(outputs, list):
            continue

        for output in outputs:
            if not isinstance(output, dict):
                continue

            file_name = output.get("file_name")
            if isinstance(file_name, str) and file_name:
                output_files.append(file_name)

    if not output_files:
        raise AudioProcessingError("Sarvam did not return any output files for the completed job")

    download_response = _request_json(
        "POST",
        f"{SARVAM_STT_JOB_URL}/download-files",
        headers=_get_headers(),
        json={"job_id": job_id, "files": output_files},
    )

    download_urls = download_response.get("download_urls")
    if not isinstance(download_urls, dict):
        raise AudioProcessingError("Sarvam download response did not include download_urls")

    for output_file in output_files:
        download_target = download_urls.get(output_file)
        download_url = _extract_url(download_target)
        if not download_url:
            continue

        try:
            result_response = requests.get(download_url, timeout=REQUEST_TIMEOUT_SECONDS)
            result_response.raise_for_status()
        except requests.RequestException as exc:
            raise AudioProcessingError(f"Failed to download Sarvam transcript output: {exc}") from exc

        try:
            result_payload = result_response.json()
        except ValueError as exc:
            raise AudioProcessingError("Sarvam output file did not contain valid JSON") from exc

        if isinstance(result_payload, dict):
            return _normalize_transcript(result_payload)

    raise AudioProcessingError("Sarvam did not provide a readable transcript output file")


def process_audio(file_path: str) -> list[dict[str, str]]:
    """Run the complete async transcription flow and return structured transcript data."""
    job_id = submit_audio_for_transcription(file_path)
    deadline = time.monotonic() + MAX_PROCESSING_SECONDS

    while time.monotonic() < deadline:
        status = check_transcription_status(job_id)

        if status == "completed":
            return fetch_transcription_result(job_id)

        if status == "failed":
            raise AudioProcessingError(f"Sarvam transcription job failed: {job_id}")

        time.sleep(POLL_INTERVAL_SECONDS)

    raise AudioProcessingError(
        f"Sarvam transcription job timed out after {MAX_PROCESSING_SECONDS} seconds: {job_id}"
    )
