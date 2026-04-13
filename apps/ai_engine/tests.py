from unittest.mock import patch

from django.test import SimpleTestCase

from apps.ai_engine import meetings_ai_engine


class MeetingsAiEngineTests(SimpleTestCase):
    def test_generate_summary_returns_empty_when_llm_returns_empty(self):
        with patch.object(meetings_ai_engine, "call_llm", return_value=""):
            summary = meetings_ai_engine.generate_summary("[00:00:00] speaker_1: Hello")

        self.assertEqual(summary, [])

    def test_extract_tasks_returns_empty_when_llm_returns_empty(self):
        with patch.object(meetings_ai_engine, "call_llm", return_value=""):
            tasks = meetings_ai_engine.extract_tasks("[00:00:00] speaker_1: I will send it")

        self.assertEqual(tasks, [])
