from fastapi import FastAPI, Request, Depends, UploadFile, File, Form
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import List as TList, Optional
from urllib.parse import unquote
from contextlib import asynccontextmanager
from sqlmodel import SQLModel, select, delete
from dotenv import load_dotenv
from mathutrice.database import engine, get_session, Session
from mathutrice.database import Session as DBSession
from apscheduler.schedulers.background import BackgroundScheduler
from decimal import Decimal
from mathutrice import models
from mathutrice.referentiel import REFERENTIEL
from mathutrice.seed import seed_missing
import msal
import uvicorn
import shutil
import os
import uuid
import json
import hmac
import random
import traceback
from datetime import datetime, timedelta


load_dotenv()


# ------------------------------------------------------------------
# Referentiel
# ------------------------------------------------------------------


def get_referentiel():
    return REFERENTIEL


# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


# ------------------------------------------------------------------
# Cleanup — supprime conversations + messages de plus de 24h
# ------------------------------------------------------------------


def cleanup_old_conversations():
    with DBSession(engine) as session:
        cutoff = datetime.utcnow() - timedelta(hours=24)

        old_conv_ids = session.exec(
            select(models.Conversation.conversation_id).where(
                models.Conversation.started_at < cutoff
            )
        ).all()

        for conv_id in old_conv_ids:
            session.exec(
                delete(models.Message).where(models.Message.conversation_id == conv_id)
            )
            session.exec(
                delete(models.Conversation).where(
                    models.Conversation.conversation_id == conv_id
                )
            )

        session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    seed_missing(engine)

    scheduler = BackgroundScheduler()
    scheduler.add_job(cleanup_old_conversations, "interval", hours=1)
    scheduler.start()

    yield

    scheduler.shutdown()


# ------------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------------


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_SECRET = os.getenv("SESSION_SECRET")

if not SESSION_SECRET:
    raise ValueError("SESSION_SECRET missing")

AUTH_MODE = (os.getenv("AUTH_MODE") or "entra").strip().lower()

if AUTH_MODE not in ("entra", "dev"):
    raise ValueError(
        f"AUTH_MODE invalid: {os.getenv('AUTH_MODE')!r} (expected 'entra' or 'dev')"
    )

DEV_LOGIN_KEY = (os.getenv("DEV_LOGIN_KEY") or "").strip() or None

if AUTH_MODE == "dev":
    key_status = "défini" if DEV_LOGIN_KEY else "non défini"
    print(
        "WARNING: AUTH_MODE=dev, connexion de développement active "
        "(aucune authentification, ne jamais utiliser en production), "
        f"DEV_LOGIN_KEY {key_status}"
    )
elif DEV_LOGIN_KEY:
    print("WARNING: DEV_LOGIN_KEY défini mais ignoré (AUTH_MODE=entra)")

# Refuse de démarrer sans LLM_BASE_URL, LLM_API_KEY et LLM_MODEL.
# Tout ce qui suit importe le client LLM, d'où la place de ces imports.
from mathutrice.llm_client import client, MODEL  # noqa: E402
from mathutrice.fonctions_python.chatbot import (  # noqa: E402
    chat,
    chat_stream_with_history,
    reset_conversation,
)
from mathutrice.fonctions_python.main import generate_mixed_test  # noqa: E402
from mathutrice.fonctions_python.session_generator import (  # noqa: E402
    build_notion_data_with_scores,
    generate_next_question,
    generate_positioning_session,
    get_competence_map_by_codes,
    init_progressions_for_user,
    is_first_session,
    persist_score_update,
)
from mathutrice.fonctions_python.type_questions.qcm_generator import (  # noqa: E402
    generate_qcm_test,
)
from mathutrice.fonctions_python.type_questions.qro_generator import (  # noqa: E402
    evaluate_answer,
    generate_qro_test,
)
from mathutrice.lacune_evaluation.LLM_as_Evaluator import (  # noqa: E402
    diagnostiquer_depuis_competence,
)

app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        https_only=AUTH_MODE != "dev",
        same_site="lax",
        max_age=3600,
)


# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "static")),
    name="static",
)

templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
templates.env.globals["auth_mode"] = AUTH_MODE
templates.env.globals["dev_login_key_set"] = bool(DEV_LOGIN_KEY)

UPLOAD_DIR = os.path.join(BASE_DIR, "rag_documents")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ------------------------------------------------------------------
# Azure AD configuration
# ------------------------------------------------------------------


CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
TENANT_ID = os.getenv("TENANT_ID")

if AUTH_MODE == "entra":
    if not CLIENT_ID:
        raise ValueError("CLIENT_ID missing")

    if not CLIENT_SECRET:
        raise ValueError("CLIENT_SECRET missing")

    if not TENANT_ID:
        raise ValueError("TENANT_ID missing")

REDIRECT_URL = os.getenv("REDIRECT_URL")
POST_LOGOUT_REDIRECT_URL = os.getenv("POST_LOGOUT_REDIRECT_URL")

if AUTH_MODE == "entra":
    if not REDIRECT_URL:
        raise ValueError("REDIRECT_URL missing")

    if not POST_LOGOUT_REDIRECT_URL:
        raise ValueError("POST_LOGOUT_REDIRECT_URL missing")

SCOPE = ["User.Read"]
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"


# ------------------------------------------------------------------
# MSAL helper
# ------------------------------------------------------------------


def get_msal_app():
    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )


# ------------------------------------------------------------------
# Session helper
# ------------------------------------------------------------------


