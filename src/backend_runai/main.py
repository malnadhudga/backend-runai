"""FastAPI app: health check, optional Bearer auth, Gemini chat proxy."""

from __future__ import annotations

import logging
import sys
from typing import Annotated, Any

import google.generativeai as genai
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend_runai.settings import Settings

# Gemini SDK request timeout (seconds); RunAI clients often allow ~300s.
GEMINI_REQUEST_TIMEOUT_SECONDS: float = 300.0

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging for JSON-friendly output."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


settings = get_settings()
configure_logging(settings)
logger = structlog.get_logger(__name__)

genai.configure(api_key=settings.gemini_api_key)

app = FastAPI(title="backend-runai", version="0.1.0")

_cors_origins = settings.cors_origin_list()
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


class ChatMessage(BaseModel):
    """One chat turn with role and text content."""

    role: str
    content: str


class ChatRequest(BaseModel):
    """Inbound JSON for POST /v1/chat."""

    model: str
    system: str = ""
    messages: list[ChatMessage]
    client_id: str | None = None


def assemble_gemini_prompt(system: str, messages: list[ChatMessage]) -> str:
    """Build a single prompt string matching RunAI direct-mode assembly.

    Prepends ``[System]`` block when system is non-empty; each message becomes
    ``[role]\\ncontent`` with blank lines between parts.

    Args:
        system: Optional system instructions.
        messages: Ordered role/content pairs from the client.

    Returns:
        Full prompt string for ``generate_content``.
    """
    parts: list[str] = []
    if system.strip():
        parts.append(f"[System]\n{system}\n")
    for message in messages:
        parts.append(f"[{message.role}]\n{message.content}")
    return "\n\n".join(parts)


def extract_response_text(response: Any) -> str:
    """Return model text from a Gemini response object.

    Args:
        response: Result of ``GenerativeModel.generate_content``.

    Returns:
        The primary text output.

    Raises:
        ValueError: If the response has no usable text (blocked or empty).
    """
    try:
        text = getattr(response, "text", None)
        if text is not None and str(text).strip():
            return str(text)
    except (ValueError, AttributeError):
        pass
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        raise ValueError("No candidates in Gemini response")
    first = candidates[0]
    content = getattr(first, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        raise ValueError("No content parts in Gemini response")
    chunks: list[str] = []
    for part in parts:
        t = getattr(part, "text", None)
        if t:
            chunks.append(str(t))
    if not chunks:
        raise ValueError("Empty text in Gemini response")
    return "".join(chunks)


def verify_bearer_if_configured(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Enforce bearer token when the server has ``PROXY_BEARER_TOKEN`` set.

    Args:
        authorization: Raw ``Authorization`` header value, if present.
    """
    expected = get_settings().proxy_bearer_token
    if not expected:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authorization",
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/v1/chat", dependencies=[Depends(verify_bearer_if_configured)])
def chat(
    request: Request,
    body: ChatRequest,
    x_runai_client_id: Annotated[str | None, Header(alias="X-Runai-Client-Id")] = None,
) -> dict[str, str]:
    """Forward assembled prompt to Gemini; return JSON with ``text`` field."""
    settings_local = get_settings()
    header_client = x_runai_client_id
    body_client = body.client_id
    effective_model = settings_local.gemini_model
    logger.info(
        "chat_request",
        path=request.url.path,
        model_requested=body.model,
        model_used=effective_model,
        client_id_body=body_client,
        client_id_header=header_client,
        message_count=len(body.messages),
    )
    prompt = assemble_gemini_prompt(body.system, body.messages)
    if settings_local.log_prompts:
        logger.debug("chat_prompt", prompt_preview=prompt[:2000])
    try:
        model = genai.GenerativeModel(effective_model)
        response = model.generate_content(
            prompt,
            request_options={"timeout": GEMINI_REQUEST_TIMEOUT_SECONDS},
        )
        text = extract_response_text(response)
    except Exception as exc:
        logger.error("gemini_error", error=str(exc), model=effective_model)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream model error",
        ) from exc
    return {"text": text}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log unexpected errors and return a safe JSON body (no stack in prod).

    Args:
        request: Incoming request (for path logging).
        exc: The unhandled exception.

    Returns:
        JSON with generic ``detail`` unless ``LOG_LEVEL`` is ``DEBUG``.
    """
    log = structlog.get_logger(__name__)
    log.exception("unhandled_exception", path=str(request.url.path))
    settings_local = get_settings()
    if settings_local.log_level.upper() == "DEBUG":
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


def create_app() -> FastAPI:
    """Factory for tests or ASGI servers that prefer explicit setup."""
    return app
