# MATHutrice

LLM-based tutor that helps EPF first-year students practise mathematical tools.

Students pick a notion (trigonometry, for example), see their progression on the competences it covers, and train on generated exercises (multiple-choice and open-answer questions) that an LLM evaluates. A chat answers their questions as they go. Teachers upload course PDFs and follow their students; Admins can additionally view the application as another user (impersonation).

The vocabulary used here and in the code (notion, competence, **connexion de développement**, **LLM endpoint**…) is defined in [`CONTEXT.md`](CONTEXT.md). Architecture decisions live in [`docs/adr/`](docs/adr/).

## Run it locally

MATHutrice is a FastAPI application (`mathutrice/app.py`) served by uvicorn. A local clone needs no Microsoft Entra credentials: it uses the [connexion de développement](#connexion-de-développement) and a SQLite database created on first start. The only thing you must provide is a key for an LLM endpoint.

### 1. Install

Requires [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv sync
```

uv installs the Python version pinned in [`.python-version`](.python-version) (3.14) and the dependency versions locked in `uv.lock`, into `.venv/`. Activate it with `. .venv/bin/activate` (Windows: `.venv\Scripts\activate`), or prefix the commands below with `uv run`.

Without uv, create a virtual environment with Python 3.14 and run `pip install -e .`; this installs from `pyproject.toml` rather than the lockfile.

### 2. Configure

The application reads its settings from environment variables, loaded from a `.env` file at the repository root. Start from the template, which lists every variable the application reads:

```sh
cp .env.example .env
```

Then set `LLM_API_KEY` to the key for your LLM endpoint. The default endpoint is Mistral: get a key at <https://console.mistral.ai> (a free account works). The other defaults in `.env.example` are right for a local clone.

| Variable | Default in `.env.example` | Purpose |
| --- | --- | --- |
| `AUTH_MODE` | `dev` | `entra` (Microsoft Entra ID) or `dev` ([connexion de développement](#connexion-de-développement)). **When unset, the application uses `entra`.** |
| `DEV_LOGIN_KEY` | empty | Optional shared key required to use the connexion de développement. Ignored when `AUTH_MODE=entra`. |
| `SESSION_SECRET` | placeholder | Signs the session cookie. Required. Replace the placeholder on any deployed environment (see [below](#session_secret)). |
| `DATABASE_URL` | `sqlite:///./mathutrice.db` | SQLAlchemy URL. Deployed environments point it at PostgreSQL. |
| `LLM_BASE_URL` | Mistral | OpenAI-compatible endpoint every LLM call goes through. Required. |
| `LLM_MODEL` | `ministral-14b-latest` | Model served by that endpoint. Required. |
| `LLM_API_KEY` | empty | Key for that endpoint. Required: you must fill it in. |
| `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID` | commented out | Entra app registration. Required when `AUTH_MODE=entra`, unused otherwise. |
| `REDIRECT_URL` | commented out | Entra callback, the application's `/auth` route (e.g. `https://example.org/auth`). Required when `AUTH_MODE=entra`, unused otherwise. |
| `POST_LOGOUT_REDIRECT_URL` | commented out | Where Entra sends the user after sign-out. Required when `AUTH_MODE=entra`, unused otherwise. |

The application refuses to start when a required variable is missing (`ValueError: ... missing`) or when `AUTH_MODE` is neither `entra` nor `dev`.

### 3. Start

From the repository root:

```sh
uvicorn mathutrice.app:app --reload --port 8000
```

Open <http://localhost:8000/>. `--reload` restarts the server when you edit the code; leave it out outside development.

### 4. Check that it works

[`docs/smoke-test.md`](docs/smoke-test.md) is the reference check that a fresh clone runs, locally and on the Linux host you deploy to. Follow it after cloning and before opening a pull request that touches setup or configuration.

## Connexion de développement

With `AUTH_MODE=dev`, you sign in without an identity provider: you pick an email address and a role (Student, Teacher or Admin) and are signed in as that user, **with no proof of identity**. It is meant for local clones, forks and environments without Entra credentials, and must never be used in production. While it is active, the application prints `WARNING: AUTH_MODE=dev, connexion de développement active ...` at startup and shows a red banner on every page.

`AUTH_MODE` defaults to `entra`: the connexion de développement only exists when you set `AUTH_MODE=dev` explicitly, as `.env.example` does. In `entra` mode, the `/dev/login` routes are not registered at all.

### Signing in

Opening the application while signed out takes you to `/dev/login`. There you can either click an existing user, grouped by role, or enter any email address and a role. The user is created on first sign-in, so this works on an empty database.

- The address must end in `@epfedu.fr` or `@epf.fr`, as with Entra.
- The role you pick is stored on the user, replacing their previous role. If you leave it out, an existing user keeps theirs and a new one becomes a Student.
- Students land on `/`, Teachers and Admins on `/teacher`.
- `/logout` clears the session and brings you back to the sign-in page. `REDIRECT_URL` and `POST_LOGOUT_REDIRECT_URL` are not used in this mode.

### `DEV_LOGIN_KEY`

If `DEV_LOGIN_KEY` is set, signing in also requires that key; a wrong or missing key gets `401`. Use it on shared environments (a fork deployed for a demo, say) so that not everyone who can reach the URL can sign in as anyone. Leave it empty on your own machine.

### `SESSION_SECRET`

`DEV_LOGIN_KEY` only guards the sign-in form. Once signed in, you are identified by a session cookie signed with `SESSION_SECRET`. The value in `.env.example` is a public placeholder: **with it, anyone can forge a session cookie for any user and role, and skip `DEV_LOGIN_KEY` entirely.** On any environment reachable by someone other than you, set `SESSION_SECRET` to a random secret, for example:

```sh
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Scripted sign-in

`POST /dev/login` takes a form with `email`, and optionally `role` (`student`, `teacher` or `admin`), `name` and `key`. On success it answers `303` and sets the session cookie. Keep that cookie in a cookie jar to make authenticated requests:

```sh
# Sign in; -c saves the session cookie to cookies.txt
curl -s -o /dev/null -w '%{http_code}\n' -c cookies.txt \
  -d email=camille.martin@epfedu.fr -d role=student -d key="$DEV_LOGIN_KEY" \
  http://localhost:8000/dev/login
# 303

# Reuse it; -b sends the cookie, -c keeps it up to date
curl -s -o /dev/null -w '%{http_code}\n' -b cookies.txt -c cookies.txt \
  http://localhost:8000/
# 200 (without the cookie: 307, a redirect to sign-in)
```

Drop `-d key=...` when `DEV_LOGIN_KEY` is empty. The session lasts one hour.

## Deployment checklist

Before deploying to any environment real users can reach:

- [ ] `AUTH_MODE` non défini ou `entra` (unset or `entra`, never `dev`).
- [ ] `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID`, `REDIRECT_URL` and `POST_LOGOUT_REDIRECT_URL` set for the Entra app registration, and `REDIRECT_URL` registered as a redirect URI in Entra.
- [ ] `SESSION_SECRET` set to a random secret, not the `.env.example` placeholder.
- [ ] `DEV_LOGIN_KEY` empty (it is ignored in `entra` mode; the application warns if it is set).
- [ ] Served over HTTPS: in `entra` mode, the session cookie is only sent over HTTPS.
- [ ] `DATABASE_URL` points at PostgreSQL, not the local SQLite file.
- [ ] `LLM_BASE_URL`, `LLM_MODEL` and `LLM_API_KEY` set for the production LLM endpoint. On Mistral's free plan, requests may be used for training: for real data, turn that off in the Mistral admin panel under Privacy.
- [ ] [`docs/smoke-test.md`](docs/smoke-test.md) passes on the host.

## Contributing

- Package boundaries are machine-checked: read [`mathutrice/README.md`](mathutrice/README.md) before adding a package or importing across one; `uv run tach check` enforces them.
- Issues are tracked on GitHub; pull requests for the 2026–27 course target `course-2026` (see [ADR 0001](docs/adr/0001-course-2026-default-branch.md)).

## License

[MIT](LICENSE)