def get_current_user(request: Request):
    return request.session.get("user")


# ------------------------------------------------------------------
# Sign-in helpers
# ------------------------------------------------------------------


def is_allowed_email(email: Optional[str]) -> bool:
    return bool(email) and email.endswith(("@epfedu.fr", "@epf.fr"))


def sign_in(
    request: Request,
    session: Session,
    email: str,
    name: str,
    role: Optional[str] = None,
) -> str:
    now = datetime.utcnow()

    user = session.exec(
        select(models.User).where(models.User.email == email)
    ).first()

    if user:
        user.last_active = now
        if role:
            user.role = role

    else:
        user = models.User(
            sso_id=uuid.uuid4(),
            name=name,
            email=email,
            role=role or "Student",
            created_at=now,
            last_active=now,
        )

    session.add(user)
    session.commit()

    init_progressions_for_user(user.sso_id, session)

    request.session["user"] = {
        "email": email,
        "name": name,
        "role": user.role,
        "impersonate": False,
    }

    if user.role in ("Teacher", "Admin"):
        return "/teacher"

    return "/"


# ------------------------------------------------------------------
# LOGIN
# ------------------------------------------------------------------


@app.get("/test_login")
async def login(request: Request):
    if AUTH_MODE == "dev":
        return RedirectResponse("/dev/login")

    msal_app = get_msal_app()

    #state en prod à activer dans /auth en prod
    state = str(uuid.uuid4())
    request.session["oauth_state"] = state

    print("STATE CREATED =", state)
    print("SESSION AFTER LOGIN =", dict(request.session))

    auth_url = msal_app.get_authorization_request_url(
        scopes=SCOPE,
        redirect_uri=REDIRECT_URL,
        state=state,
    )

    return RedirectResponse(auth_url)


# ------------------------------------------------------------------
# DEV LOGIN (AUTH_MODE=dev)
# ------------------------------------------------------------------


DEV_ROLES = {"student": "Student", "teacher": "Teacher", "admin": "Admin"}


def name_from_email(email: str) -> str:
    local_part = email.split("@")[0]
    return local_part.replace(".", " ").replace("_", " ").title()


async def dev_login_page(
    request: Request,
    session: Session = Depends(get_session),
):
    users = session.exec(
        select(models.User).order_by(models.User.name, models.User.email)
    ).all()

    users_by_role = {role: [] for role in DEV_ROLES.values()}

    for user in users:
        users_by_role.setdefault(user.role, []).append(user)

    return templates.TemplateResponse(
        "dev_login.html",
        {
            "request": request,
            "users_by_role": users_by_role,
            "roles": list(DEV_ROLES.values()),
            "current_user": get_current_user(request),
            "key_required": bool(DEV_LOGIN_KEY),
        },
    )


async def dev_login(
    request: Request,
    email: str = Form(...),
    name: Optional[str] = Form(None),
    role: Optional[str] = Form(None),
    key: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    if DEV_LOGIN_KEY and not hmac.compare_digest(
        (key or "").encode(), DEV_LOGIN_KEY.encode()
    ):
        return HTMLResponse(
            "<h2>🔒 Clé invalide</h2><p>Clé de connexion requise.</p>",
            status_code=401,
        )

    email = email.strip()

    if not is_allowed_email(email):
        return HTMLResponse(
            """
            <h2>⛔ Accès refusé</h2>
            <p>Adresse EPF obligatoire.</p>
            """,
            status_code=403,
        )

    role = (role or "").strip()

    if role:
        role = DEV_ROLES.get(role.lower())

        if not role:
            return HTMLResponse(
                "<h2>❌ Rôle invalide</h2><p>Student, Teacher ou Admin.</p>",
                status_code=400,
            )

    name = name.strip() if name else ""

    if not name:
        user = session.exec(
            select(models.User).where(models.User.email == email)
        ).first()
        name = user.name if user else name_from_email(email)

    redirect_path = sign_in(request, session, email, name, role)

    return RedirectResponse(redirect_path, status_code=303)


if AUTH_MODE == "dev":
    app.get("/dev/login", response_class=HTMLResponse)(dev_login_page)
    app.post("/dev/login")(dev_login)


# ------------------------------------------------------------------
# AUTH CALLBACK
# ------------------------------------------------------------------


async def auth_callback(
    request: Request,
    code: str = None,
    state: str = None,
    error: str = None,
    session: Session = Depends(get_session),
):
    if error:
        return HTMLResponse(
            f"<h2>❌ Erreur Azure</h2><pre>{error}</pre>",
            status_code=400,
        )

    # Vérification OAuth state désactivée temporairement en préprod
# À réactiver plus tard avec un state signé ou une session stable.
    # saved_state = request.session.get("oauth_state")

    # print("STATE URL =", state)
    # print("STATE SESSION =", saved_state)
    # print("SESSION CONTENT =", dict(request.session))

    # if not state or state != saved_state:
    #     return HTMLResponse("<h2>❌ Invalid OAuth state</h2>", status_code=400)

    # request.session.pop("oauth_state", None)

    msal_app = get_msal_app()

    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPE,
        redirect_uri=REDIRECT_URL,
    )

    if "error" in result:
        return HTMLResponse(
            f"""
            <h2>❌ Erreur token</h2>
            <pre>{json.dumps(result, indent=2)}</pre>
            """,
            status_code=400,
        )

    claims = result.get("id_token_claims", {})
    email = claims.get("email") or claims.get("preferred_username")

    if not is_allowed_email(email):
        return HTMLResponse(
            """
            <h2>⛔ Accès refusé</h2>
            <p>Adresse EPF obligatoire.</p>
            """,
            status_code=403,
        )

    name = claims.get("name", "Unknown User")

    redirect_path = sign_in(request, session, email, name)

    return RedirectResponse(redirect_path, status_code=302)


