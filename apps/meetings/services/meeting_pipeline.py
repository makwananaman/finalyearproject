"""Meeting transcript processing pipeline.

This module only orchestrates transcript chunking and AI engine calls.
It does not handle audio, prompts, or direct LLM/API access.
"""

from __future__ import annotations

import math
import re
from typing import Any

from apps.ai_engine import meetings_ai_engine

CHUNK_WINDOW_SECONDS = 10 * 60
MAX_CHUNK_TOKENS = 3000


def _timestamp_to_seconds(timestamp: str) -> int:
    """Convert HH:MM:SS timestamp text into total seconds."""
    hours, minutes, seconds = (int(part) for part in timestamp.split(":"))
    return (hours * 3600) + (minutes * 60) + seconds


def _format_chunk_text(chunk: list[dict[str, Any]]) -> str:
    """Convert structured transcript entries into plain text for the AI engine."""
    lines = []
    for entry in chunk:
        speaker = entry.get("speaker", "unknown_speaker")
        timestamp = entry.get("timestamp", "00:00:00")
        text = entry.get("text", "").strip()
        lines.append(f"[{timestamp}] {speaker}: {text}")
    return "\n".join(lines)


def _normalize_text(text: str) -> str:
    """Normalize text to support duplicate detection."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _similarity_key(text: str) -> str:
    """Create a coarse similarity key for task comparison."""
    normalized = _normalize_text(text)
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return normalized


def group_into_time_chunks(
    transcript: list[dict[str, Any]],
    chunk_window_seconds: int = CHUNK_WINDOW_SECONDS,
) -> list[list[dict[str, Any]]]:
    """Group transcript entries into 8-12 minute time chunks without splitting entries."""
    if not transcript:
        return []

    # Sort by timestamp so chunking is stable.
    sorted_entries = sorted(
        transcript,
        key=lambda entry: _timestamp_to_seconds(entry.get("timestamp", "00:00:00")),
    )

    chunks: list[list[dict[str, Any]]] = []
    current_chunk: list[dict[str, Any]] = []
    chunk_start_seconds: int | None = None

    for entry in sorted_entries:
        entry_seconds = _timestamp_to_seconds(entry.get("timestamp", "00:00:00"))

        # Start the first chunk with the first transcript entry.
        if chunk_start_seconds is None:
            current_chunk.append(entry)
            chunk_start_seconds = entry_seconds
            continue

        elapsed_seconds = entry_seconds - chunk_start_seconds

        # Start a new chunk only at entry boundaries after the window is reached.
        if elapsed_seconds >= chunk_window_seconds and current_chunk:
            chunks.append(current_chunk)
            current_chunk = [entry]
            chunk_start_seconds = entry_seconds
            continue

        current_chunk.append(entry)

    # Add the last chunk after processing all entries.
    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def estimate_token_length(text: str) -> int:
    """Estimate token length using 1 token ~= 4 characters."""
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def split_large_chunks(
    chunks: list[list[dict[str, Any]]],
    max_chunk_tokens: int = MAX_CHUNK_TOKENS,
) -> list[list[dict[str, Any]]]:
    """Split chunks at entry boundaries when they exceed the token threshold."""
    final_chunks: list[list[dict[str, Any]]] = []

    for chunk in chunks:
        chunk_text = _format_chunk_text(chunk)
        if estimate_token_length(chunk_text) <= max_chunk_tokens:
            final_chunks.append(chunk)
            continue

        current_split: list[dict[str, Any]] = []

        for entry in chunk:
            candidate_split = current_split + [entry]
            candidate_text = _format_chunk_text(candidate_split)

            # Keep adding entries while the split stays inside the token limit.
            if (
                current_split
                and estimate_token_length(candidate_text) > max_chunk_tokens
            ):
                final_chunks.append(current_split)
                current_split = [entry]
                continue

            current_split = candidate_split

        if current_split:
            final_chunks.append(current_split)

    return final_chunks


def process_chunk(
    chunk: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]] | list[str]]:
    """Convert a transcript chunk to plain text and send it to the meetings AI engine."""
    chunk_text = _format_chunk_text(chunk)

    # Prefer the dedicated chunk analyzer if it exists.
    if hasattr(meetings_ai_engine, "analyze_chunk"):
        result = meetings_ai_engine.analyze_chunk(chunk_text)
    else:
        result = meetings_ai_engine.process_meeting_text(chunk_text)

    return {
        "summary": result.get("summary", []) if isinstance(result, dict) else [],
        "tasks": result.get("tasks", []) if isinstance(result, dict) else [],
    }


def merge_chunk_results(results: list[dict[str, Any]]) -> list[str]:
    """Merge summary points from all chunks and remove duplicates."""
    merged_summary: list[str] = []
    seen_points: set[str] = set()

    for result in results:
        for point in result.get("summary", []):
            normalized_point = _normalize_text(str(point))
            if not normalized_point or normalized_point in seen_points:
                continue
            seen_points.add(normalized_point)
            merged_summary.append(str(point).strip())

    return merged_summary


def deduplicate_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate tasks using owner + normalized task text."""
    deduplicated: list[dict[str, Any]] = []
    seen_tasks: set[tuple[str, str]] = set()

    for task in tasks:
        owner = str(task.get("owner", "")).strip()
        task_text = str(task.get("task", "")).strip()
        similarity_key = _similarity_key(task_text)
        dedupe_key = (_normalize_text(owner), similarity_key)

        if not similarity_key or dedupe_key in seen_tasks:
            continue

        seen_tasks.add(dedupe_key)
        deduplicated.append(
            {
                "task": task_text,
                "owner": owner,
                "priority": task.get("priority", "Medium"),
            }
        )

    return deduplicated


def extract_high_priority(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only tasks marked as high priority."""
    return [
        task
        for task in tasks
        if str(task.get("priority", "")).strip().lower() == "high"
    ]


def process_meeting(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the full transcript-to-summary/task orchestration pipeline."""
    # Step 1: Group transcript entries into time-based chunks.
    time_chunks = group_into_time_chunks(transcript)

    # Step 2: Split oversized chunks so each chunk fits the AI engine limits.
    processable_chunks = split_large_chunks(time_chunks)

    # Step 3: Process each chunk through the meetings AI engine.
    chunk_results = [process_chunk(chunk) for chunk in processable_chunks]

    # Step 4: Merge summary points from all chunk results.
    summary = merge_chunk_results(chunk_results)

    # Step 5: Collect tasks from every chunk result.
    all_tasks: list[dict[str, Any]] = []
    for result in chunk_results:
        all_tasks.extend(result.get("tasks", []))

    # Step 6: Remove duplicate tasks across chunks.
    deduplicated_tasks = deduplicate_tasks(all_tasks)

    # Step 7: Extract only high-priority tasks.
    high_priority_tasks = extract_high_priority(deduplicated_tasks)

    # Step 8: Return the final pipeline output.
    return {
        "summary": summary,
        "tasks": deduplicated_tasks,
        "high_priority_tasks": high_priority_tasks,
    }


def run_meeting_pipeline(
    meeting_input: list[dict[str, Any]], input_type: str | None = None
) -> dict[str, Any]:
    """Backward-compatible wrapper around the transcript pipeline."""
    if input_type and input_type != "transcript":
        raise ValueError("meeting_pipeline only accepts structured transcript input.")
    return process_meeting(meeting_input)
