import time
from collections.abc import Callable

from openai import OpenAI

# How long a student waits for an evaluation test before giving up: assumption D
# of the Design Document, EPF-MDE/MATHutrice#59.
EVALUATION_DEADLINE_SECONDS = 300.0


class DeadlineExceeded(Exception):
    """The evaluation's deadline passed: no further LLM call will be made."""


class LLMDeadline:
    """Every LLM call of one evaluation test, under one deadline.

    The clock starts when the object is built. Callers pass the client they
    would have called directly; tests pass a fake one, and a fake clock.
    """

    def __init__(
        self,
        client: OpenAI,
        model: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._model = model
        self._clock = clock
        self._ends_at = clock() + EVALUATION_DEADLINE_SECONDS

    def complete(self, messages: list[dict]) -> str:
        """The model's answer to `messages`, as text."""
        response = self._client.chat.completions.create(
            model=self._model, messages=messages
        )
        return response.choices[0].message.content
