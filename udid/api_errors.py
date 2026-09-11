"""
Respuestas HTTP coherentes: evitar 500 por fallos transitorios (BD, upstream)
y no filtrar detalles internos al cliente (salvo DEBUG).
"""
import logging
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, OperationalError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_exception_handler

logger = logging.getLogger(__name__)


def _error_ref():
    return str(uuid.uuid4())[:8]


def response_db_unavailable(log_prefix: str, exc: BaseException | None = None) -> Response:
    """
    Bloqueos, timeouts o caída de BD.
    Requisito del proyecto: evitar cualquier 5xx → usar 429 con Retry-After.
    """
    ref = _error_ref()
    if exc is not None:
        logger.warning("%s [ref=%s] database unavailable: %s", log_prefix, ref, exc, exc_info=True)
    else:
        logger.warning("%s [ref=%s] database unavailable", log_prefix, ref)
    return Response(
        {
            "error": "Database temporarily unavailable. Please retry shortly.",
            "error_ref": ref,
        },
        status=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": "5"},
    )


def response_upstream_unavailable(
    log_prefix: str,
    message: str = "External service temporarily unavailable.",
    exc: BaseException | None = None,
) -> Response:
    """
    Fallo de API externa (p. ej. PanAccess).
    Requisito del proyecto: evitar cualquier 5xx → usar 429 (reintentar).
    """
    ref = _error_ref()
    if exc is not None:
        logger.error("%s [ref=%s] upstream error: %s", log_prefix, ref, exc, exc_info=True)
    else:
        logger.error("%s [ref=%s] upstream error", log_prefix, ref)
    body = {"error": message, "error_ref": ref}
    if settings.DEBUG and exc is not None:
        body["debug_detail"] = str(exc)
    return Response(body, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={"Retry-After": "10"})


def response_unexpected_error(log_prefix: str, exc: BaseException) -> Response:
    """
    Error no clasificado.
    Requisito del proyecto: evitar cualquier 5xx → usar 400 genérico (sin filtrar detalles).
    """
    ref = _error_ref()
    logger.exception("%s [ref=%s]", log_prefix, ref)
    body = {
        "error": "An unexpected error occurred. Please try again later.",
        "error_ref": ref,
    }
    if settings.DEBUG:
        body["debug_detail"] = str(exc)
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


def response_encryption_unavailable(log_prefix: str, exc: BaseException | None = None) -> Response:
    """
    Fallo de cifrado/configuración de claves.
    Requisito del proyecto: evitar cualquier 5xx → usar 429 con Retry-After.
    """
    ref = _error_ref()
    logger.error("%s [ref=%s] encryption/credentials error", log_prefix, ref, exc_info=exc is not None)
    body = {
        "error": "Credential encryption is temporarily unavailable.",
        "error_ref": ref,
    }
    if settings.DEBUG and exc is not None:
        body["debug_detail"] = str(exc)
    return Response(
        body,
        status=status.HTTP_429_TOO_MANY_REQUESTS,
        headers={"Retry-After": "10"},
    )


def classify_transient_db_error(exc: BaseException) -> bool:
    """Errores de conexión/bloqueo típicos (p. ej. SQLite locked) → 429."""
    return isinstance(exc, OperationalError)


def handle_view_exception(log_prefix: str, exc: BaseException) -> Response:
    """
    Orquestador para vistas genéricas: BD transitoria → 429, integridad → 409, resto → 400 genérico.
    """
    if isinstance(exc, IntegrityError):
        ref = _error_ref()
        logger.warning("%s integrity conflict [ref=%s]", log_prefix, ref, exc_info=True)
        return Response(
            {
                "error": "Data conflict. The operation could not be completed.",
                "error_ref": ref,
            },
            status=status.HTTP_409_CONFLICT,
        )
    if classify_transient_db_error(exc):
        return response_db_unavailable(log_prefix, exc)
    if isinstance(exc, DjangoValidationError):
        return Response(
            {"error": "Validation error", "details": getattr(exc, "message_dict", None) or str(exc)},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return response_unexpected_error(log_prefix, exc)


def drf_exception_handler(exc: BaseException, context: dict) -> Response | None:
    """
    Handler global para DRF.
    Objetivo: que NINGÚN endpoint retorne 5xx por excepciones no controladas.
    """
    response = drf_default_exception_handler(exc, context)

    # Si DRF ya generó una respuesta, pero es 5xx, degradarla a 4xx controlado.
    if response is not None:
        try:
            if 500 <= int(response.status_code) <= 599:
                # Mantener payload original si existe, pero normalizar estado y agregar referencia.
                ref = _error_ref()
                logger.error("DRF handler produced 5xx [ref=%s]: %r", ref, exc, exc_info=True)
                data = response.data
                payload = dict(data) if isinstance(data, dict) else {"detail": data}
                payload.setdefault("error", "Request failed. Please retry.")
                payload["error_ref"] = ref
                response.data = payload
                response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
                response["Retry-After"] = response.get("Retry-After", "5")
        except Exception:
            # Último recurso: devolver 400 genérico.
            return response_unexpected_error("drf_exception_handler", exc)
        return response

    # Excepción no manejada por DRF: normalizar.
    return handle_view_exception("drf_exception_handler", exc)
