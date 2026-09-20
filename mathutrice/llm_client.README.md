# The LLM client, `llm_client.py`

This is a candidate seam. It is **named here and not implemented**: nothing in
this branch places an interface in front of it, wraps it, or changes a call
site. What follows is what is true of the code today, so that whoever designs
the seam designs from facts rather than from a reading of the source.

## What the seam holds

**How long a user waits for an answer.** Every runtime LLM call goes through the
`client` built in `llm_client.py` (ADR-0002), and every page that waits on one —
feedback after an attempt, a generated question, a streamed chat reply — waits
exactly as long as that client lets it. Change the client and you change the
wait everywhere; change nothing and the wait is whatever the vendor's library
decided.

## What the client does today

`llm_client.py` builds one `openai.OpenAI` from the three settings ADR-0002
describes. What matters here is what it does **not** pass: no `timeout` and no
`max_retries`. The OpenAI client's documented defaults therefore apply, a
**ten-minute** timeout and **two** retries, so a hung call can hold a request
for half an hour or more before anyone hears about it. Nothing in this
repository says so.

That default is the vendor's, not a decision taken here. No number in this
repository expresses what the application considers an acceptable wait, because
no such number has been written down.

## The call sites

There are **eight**, and each handles its errors differently. (The
`chatbot_example.py` and `chat_stream_example.py` scripts also call an LLM, but
build their own `mistralai` client and are not reached at runtime, so they are
not among the eight and would not be covered by a seam here.)

| Call site | On failure |
| --- | --- |
| `app.py` — `feedback_endpoint` (`POST /session/feedback`) | caught by the endpoint's outer `except Exception`, prints a traceback, returns HTTP 500 |
| `fonctions_python/chatbot.py` — `chat_stream_with_history` | `except Exception` yields `f"Erreur: {e}"` **into the stream**, so the error text reaches the user as if it were the answer |
| `fonctions_python/chatbot.py` — `chat` (legacy) | `except Exception` pops the history entry, returns `f"Erreur: {e}"` as the reply |
| `fonctions_python/chatbot.py` — `chat_stream` (legacy) | `except Exception` pops the history entry, yields the error text into the stream |
| `fonctions_python/base_generator.py` — `call_mistral` | catches `ValueError` only, retries up to `MAX_RETRIES`; an API error is not a `ValueError` and escapes the retry loop |
| `fonctions_python/type_questions/qro_generator.py` — `is_correct_llm` | `except Exception` logs a warning and falls back to the plain string comparison |
| `lacune_evaluation/LLM_as_Evaluator.py` — `detecter_competences` | no handling; propagates to the caller |
| `lacune_evaluation/LLM_as_Evaluator.py` — `analyser_lacunes` | no handling; propagates to the caller |

Two of them turn a failure into a user-visible message that looks like a tutor's
answer. Two let it propagate untouched. One catches a type of error that an API
call does not raise. They do not agree on what a failed call means, and there is
no single place where that question could be answered.

## What is deliberately missing

**The number.** How long a user should wait before the application gives up is
a product decision, and it belongs in a design document written by whoever makes
it — alongside what happens when that limit is reached, and what the eight call
sites above should do instead of what they do now. This README states the
problem and stops there on purpose. Putting a number here would answer it
before it has been asked.

## Not to be confused with

[`README.md`](README.md) in this same directory, which documents the package
boundary rules enforced by `tach check`. It is a different document about a
different thing and is unaffected by any of this.
