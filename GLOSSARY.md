# MATHutrice

LLM-based tutor that helps EPF first-year students practise mathematical tools through notions, competences and training.

## Language

### Authentication

**Connexion de développement** (`AUTH_MODE=dev`):
Sign-in without an identity provider: you pick an email address and a role (Student, Teacher or Admin) and are signed in as that user, with no proof of identity. It does not depend on any pre-existing user. It only exists when `AUTH_MODE=dev` and must never be used in production.
_Avoid_: impersonation, usurpation, fake login

**Impersonation**:
A really authenticated Admin views the application as another user, then switches back to their own account. It assumes a real sign-in behind it, unlike the **connexion de développement**.
_Avoid_: connexion de développement, dev login

### LLM

**LLM endpoint**:
The OpenAI-compatible API every LLM call goes through, set by `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL`. The self-hosted gateway and Mistral are both LLM endpoints.
_Avoid_: Mistral (when meaning the endpoint in general)