if AUTH_MODE == "entra":
    app.get("/auth")(auth_callback)


# ------------------------------------------------------------------
# LOGOUT
# ------------------------------------------------------------------


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()

    if AUTH_MODE == "dev":
        return RedirectResponse("/")

    logout_url = (
        f"https://login.microsoftonline.com/"
        f"{TENANT_ID}/oauth2/v2.0/logout"
        f"?post_logout_redirect_uri="
        f"{POST_LOGOUT_REDIRECT_URL}"
    )

    return RedirectResponse(logout_url)


# ------------------------------------------------------------------
# STOP IMPERSONATE
# ------------------------------------------------------------------


@app.get("/impersonate/stop")
async def stop_impersonate(request: Request):
    current_user = get_current_user(request)

    if not current_user:
        return RedirectResponse("/test_login")

    if not current_user.get("impersonate"):
        return RedirectResponse("/")

    admin_email = current_user.get("real_admin")

    request.session["user"] = {
        "email": admin_email,
        "name": admin_email,
        "role": "Admin",
        "impersonate": False,
    }

    return RedirectResponse("/teacher")


# ------------------------------------------------------------------
# IMPERSONATE
# ------------------------------------------------------------------


@app.get("/impersonate/{email}")
async def impersonate(
    request: Request,
    email: str,
    session: Session = Depends(get_session),
):
    current_user = get_current_user(request)

    if not current_user or current_user.get("role") != "Admin":
        return HTMLResponse(
            "<h2>⛔ Accès refusé</h2>",
            status_code=403,
        )

    email = unquote(email)

    target = session.exec(select(models.User).where(models.User.email == email)).first()

    target_role = target.role if target else "Student"

    request.session["user"] = {
        "email": email,
        "name": target.name if target else email,
        "role": target_role,
        "impersonate": True,
        "real_admin": current_user["email"],
    }

    if target_role in ("Teacher", "Admin"):
        return RedirectResponse("/teacher", status_code=302)

    return RedirectResponse("/", status_code=302)


# ------------------------------------------------------------------
# TEACHER PAGE
# ------------------------------------------------------------------


@app.get("/teacher", response_class=HTMLResponse)
async def teacher_page(request: Request):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/test_login")

    if user.get("role") not in ("Teacher", "Admin"):
        return RedirectResponse("/")

    return templates.TemplateResponse(
        "upload.html",
        {"request": request},
    )


# ------------------------------------------------------------------
# PDF Upload
# ------------------------------------------------------------------


@app.post("/upload/pdf")
async def upload_pdf(
    request: Request,
    files: TList[UploadFile] = File(...),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Non connecté"},
        )

    if user.get("role") not in ("Teacher", "Admin"):
        return JSONResponse(
            status_code=403,
            content={"detail": "Accès refusé"},
        )

    email_prefix = user["email"].replace("@", "_at_")

    saved = []

    for file in files:
        if file.content_type != "application/pdf":
            continue

        filename = f"{email_prefix}__{file.filename}"
        dest = os.path.join(UPLOAD_DIR, filename)

        with open(dest, "wb") as f:
            shutil.copyfileobj(file.file, f)

        saved.append(file.filename)

    return {
        "ok": True,
        "message": f"{len(saved)} fichier(s) uploadé(s).",
        "files": saved,
    }


# ------------------------------------------------------------------
# PDF List
# ------------------------------------------------------------------


@app.get("/upload/list")
async def list_pdfs(request: Request):
    user = get_current_user(request)

    if not user:
        return JSONResponse(
            status_code=401,
            content={"detail": "Non connecté"},
        )

    if user.get("role") not in ("Teacher", "Admin"):
        return JSONResponse(
            status_code=403,
            content={"detail": "Accès refusé"},
        )

    email_prefix = user["email"].replace("@", "_at_")

    files = []

    for filename in os.listdir(UPLOAD_DIR):
        if not filename.endswith(".pdf"):
            continue

        if user.get("role") == "Teacher" and not filename.startswith(email_prefix):
            continue

        path = os.path.join(UPLOAD_DIR, filename)
        stat = os.stat(path)

        parts = filename.split("__", 1)

        display_name = parts[1] if len(parts) == 2 else filename
        uploader = parts[0].replace("_at_", "@") if len(parts) == 2 else "unknown"

        files.append(
            {
                "name": display_name,
                "uploader": uploader,
                "size": f"{stat.st_size / (1024 * 1024):.1f} MB",
                "date": datetime.fromtimestamp(stat.st_mtime).strftime(
                    "%d/%m/%Y %H:%M"
                ),
            }
        )

    files.sort(
        key=lambda x: x["date"],
        reverse=True,
    )

    return {
        "ok": True,
        "files": files,
    }


# ------------------------------------------------------------------
# HOME
# ------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def home_page(
    request: Request,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/test_login")

    if user.get("role") == "Teacher":
        return RedirectResponse("/teacher")

    notions = session.exec(select(models.Notion.notion_id, models.Notion.title)).all()

    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "notions": notions,
            "name": user.get("name").split(" ")[0],
        },
    )


