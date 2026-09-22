# Smoke test

Manual check that a fresh clone of `course-2026` runs, with no Entra credentials and no committed LLM key. Run it locally and on the Linux host you deploy to.

## 1. Set up

Requires [uv](https://docs.astral.sh/uv/). It installs the Python version in `.python-version` and the dependency versions in `uv.lock`.

```sh
git clone -b course-2026 <your fork URL> mathutrice
cd mathutrice
uv sync
. .venv/bin/activate
cp .env.example .env
```

If `uv sync` reports `No interpreter found for Python 3.14.7`, your uv predates that Python release: run `uv self update`, then `uv sync` again.

Without uv, `pip install -e .` in a virtual environment running that Python version installs the project as well, from `pyproject.toml` rather than the lockfile.

In `.env`, set `LLM_API_KEY` to the key for your LLM endpoint. The default endpoint is Mistral: get a key at <https://console.mistral.ai> (a free account works). Leave everything else as it is.

## 2. Start

From the repository root:

```sh
uvicorn mathutrice.app:app --port 8000
```

Expected, on a fresh database: a `WARNING: AUTH_MODE=dev` line, a
`Base initialisée : ... lignes insérées.` line, then `Application startup
complete`. The seeding line only appears when there was something to insert.

The application must refuse to start when a required setting is missing: with `LLM_API_KEY` emptied, startup fails with `ValueError: LLM_API_KEY missing`.

## 3. Check in the browser

1. Open <http://localhost:8000/>. You are redirected to `/dev/login`, and a red dev-mode banner shows on every page.
2. Sign in as the seeded Student, `eleve@epf.fr`, or with any other address ending in `@epf.fr` or `@epfedu.fr`. You land on the home page.
3. The home page lists the seven seeded modules. Open one: its page shows the notion's description and your progression on its competences.
4. Open the chat and ask a question. The answer appears word by word (streaming).

Step 4 calls the **LLM endpoint**. An `Erreur: ...` message in the chat means the endpoint, key or model in `.env` is wrong.

## 4. Check from the command line

With the application running:

```sh
curl -s -H 'Content-Type: application/json' -d '{"message":"bonjour"}' \
  http://localhost:8000/chat/complete
```

Expected: `{"ok":true,"response":"..."}` with an answer from the model.
