from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from apps.meetings.services import audio_processing


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class MeetingViewTests(SimpleTestCase):
    @patch("apps.meetings.views.process_meeting")
    def test_ajax_submit_returns_result_payload(self, process_meeting_mock):
        process_meeting_mock.return_value = {
            "summary": ["Done"],
            "tasks": [],
            "high_priority_tasks": [],
        }

        response = self.client.post(
            reverse("meeting_list"),
            {
                "transcript": '[{"speaker":"speaker_1","timestamp":"00:00:00","text":"I will send the report tomorrow."}]'
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {
                "summary": ["Done"],
                "tasks": [],
                "high_priority_tasks": [],
                "error": "",
                "processing": False,
            },
        )
        process_meeting_mock.assert_called_once()


class AudioProcessingTests(SimpleTestCase):
    @patch(
        "apps.meetings.services.audio_processing.create_job",
        side_effect=audio_processing.AudioProcessingError("boom"),
    )
    def test_process_audio_raises_clear_error(self, create_job_mock):
        with self.assertRaises(audio_processing.AudioProcessingError) as exc_info:
            audio_processing.process_audio("C:\\temp\\meeting.mp3", upload_name="meeting.mp3")

        self.assertIn("boom", str(exc_info.exception))
        create_job_mock.assert_called_once()