# ------------------------------------------------------------------
# MODULE PAGE
# ------------------------------------------------------------------

@app.get("/module", response_class=HTMLResponse)
async def module_page(
    request: Request,
    id: str,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/test_login")

    if user.get("role") == "Teacher":
        return RedirectResponse("/teacher")

    try:
        notion_uuid = uuid.UUID(id)
    except ValueError:
        return HTMLResponse(
            "<h2>Identifiant de module invalide</h2>",
            status_code=400,
        )

    notion = session.exec(
        select(models.Notion).where(models.Notion.notion_id == notion_uuid)
    ).first()

    if not notion:
        return HTMLResponse(
            "<h2>Module introuvable</h2>",
            status_code=404,
        )

    return templates.TemplateResponse(
        "module.html",
        {
            "request": request,
            "notion": notion,
            "module_id": str(notion.notion_id),
            "notion_key": notion.referentiel_key,
        },
    )

# ------------------------------------------------------------------
# CHAT PAGE
# ------------------------------------------------------------------


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(
    request: Request,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/test_login")

    if user.get("role") == "Teacher":
        return RedirectResponse("/teacher")

    notions = session.exec(select(models.Notion.notion_id, models.Notion.title)).all()

    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "notions": notions,
        },
    )


# ------------------------------------------------------------------
# QCM PAGE
# ------------------------------------------------------------------


@app.get("/qcm", response_class=HTMLResponse)
async def qcm_page(request: Request):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/test_login")

    if user.get("role") == "Teacher":
        return RedirectResponse("/teacher")

    return templates.TemplateResponse(
        "qcm.html",
        {"request": request},
    )


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------


class QCMRequest(BaseModel):
    notion: str = "trigonométrie"
    niveau: str = "intermédiaire"
    n: int = 9


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class SessionHistoryRequest(BaseModel):
    notion_key: str
    session_type: str
    score_total: int
    score_max: int
    type_stats: dict
    started_at: Optional[str] = None


class ResetRequest(BaseModel):
    notion_key: str


class SessionRequest(BaseModel):
    notion_key: str


class SubmitAnswerRequest(BaseModel):
    notion_key: str
    competences_dict: dict
    question_type: str
    question_niveau: str


class EvaluateQRORequest(BaseModel):
    question: str
    correct_answer: str
    user_answer: str


class FeedbackRequest(BaseModel):
    question: str
    correct_answer: str
    user_answer: str
    attempt: int
    competence: dict
    notion_nom: str
    notion_key: Optional[str] = None
    question_type: str


class NextTargetedRequest(BaseModel):
    notion_key: str
    competence_code: str


class TrainingStartedRequest(BaseModel):
    notion_key: str


class EvaluationRequest(BaseModel):
    notion_key: str
    n_questions: int = 10


# ------------------------------------------------------------------
# CONVERSATIONS — liste les conversations de l'utilisateur
# ------------------------------------------------------------------


