"""An evaluation test comes back within 5 minutes, however slow the LLM is.

The number is assumption D of the Design Document, EPF-MDE/MATHutrice#59: a
student in the session slot waits 5 minutes before giving up.
"""

import math
from types import SimpleNamespace

import httpx
import openai
import pytest

from mathutrice.llm_deadline import DeadlineExceeded, LLMDeadline

FIVE_MINUTES = 300.0
QUESTIONS_ASKED = 20


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SlowFakeClient:
    """Stands in for `openai.OpenAI`: every answer takes `seconds_per_answer`.

    Time passes on `clock`, not on the wall. Like the real client, a call that
    outlasts its timeout raises `openai.APITimeoutError` once its retries are
    spent, and a client nobody configured uses the library's defaults: a
    ten-minute timeout and two retries.
    """

    def __init__(
        self,
        clock: FakeClock,
        seconds_per_answer: float,
        timeout: float = 600.0,
        max_retries: int = 2,
    ) -> None:
        self._clock = clock
        self._seconds_per_answer = seconds_per_answer
        self._timeout = timeout
        self._max_retries = max_retries
        self.calls_started_at: list[float] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, *, timeout: float, max_retries: int) -> "SlowFakeClient":
        configured = SlowFakeClient(
            self._clock, self._seconds_per_answer, timeout, max_retries
        )
        configured.calls_started_at = self.calls_started_at
        return configured

    def _create(self, *, model: str, messages: list[dict]):
        self.calls_started_at.append(self._clock.now)
        if self._seconds_per_answer <= self._timeout:
            self._clock.now += self._seconds_per_answer
            message = SimpleNamespace(content="une question")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        self._clock.now += self._timeout * (1 + self._max_retries)
        raise openai.APITimeoutError(
            request=httpx.Request("POST", "https://llm.invalid/chat/completions")
        )


def sit_evaluation(client: SlowFakeClient, clock: FakeClock) -> list[str]:
    """Asks for every question of the longest test, as the evaluation does."""
    deadline = LLMDeadline(client, "fake-model", clock=clock)
    answers = []
    for _ in range(QUESTIONS_ASKED):
        answers.append(
            deadline.complete([{"role": "user", "content": "Pose une question."}])
        )
    return answers


# 20 s an answer: each call is quick, but 20 of them take 400 s. Stopping at
# 5 minutes exactly, not before, is what tells one deadline for the whole
# test apart from a timeout per call.
@pytest.mark.parametrize(
    "seconds_per_answer",
    [20.0, 60.0, math.inf],
    ids=[
        "every-answer-takes-20-seconds",
        "every-answer-takes-a-minute",
        "the-first-call-hangs",
    ],
)
def test_a_slow_evaluation_stops_at_five_minutes(seconds_per_answer):
    clock = FakeClock()
    client = SlowFakeClient(clock, seconds_per_answer)

    with pytest.raises(DeadlineExceeded):
        sit_evaluation(client, clock)

    assert clock.now == FIVE_MINUTES
    assert all(started < FIVE_MINUTES for started in client.calls_started_at)


def test_a_fast_evaluation_gets_every_question():
    clock = FakeClock()
    client = SlowFakeClient(clock, seconds_per_answer=2.3)

    answers = sit_evaluation(client, clock)

    assert len(answers) == QUESTIONS_ASKED
