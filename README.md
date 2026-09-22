# MATHutrice

An LLM-based tutor that helps EPF first-year students practise mathematical
tools. A student signs in, picks a **notion** (trigonometry, logarithms,
polynomials…), and works through **competences** with generated exercises —
multiple choice, open answer, and step by step. Answers are corrected by the
**LLM endpoint**, scores are stored per competence, and the tutor recommends
the notions a student is weakest on. A free chat is available alongside the
exercises. Teachers and admins get their own pages, including PDF upload.

The vocabulary this project uses — *notion*, *competence*, **connexion de
développement**, **LLM endpoint** — is defined in [`CONTEXT.md`](CONTEXT.md).
Decisions that shaped the code are in [`docs/adr/`](docs/adr/).

## Run it locally

Requires **Python 3.14** (the exact version is in `.python-version`) and
[uv](https://docs.astral.sh/uv/), which installs that Python and the locked
dependency versions from `uv.lock`.

```sh
uv sync
. .venv/bin/activate
cp .env.example .env
```

Then set `LLM_API_KEY` in `.env` to the key for your LLM endpoint. Everything
else in `.env.example` has a working default for a local run. Start the
application from the repository root:

```sh
uvicorn mathutrice.app:app --port 8000
```

It listens on <http://localhost:8000/>.

On the first start the tables are created and the reference data is inserted:
the notions and competences of `referentiel.py`, and one demonstration account
per role:

| Email | Role |
| --- | --- |
| `eleve@epf.fr` | Student |
| `enseignant@epf.fr` | Teacher |
| `admin@epf.fr` | Admin |

Only rows that are missing get inserted, so nothing is written when the data is
already there and restarting is safe. Only `@epf.fr` and `@epfedu.fr` addresses
may sign in.

To check that a fresh clone actually works, follow
[`docs/smoke-test.md`](docs/smoke-test.md).

## Configuration

Every variable the application reads is listed, with comments, in
`.env.example`. In short:

| Variable | Meaning |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy URL. SQLite by default; PostgreSQL where it is deployed. |
| `LLM_BASE_URL` | The OpenAI-compatible **LLM endpoint** every LLM call goes through. **Required.** |
| `LLM_MODEL` | The model served by that endpoint. **Required.** |
| `LLM_API_KEY` | The key for that endpoint. **Required.** |
| `SESSION_SECRET` | Signing key for the session cookie. **Required.** |
| `AUTH_MODE` | `entra` (the default, Microsoft Entra ID) or `dev`. |
| `DEV_LOGIN_KEY` | Optional shared key protecting the `/dev/login` page. Ignored when `AUTH_MODE=entra`. |
| `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID` | The Entra application. Needed when `AUTH_MODE=entra`. |
| `REDIRECT_URL` | Where Entra sends the user back after sign-in — this host's `/auth`. |
| `POST_LOGOUT_REDIRECT_URL` | Where Entra sends the user after sign-out — this host's `/test_login`. |

The LLM defaults in `.env.example` use the Mistral API: get a key at
<https://console.mistral.ai> (a free account works). Any other OpenAI-compatible
endpoint works by changing the three `LLM_*` variables.

No key belongs in the repository. `.env` is not committed; `.env.example` holds
names and placeholders only.

## Connexion de développement (`AUTH_MODE=dev`)

With `AUTH_MODE=dev`, the application serves `/dev/login`, where you pick an
email address and a role and are signed in as that user **with no proof of
identity**. It is what lets you run MATHutrice without Entra credentials. A red
banner shows on every application page while it is on.

**It must never reach production.** In the deployment checklist:

- `AUTH_MODE` unset or `entra`
- `SESSION_SECRET` set to a random value, not the placeholder
- `DATABASE_URL` pointing at the deployed database
- `LLM_API_KEY` supplied by the environment, never committed
- `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID`, `REDIRECT_URL` and
  `POST_LOGOUT_REDIRECT_URL` set for the real host

`DEV_LOGIN_KEY` narrows who can reach the `/dev/login` page: when it is set, the
page asks for it and refuses the sign-in without it.

**`SESSION_SECRET` is the real gate, not `DEV_LOGIN_KEY`.** The session cookie
is signed with `SESSION_SECRET`; anyone who knows its value can forge a cookie
for any user and any role, and never sees the sign-in page at all. Leaving the
placeholder from `.env.example` in place on a reachable host makes
`DEV_LOGIN_KEY` decorative.

Signing in from a script means keeping the session cookie between requests:

```sh
curl -s -c cookies.txt -X POST \
  -d 'email=eleve@epf.fr&role=student' \
  http://localhost:8000/dev/login

curl -s -b cookies.txt http://localhost:8000/
```

The first call answers `303` with a `Set-Cookie: session=…`. `role` is
`student`, `teacher` or `admin`; `name` is optional and defaults to the existing
user's name, or one derived from the address. Add `-d 'key=…'` when
`DEV_LOGIN_KEY` is set.

## Layout

```
mathutrice/            the application package
  app.py               FastAPI routes
  models.py            the SQLModel tables
  database.py          the engine and session
  referentiel.py       the notions and competences reference data
  seed.py              inserts the missing reference data at startup
  llm_client.py        the OpenAI-compatible client every LLM call goes through
  fonctions_python/    exercise generation and correction
  lacune_evaluation/   gap detection from a student's answers
  templates/, static/  the pages
  rag_documents/       PDFs uploaded by teachers (runtime data, kept out of the wheel)
docs/                  the smoke test and the ADRs
scripts/               the architecture checks
```

Package boundaries are machine-checked: a name starting with `_` cannot be
imported from outside its package, and packages may not form an import cycle.
Read [`mathutrice/README.md`](mathutrice/README.md) before adding a package or
importing across one, and run the checks with:

```sh
tach check
python scripts/check_cycles.py
```

## Licence

MIT — see [`LICENSE`](LICENSE).
