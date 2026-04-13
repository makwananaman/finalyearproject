import os
import tempfile

from django.http import JsonResponse
from django.shortcuts import render

from apps.meetings.services.audio_processing import AudioProcessingError, process_audio
from apps.meetings.services.meeting_pipeline import process_meeting


def set_meeting_status(request, status):
    request.session["meeting_processing_status"] = status
    request.session.modified = True


def meetings_view(request):
    summary = []
    tasks = []
    high_priority_tasks = []
    error = ""
    processing = False
    transcript_input = ""

    if request.method == "POST":
        processing = True
        set_meeting_status(request, "Starting analysis...")
        audio_file = request.FILES.get("audio_file") or request.FILES.get(
            "meeting_file"
        )
        text_file = request.FILES.get("text_file")
        manual_text = (
            request.POST.get("manual_text", "").strip()
            or request.POST.get("transcript", "").strip()
        )

        try:
            if not audio_file and not text_file and not manual_text:
                raise ValueError(
                    "Please provide an audio file, text file, or manual text."
                )

            if audio_file:
                temp_file_path = ""
                try:
                    suffix = os.path.splitext(audio_file.name)[1] or ".mp3"
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=suffix
                    ) as temp_file:
                        for chunk in audio_file.chunks():
                            temp_file.write(chunk)
                        temp_file_path = temp_file.name

                    def status_callback(msg):
                        set_meeting_status(request, msg)

                    transcript = process_audio(
                        temp_file_path, status_callback=status_callback
                    )
                    transcript_input = ""
                finally:
                    if temp_file_path and os.path.exists(temp_file_path):
                        os.remove(temp_file_path)
            else:
                set_meeting_status(request, "Processing transcript text...")
                if text_file:
                    text_content = text_file.read().decode("utf-8").strip()
                else:
                    text_content = manual_text

                transcript_input = text_content
                transcript = [
                    {
                        "speaker": "speaker_1",
                        "timestamp": "00:00:00",
                        "text": text_content,
                    }
                ]

            set_meeting_status(request, "Analyzing meeting content with AI...")
            result = process_meeting(transcript)
            summary = result.get("summary", [])
            tasks = result.get("tasks", [])
            high_priority_tasks = result.get("high_priority_tasks", [])
            set_meeting_status(request, "Finalizing results...")
        except Exception as exc:
            error = str(exc) or "Meeting processing failed. Please try again."
            set_meeting_status(request, "Error: " + error)
        finally:
            processing = False
            # We don't clear status here so the last poll gets "Finalizing" or "Error"

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return render_json_response(
                summary, tasks, high_priority_tasks, error, processing
            )

    context = {
        "summary": summary,
        "tasks": tasks,
        "high_priority_tasks": high_priority_tasks,
        "error": error,
        "processing": processing,
        "result": {
            "summary": summary,
            "tasks": tasks,
            "high_priority_tasks": high_priority_tasks,
        }
        if summary or tasks or high_priority_tasks
        else None,
        "error_message": error,
        "transcript_input": transcript_input,
    }
    return render(request, "meetings/list.html", context)


meeting_list = meetings_view


def meeting_status_view(request):
    status = request.session.get("meeting_processing_status", "Ready")
    return JsonResponse({"status": status})


def render_json_response(summary, tasks, high_priority_tasks, error, processing):
    return JsonResponse(
        {
            "summary": summary,
            "tasks": tasks,
            "high_priority_tasks": high_priority_tasks,
            "error": error,
            "processing": processing,
        }
    )
