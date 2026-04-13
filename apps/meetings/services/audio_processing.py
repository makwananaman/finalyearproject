"""Simple Sarvam speech-to-text integration for meeting audio."""

import os
import time

import requests


SARVAM_BASE_URL = "https://api.sarvam.ai"
SARVAM_JOB_URL = f"{SARVAM_BASE_URL}/speech-to-text/job/v1"
POLL_INTERVAL_SECONDS = 5
MAX_WAIT_SECONDS = 900

AudioProcessingError = RuntimeError


def get_api_key():
    """Read SARVAM_API_KEY from environment."""
    api_key = os.getenv("SARVAM_API_KEY", "").strip()
    if not api_key:
        raise AudioProcessingError("Missing SARVAM_API_KEY in environment.")
    return api_key


def create_job():
    """Create a Sarvam job and return its job_id."""
    try:
        response = requests.post(
            SARVAM_JOB_URL,
            headers={
                "api-subscription-key": get_api_key(),
                "Content-Type": "application/json",
            },
            json={
                "job_parameters": {
                    "model": "saaras:v3",
                    "mode": "transcribe",
                    "language_code": "unknown",
                    "with_diarization": True,
                }
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to create Sarvam job: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Sarvam job creation returned invalid JSON.") from exc

    job_id = data.get("job_id")
    if not job_id:
        raise AudioProcessingError("Sarvam job creation did not return job_id.")
    return job_id

def extract_upload_url(upload_info):
    # Case 1: direct string
    if isinstance(upload_info, str):
        return upload_info

    # Case 2: dictionary
    if isinstance(upload_info, dict):
        # Add file_url support FIRST
        if isinstance(upload_info.get("file_url"), str):
            return upload_info.get("file_url")

        # Standard keys (docs)
        for key in ["url", "upload_url"]:
            value = upload_info.get(key)
            if isinstance(value, str):
                return value

        # Nested structure (if any)
        data = upload_info.get("data")
        if isinstance(data, dict):
            for key in ["url", "upload_url"]:
                value = data.get(key)
                if isinstance(value, str):
                    return value

    return None


def extract_download_url(download_info):
    """Resolve presigned download URL from Sarvam download_urls values (see FileSignedURLDetails)."""
    if isinstance(download_info, str):
        return download_info

    if isinstance(download_info, dict):
        if isinstance(download_info.get("file_url"), str):
            return download_info.get("file_url")

        for key in ("url", "download_url", "presigned_url", "signed_url"):
            value = download_info.get(key)
            if isinstance(value, str):
                return value

        data = download_info.get("data")
        if isinstance(data, dict):
            if isinstance(data.get("file_url"), str):
                return data.get("file_url")
            for key in ("url", "download_url", "presigned_url", "signed_url"):
                value = data.get(key)
                if isinstance(value, str):
                    return value

    return None


def upload_audio(file_path, job_id):
    """Upload the audio file to the Sarvam upload URL."""
    file_name = os.path.basename(file_path)

    if not os.path.isfile(file_path):
        raise AudioProcessingError(f"Audio file not found: {file_path}")

    try:
        response = requests.post(
            f"{SARVAM_JOB_URL}/upload-files",
            headers={
                "api-subscription-key": get_api_key(),
                "Content-Type": "application/json",
            },
            json={"job_id": job_id, "files": [file_name]},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to get Sarvam upload URL: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Sarvam upload URL response was not valid JSON.") from exc

    upload_urls = data.get("upload_urls")
    if not isinstance(upload_urls, dict) or not upload_urls:
        raise AudioProcessingError("Sarvam did not return upload_urls.")

    upload_info = upload_urls.get(file_name)
    if not upload_info and len(upload_urls) == 1:
        upload_info = next(iter(upload_urls.values()))
        
    print("Upload Info:", upload_info)

    if isinstance(upload_info, dict):
        upload_url = extract_upload_url(upload_info)
    else:
        upload_url = upload_info

    if not upload_url:
        raise AudioProcessingError("Sarvam did not return a usable upload URL.")

    try:
        with open(file_path, "rb") as audio_file:
            response = requests.put(
                upload_url,
                data=audio_file,
                headers={
                    "Content-Type": "audio/mpeg",
                    "x-ms-blob-type": "BlockBlob",
                },
                timeout=60,
            )
            response.raise_for_status()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to upload audio to Sarvam: {exc}") from exc
    except OSError as exc:
        raise AudioProcessingError(f"Failed to read audio file: {exc}") from exc


def start_job(job_id):
    """Start the Sarvam transcription job."""
    try:
        response = requests.post(
            f"{SARVAM_JOB_URL}/{job_id}/start",
            headers={
                "api-subscription-key": get_api_key(),
                "Content-Type": "application/json",
            },
            json={},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to start Sarvam job: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Sarvam start-job response was not valid JSON.") from exc

    if str(data.get("job_state", "")).strip().lower() == "failed":
        raise AudioProcessingError("Sarvam failed to start the transcription job.")


def check_status(job_id):
    """Return processing, completed, or failed."""
    try:
        response = requests.get(
            f"{SARVAM_JOB_URL}/{job_id}/status",
            headers={"api-subscription-key": get_api_key()},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to check Sarvam job status: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Sarvam status response was not valid JSON.") from exc

    state = str(data.get("job_state", "")).strip().lower()
    if state in {"accepted", "pending", "running", "processing"}:
        return "processing"
    if state == "completed":
        return "completed"
    if state == "failed":
        return "failed"
    raise AudioProcessingError(f"Unexpected Sarvam job status: {data.get('job_state')}")


def fetch_result(job_id):
    """Fetch transcript JSON and normalize it."""
    try:
        status_response = requests.get(
            f"{SARVAM_JOB_URL}/{job_id}/status",
            headers={"api-subscription-key": get_api_key()},
            timeout=30,
        )
        status_response.raise_for_status()
        status_data = status_response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to fetch Sarvam job details: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Sarvam job details response was not valid JSON.") from exc

    output_files = []
    for detail in status_data.get("job_details", []):
        if not isinstance(detail, dict):
            continue
        for output in detail.get("outputs", []):
            if isinstance(output, dict) and output.get("file_name"):
                output_files.append(output["file_name"])

    if not output_files:
        raise AudioProcessingError("Sarvam did not return any transcript output files.")

    try:
        download_response = requests.post(
            f"{SARVAM_JOB_URL}/download-files",
            headers={
                "api-subscription-key": get_api_key(),
                "Content-Type": "application/json",
            },
            json={"job_id": job_id, "files": output_files},
            timeout=30,
        )
        download_response.raise_for_status()
        download_data = download_response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to get Sarvam download URL: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Sarvam download URL response was not valid JSON.") from exc

    download_urls = download_data.get("download_urls")
    if not isinstance(download_urls, dict) or not download_urls:
        raise AudioProcessingError("Sarvam did not return download_urls.")

    download_info = None
    for file_name in output_files:
        if file_name in download_urls:
            download_info = download_urls[file_name]
            break
    if not download_info and download_urls:
        download_info = next(iter(download_urls.values()))

    download_url = extract_download_url(download_info)

    if not download_url:
        raise AudioProcessingError("Sarvam download_urls had no usable URL for the transcript.")

    try:
        file_response = requests.get(download_url, timeout=30)
        print("DEBUG raw response text:", file_response.text[:500])
        file_response.raise_for_status()
        transcript_data = file_response.json()
    except requests.RequestException as exc:
        raise AudioProcessingError(f"Failed to download transcript file: {exc}") from exc
    except ValueError as exc:
        raise AudioProcessingError("Transcript file was not valid JSON.") from exc

    if not isinstance(transcript_data, dict):
        raise AudioProcessingError("Sarvam transcript data was missing or invalid.")

    normalized = []
    diarized = transcript_data.get("diarized_transcript")

    if isinstance(diarized, dict):
        for entry in diarized.get("entries", []):
            if not isinstance(entry, dict):
                continue

            text = str(entry.get("transcript", "")).strip()
            if not text:
                continue

            speaker_id = str(entry.get("speaker_id", "1")).strip() or "1"
            try:
                seconds = int(float(entry.get("start_time_seconds", 0)))
            except (TypeError, ValueError):
                seconds = 0

            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            secs = seconds % 60

            normalized.append(
                {
                    "speaker": f"speaker_{speaker_id}",
                    "timestamp": f"{hours:02d}:{minutes:02d}:{secs:02d}",
                    "text": text,
                }
            )

    if normalized:
        return normalized

    full_text = str(transcript_data.get("transcript", "")).strip()
    if full_text:
        return [
            {
                "speaker": "speaker_1",
                "timestamp": "00:00:00",
                "text": full_text,
            }
        ]

    raise AudioProcessingError("Sarvam transcript response did not contain usable text.")


def process_audio(file_path, status_callback=None, upload_name=None):
    """Run the full STT pipeline."""
    try:
        if status_callback:
            status_callback("Creating transcription job...")
        job_id = create_job()

        if status_callback:
            status_callback("Uploading audio to transcription provider...")
        upload_audio(file_path, job_id)

        if status_callback:
            status_callback("Starting transcription job...")
        start_job(job_id)

        if status_callback:
            status_callback("Waiting for transcription result...")
        deadline = time.time() + MAX_WAIT_SECONDS

        while time.time() < deadline:
            status = check_status(job_id)
            if status == "completed":
                if status_callback:
                    status_callback("Downloading transcript...")
                return fetch_result(job_id)
            if status == "failed":
                raise AudioProcessingError(f"Sarvam transcription job failed: {job_id}")
            time.sleep(POLL_INTERVAL_SECONDS)

        raise AudioProcessingError(
            f"Sarvam transcription job timed out after {MAX_WAIT_SECONDS} seconds: {job_id}"
        )
    except AudioProcessingError:
        raise
    except Exception as exc:
        raise AudioProcessingError(f"Unexpected error: {exc}") from exc
