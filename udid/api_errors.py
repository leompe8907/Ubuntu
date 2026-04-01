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

logger = logging.getLogger(__name__)


def _error_ref():
    return str(uuid.uuid4())[:8]


def response_db_unavailable(log_prefix: str, exc: BaseException | None = None) -> Response:
    """Bloqueos, timeouts o caída de BD: 503 con Retry-After."""
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
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "5"},
    )


def response_upstream_unavailable(
    log_prefix: str,
    message: str = "External service temporarily unavailable.",
    exc: BaseException | None = None,
) -> Response:
    """Fallo de API externa (p. ej. PanAccess): 502."""
    ref = _error_ref()
    if exc is not None:
        logger.error("%s [ref=%s] upstream error: %s", log_prefix, ref, exc, exc_info=True)
    else:
        logger.error("%s [ref=%s] upstream error", log_prefix, ref)
    body = {"error": message, "error_ref": ref}
    if settings.DEBUG and exc is not None:
        body["debug_detail"] = str(exc)
    return Response(body, status=status.HTTP_502_BAD_GATEWAY)


def response_unexpected_error(log_prefix: str, exc: BaseException) -> Response:
    """Error interno no clasificado: 500, mensaje genérico, detalle solo en DEBUG."""
    ref = _error_ref()
    logger.exception("%s [ref=%s]", log_prefix, ref)
    body = {
        "error": "An unexpected error occurred. Please try again later.",
        "error_ref": ref,
    }
    if settings.DEBUG:
        body["debug_detail"] = str(exc)
    return Response(body, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def response_encryption_unavailable(log_prefix: str, exc: BaseException | None = None) -> Response:
    """Fallo de cifrado/configuración de claves: 503, no 500."""
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
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
        headers={"Retry-After": "10"},
    )


def classify_transient_db_error(exc: BaseException) -> bool:
    """Errores de conexión/bloqueo típicos (p. ej. SQLite locked) → 503."""
    return isinstance(exc, OperationalError)


def handle_view_exception(log_prefix: str, exc: BaseException) -> Response:
    """
    Orquestador para vistas genéricas: BD transitoria → 503, integridad → 409, resto → 500 genérico.
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
