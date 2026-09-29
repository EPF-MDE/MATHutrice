import time
from collections.abc import Callable

import openai
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
        """The model's answer to `messages`, as text.

        The call gets only the time left before the deadline, and no retry: a
        retry would start its own timeout over. Raises `DeadlineExceeded`
        without calling the model once the deadline has passed, or when the
        call runs out of time. Any other API error reaches the caller as is.
        """
        remaining = self._ends_at - self._clock()
        if remaining <= 0:
            raise DeadlineExceeded
        try:
            response = self._client.with_options(
                timeout=remaining, max_retries=0
            ).chat.completions.create(model=self._model, messages=messages)
        except openai.APITimeoutError as timeout:
            raise DeadlineExceeded from timeout
        return response.choices[0].message.content