@app.get("/conversations")
async def list_conversations(
    request: Request,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    conversations = session.exec(
        select(models.Conversation)
        .where(models.Conversation.sso_id == sso_id)
        .order_by(models.Conversation.updated_at.desc())
    ).all()

    return {
        "ok": True,
        "conversations": [
            {
                "id": str(c.conversation_id),
                "title": c.title,
                "updated_at": c.updated_at.isoformat(),
                "started_at": c.started_at.isoformat(),
            }
            for c in conversations
        ],
    }


# ------------------------------------------------------------------
# CONVERSATIONS — charge une conversation complète
# ------------------------------------------------------------------


@app.get("/conversations/{conversation_id}")
async def get_conversation(
    request: Request,
    conversation_id: str,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    conv = session.exec(
        select(models.Conversation).where(
            models.Conversation.conversation_id == conversation_id
        )
    ).first()

    if not conv:
        return JSONResponse(
            status_code=404,
            content={"detail": "Conversation introuvable"},
        )

    messages = session.exec(
        select(models.Message)
        .where(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.sent_at.asc())
    ).all()

    return {
        "ok": True,
        "conversation": {
            "id": str(conv.conversation_id),
            "title": conv.title,
        },
        "messages": [{"role": m.role, "content": m.content} for m in messages],
    }


# ------------------------------------------------------------------
# Chat streaming endpoint
# ------------------------------------------------------------------


@app.post("/chat/stream")
async def chat_stream_endpoint(
    request: Request,
    data: ChatRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
    select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    now = datetime.utcnow()
    conversation_id = data.conversation_id

    if not conversation_id:
        conversation_id = uuid.uuid4()
        title = data.message[:47] + "..." if len(data.message) > 47 else data.message

        new_conv = models.Conversation(
            conversation_id=conversation_id,
            title=title,
            status="active",
            context_type="chat_libre",
            started_at=now,
            updated_at=now,
            sso_id=sso_id,
        )

        session.add(new_conv)
        session.commit()

    user_msg = models.Message(
        message_id=uuid.uuid4(),
        role="user",
        content=data.message,
        sent_at=now,
        conversation_id=conversation_id,
    )

    session.add(user_msg)
    session.commit()

    all_messages = session.exec(
        select(models.Message)
        .where(models.Message.conversation_id == conversation_id)
        .order_by(models.Message.sent_at.asc())
    ).all()

    history = [{"role": m.role, "content": m.content} for m in all_messages[-10:]]

    full_response = []

    def generate():
        for chunk in chat_stream_with_history(history):
            full_response.append(chunk)
            yield f"data: {chunk}\n\n"

        yield f"data: [CONV_ID:{conversation_id}]\n\n"
        yield "data: [DONE]\n\n"

        assistant_msg = models.Message(
            message_id=uuid.uuid4(),
            role="assistant",
            content="".join(full_response),
            sent_at=datetime.utcnow(),
            conversation_id=conversation_id,
        )

        session.add(assistant_msg)

        conv = session.exec(
            select(models.Conversation).where(
                models.Conversation.conversation_id == conversation_id
            )
        ).first()

        if conv:
            conv.updated_at = datetime.utcnow()
            session.add(conv)

        session.commit()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------
# Chat reset
# ------------------------------------------------------------------


@app.post("/chat/reset")
async def chat_reset_endpoint():
    reset_conversation()

    return {
        "ok": True,
        "message": "Conversation reinitialisee",
    }


# ------------------------------------------------------------------
# Chat complete response
# ------------------------------------------------------------------


@app.post("/chat/complete")
async def chat_complete_endpoint(data: ChatRequest):
    try:
        response = chat(data.message)

        return {
            "ok": True,
            "response": response,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": str(e),
            },
        )


# ------------------------------------------------------------------
# SESSION — reset scores d'une notion pour un élève
# ------------------------------------------------------------------


@app.post("/session/reset")
async def reset_session_endpoint(
    request: Request,
    data: ResetRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    REFERENTIEL = get_referentiel()

    try:
        if data.notion_key not in REFERENTIEL:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Notion inconnue"},
            )

        notion = session.exec(
            select(models.Notion).where(
                models.Notion.referentiel_key == data.notion_key
            )
        ).first()

        if not notion:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Notion inconnue en BDD"},
            )

        codes = [
            comp["code"]
            for comp in REFERENTIEL[data.notion_key]["competences"]
        ]

        code_to_competence = get_competence_map_by_codes(codes, session)

        missing_codes = [
            code
            for code in codes
            if code not in code_to_competence
        ]

        if missing_codes:
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "error": "Compétences absentes en BDD",
                    "missing_codes": missing_codes,
                },
            )

        competence_ids = [
            competence.competence_id
            for competence in code_to_competence.values()
        ]

        rows = session.exec(
            select(models.Progression).where(
                models.Progression.sso_id == sso_id,
                models.Progression.competence_id.in_(competence_ids),
            )
        ).all()

        if len(rows) < len(competence_ids):
            init_progressions_for_user(sso_id, session)

            rows = session.exec(
                select(models.Progression).where(
                    models.Progression.sso_id == sso_id,
                    models.Progression.competence_id.in_(competence_ids),
                )
            ).all()

        now = datetime.utcnow()

        for prog in rows:
            prog.score = Decimal("0.50")
            prog.level = "moyen"
            prog.attempts_count = 0
            prog.updated_at = None
            session.add(prog)

        notion_prog = session.exec(
            select(models.NotionProgress).where(
                models.NotionProgress.sso_id == sso_id,
                models.NotionProgress.notion_id == notion.notion_id,
            )
        ).first()

        if notion_prog:
            notion_prog.training_started = False
            notion_prog.updated_at = now
            session.add(notion_prog)

        session.commit()

        return {
            "ok": True,
            "reset_count": len(rows),
            "notion_key": data.notion_key,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION PAGE
# ------------------------------------------------------------------


@app.get("/session", response_class=HTMLResponse)
async def session_page(request: Request):
    user = get_current_user(request)

    if not user:
        return RedirectResponse("/test_login")

    if user.get("role") == "Teacher":
        return RedirectResponse("/teacher")

    return templates.TemplateResponse("session.html", {"request": request})


# ------------------------------------------------------------------
# SESSION — check première session
# ------------------------------------------------------------------


@app.get("/session/check")
async def check_session(
    request: Request,
    notion_key: str,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    notion = session.exec(
        select(models.Notion).where(
            models.Notion.referentiel_key == notion_key
        )
    ).first()

    if not notion:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Notion inconnue"},
        )

    try:
        first = is_first_session(notion_key, sso_id, session)
    except ValueError as e:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": str(e)},
        )

    notion_prog = session.exec(
        select(models.NotionProgress).where(
            models.NotionProgress.sso_id == sso_id,
            models.NotionProgress.notion_id == notion.notion_id,
        )
    ).first()

    training_started = notion_prog.training_started if notion_prog else False

    return {
        "ok": True,
        "first_session": first,
        "training_started": training_started,
    }


# ------------------------------------------------------------------
# SESSION — génération positionnement
# ------------------------------------------------------------------


@app.post("/session/positioning")
async def positioning_endpoint(
    request: Request,
    data: SessionRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    try:
        result = generate_positioning_session(data.notion_key, sso_id, session)
        return {"ok": True, **result}

    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ------------------------------------------------------------------
# SESSION — génération question entraînement
# ------------------------------------------------------------------


@app.post("/session/next")
async def next_question_endpoint(
    request: Request,
    data: SessionRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    try:
        result = generate_next_question(data.notion_key, sso_id, session)
        return {"ok": True, **result}

    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "error": str(e)})

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ------------------------------------------------------------------
# SESSION — soumettre une réponse
# ------------------------------------------------------------------


