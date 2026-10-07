"""One deadline over every LLM call of an evaluation test."""

from ._deadline import EVALUATION_DEADLINE_SECONDS, DeadlineExceeded, LLMDeadline

__all__ = ["EVALUATION_DEADLINE_SECONDS", "DeadlineExceeded", "LLMDeadline"]
