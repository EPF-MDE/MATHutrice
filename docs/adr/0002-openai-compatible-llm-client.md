# Every LLM call goes through an OpenAI-compatible client configured by settings

MATHutrice called Mistral through the `mistralai` SDK, with a hard-coded model and an API key committed as a fallback in code. A deployment must be able to point at a self-hosted OpenAI-compatible gateway (`https://locallm.mde.epf.fr/ollama/v1`) while production keeps using Mistral, and no key may live in the repository. So every runtime LLM call now goes through the `openai` client built once in `mathutrice/llm_client.py` from `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`, with no default value in code: the application refuses to start if one is missing. Mistral exposes an OpenAI-compatible API at `https://api.mistral.ai/v1`, so production only changes settings.

## Consequences

- Switching LLM endpoint or model is a settings change, never a code change.
- `mistralai` stays in the dependencies in `pyproject.toml`, and the `*_example.py` files keep importing it; removing them is tracked in #29.