@app.post("/session/submit")
async def submit_answer_endpoint(
    request: Request,
    data: SubmitAnswerRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    try:
        result = persist_score_update(
            sso_id=sso_id,
            competences_dict=data.competences_dict,
            question_type=data.question_type,
            question_niveau=data.question_niveau,
            db=session,
        )

        return {"ok": True, "new_scores": result}

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ------------------------------------------------------------------
# SESSION — scores réels d'un élève pour une notion
# ------------------------------------------------------------------


@app.get("/session/scores")
async def get_scores_endpoint(
    request: Request,
    notion_key: str,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    REFERENTIEL = get_referentiel()

    if notion_key not in REFERENTIEL:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Notion inconnue"},
        )

    try:
        notion_data = build_notion_data_with_scores(notion_key, sso_id, session)

        competences = []

        for comp in notion_data["competences"]:
            score = float(comp.get("score", 0.5))

            competences.append(
                {
                    "code": comp.get("code"),
                    "nom": comp.get("nom") or comp.get("title") or comp.get("code"),
                    "niveau": comp.get("niveau", "basique"),
                    "score": round(score, 2),
                    "score_pct": round(score * 100),
                }
            )

        global_score = (
            sum(c["score"] for c in competences) / len(competences)
            if competences
            else 0.0
        )

        return {
            "ok": True,
            "notion_key": notion_key,
            "notion_nom": notion_data.get("notion_nom", notion_key),
            "global_score": round(global_score, 2),
            "global_score_pct": round(global_score * 100),
            "competences": competences,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION — enregistrer historique
# ------------------------------------------------------------------


@app.post("/session/history")
async def save_session_history_endpoint(
    request: Request,
    data: SessionHistoryRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    notion = session.exec(
        select(models.Notion).where(
            models.Notion.referentiel_key == data.notion_key
        )
    ).first()

    if not notion:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Notion inconnue"},
        )

    try:
        total = max(0, int(data.score_max or 0))
        correct = max(0, int(data.score_total or 0))

        score_pct = Decimal("0.00")

        if total > 0:
            score_pct = Decimal(str(round((correct / total) * 100, 2)))

        qcm = data.type_stats.get("qcm", [0, 0])
        qro = data.type_stats.get("qro", [0, 0])
        sbs = data.type_stats.get("sbs", [0, 0])

        started_at = None

        if data.started_at:
            try:
                started_at = datetime.fromisoformat(
                    data.started_at.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except Exception:
                started_at = None

        row = models.SessionHistory(
            session_history_id=uuid.uuid4(),
            sso_id=sso_id,
            notion_id=notion.notion_id,
            session_type=data.session_type,
            score=score_pct,
            correct_count=correct,
            total_count=total,
            qcm_correct=int(qcm[0] or 0),
            qcm_total=int(qcm[1] or 0),
            qro_correct=int(qro[0] or 0),
            qro_total=int(qro[1] or 0),
            sbs_correct=int(sbs[0] or 0),
            sbs_total=int(sbs[1] or 0),
            started_at=started_at,
            ended_at=datetime.utcnow(),
        )

        session.add(row)
        session.commit()

        return {
            "ok": True,
            "session_history_id": str(row.session_history_id),
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION — lire historique
# ------------------------------------------------------------------


@app.get("/session/history")
async def get_session_history_endpoint(
    request: Request,
    notion_key: str,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    notion = session.exec(
        select(models.Notion).where(
            models.Notion.referentiel_key == notion_key
        )
    ).first()

    if not notion:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Notion inconnue"},
        )

    try:
        rows = session.exec(
            select(models.SessionHistory)
            .where(
                models.SessionHistory.sso_id == sso_id,
                models.SessionHistory.notion_id == notion.notion_id,
            )
            .order_by(models.SessionHistory.ended_at.desc())
        ).all()

        history = []

        for row in rows[:8]:
            history.append(
                {
                    "type": row.session_type,
                    "date": row.ended_at.strftime("%d/%m/%Y %H:%M"),
                    "notion": notion.title,
                    "score_pct": round(float(row.score)),
                    "score": row.correct_count,
                    "total": row.total_count,
                    "qcm": [row.qcm_correct, row.qcm_total],
                    "qro": [row.qro_correct, row.qro_total],
                    "sbs": [row.sbs_correct, row.sbs_total],
                }
            )

        return {
            "ok": True,
            "history": history,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION — évaluation QRO
# ------------------------------------------------------------------


@app.post("/session/evaluate_qro")
async def evaluate_qro_endpoint(
    request: Request,
    data: EvaluateQRORequest,
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    try:
        q = {
            "question": data.question,
            "correct_answer": data.correct_answer,
        }

        correct, feedback = evaluate_answer(q, data.user_answer)

        return {
            "ok": True,
            "correct": correct,
            "feedback": feedback,
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ------------------------------------------------------------------
# SESSION — feedback progressif
# ------------------------------------------------------------------


@app.post("/session/feedback")
async def feedback_endpoint(
    request: Request,
    data: FeedbackRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    try:
        instructions = {
            1: "Donne un indice court et orientant (2 phrases max). Ne donne pas la methode, juste une piste de reflexion.",
            2: "Explique la methode a suivre sans donner le resultat. Rappelle la definition ou la regle cle. 3-4 phrases.",
            3: "Donne une explication complete avec la formule ou la regle exacte. C'est la derniere chance. 4-5 phrases.",
        }

        instruction = instructions[min(data.attempt, 3)]

        prompt = (
            "Tu es un tuteur de mathematiques pour etudiants de premiere annee.\n"
            f"Tentative {data.attempt}/3.\n\n"
            f"Question : {data.question}\n"
            f"Reponse eleve : {data.user_answer}\n"
            "Competence : "
            + data.competence.get("nom", "")
            + " niveau "
            + data.competence.get("niveau", "")
            + "\n\n"
            "Consigne : "
            + instruction
            + "\n"
            "Ne donne JAMAIS la bonne reponse. Utilise le tu. Sois concis. Pas de JSON ni balises.\n"
        )

        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
        )

        feedback = response.choices[0].message.content.strip()

        cross_module_reco = None

        if data.attempt >= 2 and data.notion_key:
            try:
                REFERENTIEL = get_referentiel()

                sso_id = session.exec(
                    select(models.User.sso_id).where(
                        models.User.email == user["email"]
                    )
                ).first()

                if not sso_id:
                    return JSONResponse(
                        status_code=404,
                        content={"detail": "Utilisateur introuvable"},
                    )

                source_notion = session.exec(
                    select(models.Notion).where(
                        models.Notion.referentiel_key == data.notion_key
                    )
                ).first()

                if not source_notion:
                    return JSONResponse(
                        status_code=400,
                        content={"ok": False, "error": "Notion source inconnue"},
                    )

                result_diag = diagnostiquer_depuis_competence(
                    notion=data.notion_nom,
                    niveau=data.competence.get("niveau", "basique"),
                    enonce=data.question,
                    reponse_correcte=data.correct_answer,
                    reponse_etudiant=data.user_answer,
                    competence_cible=data.competence,
                    nb_tentatives=data.attempt,
                )

                diag = result_diag.get("diagnostic", {}).get("diagnostic", {})
                lacunaires = diag.get("competences_lacunaires", [])

                notion_codes_courants = [
                    c["code"]
                    for c in REFERENTIEL.get(data.notion_key, {}).get("competences", [])
                ]

                for lac in lacunaires:
                    lacune_code = lac.get("code")

                    if not lacune_code:
                        continue

                    if (
                        lac.get("source") == "detectee_passe2"
                        and lacune_code not in notion_codes_courants
                    ):
                        notion_trouvee_key = None
                        notion_trouvee_nom = None

                        for nkey, ndata in REFERENTIEL.items():
                            if nkey == data.notion_key:
                                continue

                            for comp in ndata["competences"]:
                                if comp["code"] == lacune_code:
                                    notion_trouvee_key = nkey
                                    notion_trouvee_nom = ndata["notion_nom"]
                                    break

                            if notion_trouvee_key:
                                break

                        if not notion_trouvee_key:
                            continue

                        lacunaire_notion = session.exec(
                            select(models.Notion).where(
                                models.Notion.referentiel_key == notion_trouvee_key
                            )
                        ).first()

                        if not lacunaire_notion:
                            print(
                                "[RECO] Notion lacunaire introuvable en BDD :",
                                notion_trouvee_key,
                            )
                            continue

                        existing = session.exec(
                            select(models.ModuleRecommendation).where(
                                models.ModuleRecommendation.sso_id == sso_id,
                                models.ModuleRecommendation.notion_source_id
                                == source_notion.notion_id,
                                models.ModuleRecommendation.notion_lacunaire_id
                                == lacunaire_notion.notion_id,
                            )
                        ).first()

                        now = datetime.utcnow()

                        if existing:
                            existing.count += 1
                            existing.updated_at = now
                            session.add(existing)
                            reco_count = existing.count

                        else:
                            new_reco = models.ModuleRecommendation(
                                recommendation_id=uuid.uuid4(),
                                sso_id=sso_id,
                                notion_source_id=source_notion.notion_id,
                                notion_lacunaire_id=lacunaire_notion.notion_id,
                                notion_lacunaire_nom=notion_trouvee_nom,
                                count=1,
                                updated_at=now,
                            )

                            session.add(new_reco)
                            reco_count = 1

                        session.commit()

                        cross_module_reco = {
                            "notion_id": str(lacunaire_notion.notion_id),
                            "notion_key": notion_trouvee_key,
                            "notion_nom": notion_trouvee_nom,
                            "count": reco_count,
                        }

                        break

            except Exception as reco_err:
                print("Reco error:", reco_err)

        return {
            "ok": True,
            "feedback": feedback,
            "can_retry": data.attempt < 3,
            "cross_module_reco": cross_module_reco,
        }

    except Exception as e:
        print("FEEDBACK ERROR:", traceback.format_exc())

        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION — question ciblée sur une compétence spécifique
# ------------------------------------------------------------------


@app.post("/session/next_targeted")
async def next_targeted_endpoint(
    request: Request,
    data: NextTargetedRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    REFERENTIEL = get_referentiel()

    try:
        if data.notion_key not in REFERENTIEL:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Notion inconnue"},
            )

        notion_data = build_notion_data_with_scores(data.notion_key, sso_id, session)
        notion_nom = notion_data["notion_nom"]

        comp = next(
            (
                c
                for c in notion_data["competences"]
                if c["code"] == data.competence_code
            ),
            None,
        )

        if not comp:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "error": "Compétence inconnue"},
            )

        qtype = random.choice(["qcm", "qro"])

        if qtype == "qcm":
            questions = generate_qcm_test(notion_nom, [comp])

            if not questions:
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": "Aucune QCM générée"},
                )

            question = questions[0]
            question["type"] = "qcm"

        else:
            questions = generate_qro_test(notion_nom, [comp])

            if not questions:
                return JSONResponse(
                    status_code=500,
                    content={"ok": False, "error": "Aucune QRO générée"},
                )

            question = questions[0]
            question["type"] = "qro"

        question["notion_nom"] = notion_nom
        question["niveau"] = comp.get("niveau", "basique")

        return {
            "ok": True,
            "questions": [question],
            "notion_nom": notion_nom,
        }

    except Exception as e:
        print("NEXT_TARGETED ERROR:", traceback.format_exc())

        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION — marquer l'entraînement comme commencé
# ------------------------------------------------------------------


@app.post("/session/training_started")
async def mark_training_started(
    request: Request,
    data: TrainingStartedRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    notion = session.exec(
        select(models.Notion).where(
            models.Notion.referentiel_key == data.notion_key
        )
    ).first()

    if not notion:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Notion inconnue"},
        )

    try:
        existing = session.exec(
            select(models.NotionProgress).where(
                models.NotionProgress.sso_id == sso_id,
                models.NotionProgress.notion_id == notion.notion_id,
            )
        ).first()

        now = datetime.utcnow()

        if existing:
            existing.training_started = True
            existing.updated_at = now
            session.add(existing)

        else:
            session.add(
                models.NotionProgress(
                    progress_id=uuid.uuid4(),
                    sso_id=sso_id,
                    notion_id=notion.notion_id,
                    training_started=True,
                    updated_at=now,
                )
            )

        session.commit()

        return {"ok": True}

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# SESSION — génération test d'évaluation
# ------------------------------------------------------------------


@app.post("/session/evaluation")
async def evaluation_endpoint(
    request: Request,
    data: EvaluationRequest,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    try:
        n = max(5, min(20, data.n_questions))

        n_bas = max(1, round(n * 0.2))
        n_sol = max(1, round(n * 0.5))
        n_exp = max(1, n - n_bas - n_sol)

        def split(total):
            if total <= 0:
                return 0, 0, 0

            qcm = max(1, round(total * 0.5))
            qro = max(1, round(total * 0.3))
            sbs = max(0, total - qcm - qro)

            return qcm, qro, sbs

        notion_data = build_notion_data_with_scores(data.notion_key, sso_id, session)
        notion_nom = notion_data["notion_nom"]

        q_bas = generate_mixed_test(
            notion=data.notion_key,
            niveau="basique",
            n_qcm=split(n_bas)[0],
            n_qro=split(n_bas)[1],
            n_steps=split(n_bas)[2],
            notion_data_override=notion_data,
        )

        q_sol = generate_mixed_test(
            notion=data.notion_key,
            niveau="solide",
            n_qcm=split(n_sol)[0],
            n_qro=split(n_sol)[1],
            n_steps=split(n_sol)[2],
            notion_data_override=notion_data,
        )

        q_exp = generate_mixed_test(
            notion=data.notion_key,
            niveau="expert",
            n_qcm=split(n_exp)[0],
            n_qro=split(n_exp)[1],
            n_steps=split(n_exp)[2],
            notion_data_override=notion_data,
        )

        questions = q_bas + q_sol + q_exp

        seen = set()
        unique_questions = []

        for q in questions:
            comp_code = (q.get("competence_cible") or {}).get("code", "")
            key = comp_code + q.get("type", "")

            if key not in seen:
                seen.add(key)
                unique_questions.append(q)

        questions = unique_questions
        random.shuffle(questions)

        return {
            "ok": True,
            "questions": questions,
            "notion_nom": notion_nom,
            "n_questions": len(questions),
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(e)})


# ------------------------------------------------------------------
# SESSION — recommandations inter-modules
# ------------------------------------------------------------------


@app.get("/session/recommendations")
async def get_recommendations_endpoint(
    request: Request,
    notion_key: str,
    session: Session = Depends(get_session),
):
    user = get_current_user(request)

    if not user:
        return JSONResponse(status_code=401, content={"detail": "Non connecté"})

    sso_id = session.exec(
        select(models.User.sso_id).where(models.User.email == user["email"])
    ).first()

    if not sso_id:
        return JSONResponse(
            status_code=404,
            content={"detail": "Utilisateur introuvable"},
        )

    source_notion = session.exec(
        select(models.Notion).where(
            models.Notion.referentiel_key == notion_key
        )
    ).first()

    if not source_notion:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Notion inconnue"},
        )

    try:
        rows = session.exec(
            select(models.ModuleRecommendation)
            .where(
                models.ModuleRecommendation.sso_id == sso_id,
                models.ModuleRecommendation.notion_source_id == source_notion.notion_id,
                models.ModuleRecommendation.count >= 2,
            )
            .order_by(models.ModuleRecommendation.count.desc())
        ).all()

        recommendations = []

        for reco in rows:
            lacunaire_notion = session.exec(
                select(models.Notion).where(
                    models.Notion.notion_id == reco.notion_lacunaire_id
                )
            ).first()

            if not lacunaire_notion:
                continue

            recommendations.append(
                {
                    "notion_id": str(lacunaire_notion.notion_id),
                    "notion_key": lacunaire_notion.referentiel_key,
                    "notion_nom": reco.notion_lacunaire_nom,
                    "title": lacunaire_notion.title,
                    "description": lacunaire_notion.description,
                    "count": reco.count,
                }
            )

        return {
            "ok": True,
            "recommendations": recommendations,
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": str(e)},
        )


# ------------------------------------------------------------------
# Run app
# ------------------------------------------------------------------


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
    )