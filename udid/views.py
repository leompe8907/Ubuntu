from functools import reduce
import operator

from rest_framework.views import APIView
from rest_framework import status, filters
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated, IsAdminUser

from django.db.models import Q
from django.db import transaction, OperationalError
from django.db.utils import IntegrityError
from django.utils import timezone
from django.core.cache import cache
from django.core.paginator import Paginator


from asgiref.sync import async_to_sync

from channels.layers import get_channel_layer

from datetime import timedelta

import logging
import secrets
import hashlib
import json

from .management.commands.keyGenerator import hybrid_encrypt_for_app
from .serializers import UDIDAssociationSerializer
from .util import (
    get_client_ip, compute_encrypted_hash, json_serialize_credentials, is_valid_app_type,
    generate_device_fingerprint, check_device_fingerprint_rate_limit, check_udid_rate_limit,
    check_combined_rate_limit, increment_rate_limit_counter,
    is_legitimate_reconnection, check_adaptive_rate_limit,
    should_apply_retry_delay, reset_retry_info, get_retry_info,
    get_client_token, check_token_bucket_lua
)
from .models import UDIDAuthRequest, SubscriberInfo, AppCredentials, EncryptedCredentialsLog
from .utils.server.log_buffer import log_audit_async
from .utils.server.metrics import get_metrics, reset_metrics
from .cron import execute_sync_tasks
from .api_errors import (
    handle_view_exception,
    response_encryption_unavailable,
)

logger = logging.getLogger(__name__)

UDID_STATUS_FOR_LIST = ("validated", "used", "revoked")


def _latest_udid_map_for_subscribers(subscribers):
    """
    Una consulta batch para el último UDIDAuthRequest por (subscriber_code, sn)
    en la página actual (evita N+1).
    """
    pairs = [(s.subscriber_code, s.sn) for s in subscribers]
    if not pairs:
        return {}
    q_filter = reduce(
        operator.or_,
        (Q(subscriber_code=c, sn=sn) for c, sn in pairs),
    )
    candidates = (
        UDIDAuthRequest.objects.filter(
            q_filter,
            status__in=UDID_STATUS_FOR_LIST,
        )
        .order_by("-validated_at")
        .only(
            "subscriber_code",
            "sn",
            "udid",
            "status",
            "created_at",
            "validated_at",
            "user_agent",
            "app_type",
            "app_version",
            "method",
            "validated_by_operator",
        )
    )
    latest = {}
    for u in candidates:
        key = (u.subscriber_code, u.sn)
        if key not in latest:
            latest[key] = u
    return latest


def _parse_pagination_params(request):
    """page y page_size enteros acotados; None + Response error si inválido."""
    try:
        raw_page = request.query_params.get("page", 1)
        raw_size = request.query_params.get("page_size", 20)
        page_number = int(raw_page) if raw_page not in (None, "") else 1
        page_size = int(raw_size) if raw_size not in (None, "") else 20
    except (TypeError, ValueError):
        return None, None, Response(
            {"error": "Invalid page or page_size"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if page_number < 1:
        return None, None, Response(
            {"error": "page must be >= 1"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    page_size = max(1, min(page_size, 200))
    return page_number, page_size, None

def get_cached_app_credentials(app_type, app_version):
    """
    Devuelve AppCredentials desde cache de corto plazo para reducir
    consultas a BD bajo alta concurrencia.
    """
    cache_key = f"appcred:{app_type}:{app_version}"
    app_credentials = cache.get(cache_key)
    if app_credentials is not None:
        return app_credentials

    app_credentials = AppCredentials.objects.filter(
        app_type=app_type,
        app_version=app_version,
        is_active=True
    ).first()

    # Cache corto (10 segundos) para no romper rotación de claves
    cache.set(cache_key, app_credentials, timeout=10)
    return app_credentials


class RequestUDIDManualView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        """
        Paso 1: Generar UDID único para solicitud manual
        """
        client_ip = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        logger.info(
            f"RequestUDIDManualView: Request recibido - "
            f"ip={client_ip}, user_agent={user_agent[:100] if user_agent else 'N/A'}"
        )
        
        try:
            # ========================================================================
            # FAST-FAIL: Rate limiting ANTES de tocar la BD
            # ========================================================================
            
            # Rate limiting por Device Fingerprint (Redis, sin BD)
            device_fingerprint = generate_device_fingerprint(request)
            
            is_allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
                device_fingerprint,
                max_requests=1,  # 1 request cada 5 min (ventana 5 min entre solicitudes)
                window_minutes=5
            )
            
            if not is_allowed:
                logger.warning(
                    f"RequestUDIDManualView: Rate limit excedido - "
                    f"device_fingerprint={device_fingerprint[:8]}..., ip={client_ip}, "
                    f"retry_after={retry_after}s"
                )
                retry_at = timezone.now() + timedelta(seconds=retry_after)
                return Response({
                    "error_code": "DEVICE_FP_RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_after,
                    "retry_at": retry_at.isoformat(),
                    "remaining_requests": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })

            # ========================================================================
            # AHORA SÍ: Operaciones de BD
            # ========================================================================
            
            # 3. Generar UDID único (reintentos ante colisión / IntegrityError)
            auth_request = None
            udid = None
            for _ in range(12):
                candidate = secrets.token_hex(4)
                try:
                    auth_request = UDIDAuthRequest.objects.create(
                        udid=candidate,
                        status='pending',
                        client_ip=client_ip,
                        user_agent=request.META.get('HTTP_USER_AGENT', ''),
                        device_fingerprint=device_fingerprint,
                    )
                    udid = candidate
                    break
                except IntegrityError:
                    continue
            if auth_request is None:
                logger.error(
                    "RequestUDIDManualView: agotados reintentos de UDID único ip=%s",
                    client_ip,
                )
                return Response(
                    {
                        "error_code": "SERVICE_TEMPORARILY_UNAVAILABLE",
                        "detail": "Could not allocate a unique UDID. Please retry.",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    headers={"Retry-After": "2"},
                )
            
            # ✅ Verificar que se guardó correctamente (recargar desde BD)
            auth_request.refresh_from_db()
            
            # Incrementar contador
            increment_rate_limit_counter('device_fp', device_fingerprint)
            
            # Log de auditoría (asíncrono)
            log_audit_async(
                action_type='udid_generated',
                udid=udid,
                client_ip=client_ip,
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                details={
                    'method': 'manual_request',
                    'device_fingerprint': device_fingerprint,
                    'device_fingerprint_stored': auth_request.device_fingerprint,  # ✅ Verificar almacenamiento
                    'rate_limit_remaining': remaining - 1
                }
            )
            
            # ✅ Incluir device_fingerprint en la respuesta
            logger.info(
                f"RequestUDIDManualView: UDID generado exitosamente - "
                f"udid={udid}, device_fingerprint={device_fingerprint[:8]}..., "
                f"ip={client_ip}, expires_at={auth_request.expires_at}"
            )
            
            return Response({
                "udid": auth_request.udid,
                "expires_at": auth_request.expires_at,
                "status": auth_request.status,
                "expires_in_minutes": 5,
                "device_fingerprint": auth_request.device_fingerprint,
                "remaining_requests": remaining - 1,
                "rate_limit": {
                    "remaining": remaining - 1,
                    "reset_in_seconds": 5 * 60
                }
            }, status=status.HTTP_201_CREATED)
            
        except OperationalError as db_err:
            logger.error(
                f"RequestUDIDManualView: Database timeout/lock - "
                f"ip={client_ip}, error={str(db_err)}", exc_info=True
            )
            return Response({
                "error_code": "SERVICE_TEMPORARILY_UNAVAILABLE",
                "detail": "The service is currently experiencing high load. Please try again later."
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={
                "Retry-After": "5"
            })
        except Exception as e:
            return handle_view_exception(
                f"RequestUDIDManualView ip={client_ip}",
                e,
            )

class ValidateAndAssociateUDIDView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        """
        Valida y asocia un UDID con un subscriber.
        PROTEGIDO POR: UDID Rate Limiting
        """
        client_ip = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        logger.info(
            f"ValidateAndAssociateUDIDView: Request recibido - "
            f"ip={client_ip}, data_keys={list(request.data.keys()) if request.data else 'N/A'}"
        )
        
        # ========================================================================
        # FAST-FAIL: Rate limiting ANTES de tocar la BD
        # ========================================================================
        
        # 1. Rate limiting con token bucket (Redis, sin BD) - lo más temprano posible
        client_token = get_client_token(request)
        if client_token:
            is_allowed, remaining, retry_after = check_token_bucket_lua(
                identifier=client_token,
                capacity=5,  # 5 requests (más restrictivo para asociación)
                refill_rate=1,  # 1 token por segundo
                window_seconds=60,
                tokens_requested=1
            )
            
            if not is_allowed:
                logger.warning(
                    f"ValidateAndAssociateUDIDView: Token bucket rate limit excedido - "
                    f"token={client_token[:8] if len(client_token) > 8 else client_token}..., "
                    f"ip={client_ip}, retry_after={retry_after}s"
                )
                retry_at = timezone.now() + timedelta(seconds=retry_after)
                return Response({
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_after,
                    "retry_at": retry_at.isoformat(),
                    "remaining": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })
        
        # 2. Validación básica de datos (sin BD)
        serializer = UDIDAssociationSerializer(data=request.data)
        
        if not serializer.is_valid():
            logger.warning(
                f"ValidateAndAssociateUDIDView: Datos inválidos - "
                f"ip={client_ip}, errors={serializer.errors}"
            )
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        
        # 3. Obtener UDID del request validado (el serializer ya consultó BD, pero sin lock)
        data = serializer.validated_data
        udid_request = data["udid_request"]   # instancia ya validada por el serializer
        udid = udid_request.udid if hasattr(udid_request, 'udid') else None
        
        # 4. Rate limiting por UDID (Redis, sin BD): 1 intento por minuto
        if udid:
            is_allowed, remaining, retry_after = check_udid_rate_limit(
                udid,
                max_requests=1,
                window_minutes=1
            )
            
            if not is_allowed:
                logger.warning(
                    f"ValidateAndAssociateUDIDView: Rate limit excedido - "
                    f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                    f"ip={client_ip}, retry_after={retry_after}s"
                )
                retry_at = timezone.now() + timedelta(seconds=retry_after)
                return Response({
                    "error_code": "UDID_ASSOCIATION_RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_after,
                    "retry_at": retry_at.isoformat(),
                    "remaining_requests": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })
        
        # Obtener datos del serializer
        subscriber   = data["subscriber"]
        sn           = data["sn"]
        operator_id  = data["operator_id"]
        method       = data["method"]

        # ========================================================================
        # OPERACIONES DE BD CON LOCKS (al final del flujo)
        # ========================================================================
        
        # 5. AHORA SÍ: select_for_update() - al final, después de todas las validaciones
        with transaction.atomic():
            # Bloqueo optimista de la fila del request
            udid_request = UDIDAuthRequest.objects.select_for_update().get(pk=udid_request.pk)
            
            # Asegurar que tenemos el UDID
            udid = udid_request.udid

            # Asociar y marcar como validated (auditoría adentro)
            self.associate_udid_with_subscriber(
                udid_request, subscriber, sn, operator_id, method, request
            )

            # Notificar a los WebSockets que esperan este UDID: al commit
            def _notify():
                try:
                    channel_layer = get_channel_layer()
                    if channel_layer:
                        async_to_sync(channel_layer.group_send)(
                            f"udid_{udid}",              # 👈 mismo group que usa el consumer
                            {"type": "udid.validated", "udid": udid}  # 👈 llama a AuthWaitWS.udid_validated
                        )
                        logger.info("Notificado udid.validated para %s", udid)
                    else:
                        logger.warning("Channel layer no disponible; no se notificó udid %s", udid)
                except Exception as e:
                    logger.exception("Error notificando WebSocket para udid %s: %s", udid, e)

            transaction.on_commit(_notify)

        logger.info(
            f"ValidateAndAssociateUDIDView: Asociación exitosa - "
            f"udid={udid_request.udid}, subscriber_code={subscriber.subscriber_code}, "
            f"sn={sn}, operator_id={operator_id}, method={method}, ip={client_ip}"
        )
        
        # Incrementar contador de rate limiting
        if udid:
            increment_rate_limit_counter('udid', udid)

        # DRF serializa datetime a ISO automáticamente en Response
        response_data = {
            "message": "UDID validated and associated successfully",
            "udid": udid_request.udid,
            "subscriber_code": subscriber.subscriber_code,
            "smartcard_sn": sn,
            "status": udid_request.status,
            "validated_at": udid_request.validated_at,
            "used_at": udid_request.used_at,
            "validated_by_operator": operator_id
        }
        
        # Agregar información de rate limit si está disponible
        if udid and remaining is not None:
            response_data["remaining_requests"] = remaining - 1
            response_data["rate_limit"] = {
                "remaining": remaining - 1,
                "reset_in_seconds": 60
            }

        return Response(response_data, status=status.HTTP_200_OK)

    def associate_udid_with_subscriber(self, auth_request, subscriber, sn, operator_id, method, request):
        """Método auxiliar para asociar UDID con subscriber (con logging interno)"""
        now = timezone.now()
        client_ip  = get_client_ip(request)
        user_agent = request.META.get("HTTP_USER_AGENT", "")

        # Marcar asociación y validación en el request
        auth_request.subscriber_code       = subscriber.subscriber_code
        auth_request.sn                    = sn
        auth_request.status                = "validated"
        auth_request.validated_at          = now
        auth_request.used_at               = now
        auth_request.validated_by_operator = operator_id
        auth_request.client_ip             = client_ip
        auth_request.user_agent            = user_agent
        auth_request.method                = method
        auth_request.save()

        # Marcar actividad del suscriptor (si corresponde)
        subscriber.last_login = now
        subscriber.save(update_fields=["last_login"])

        # Auditoría (asíncrono)
        log_audit_async(
            action_type="udid_used",
            udid=auth_request.udid,
            subscriber_code=subscriber.subscriber_code,
            operator_id=operator_id,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                "subscriber_name": f"{subscriber.first_name} {subscriber.last_name}".strip(),
                "smartcard_sn": sn,
                "validation_timestamp": now.isoformat(),
            },
        )

class AuthenticateWithUDIDView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        """
        Autentica con UDID y retorna credenciales encriptadas.
        PROTEGIDO POR: UDID Rate Limiting
        """
        udid = request.data.get('udid')
        app_type = request.data.get('app_type', 'android_tv')
        app_version = request.data.get('app_version', '1.0')
        client_ip = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')

        logger.info(
            f"AuthenticateWithUDIDView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"app_type={app_type}, app_version={app_version}, ip={client_ip}"
        )

        if not udid:
            logger.warning(
                f"AuthenticateWithUDIDView: UDID faltante - ip={client_ip}"
            )
            return Response({"error": "UDID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Validar app_type usando la función centralizada (validación rápida, sin BD)
        if not is_valid_app_type(app_type):
            logger.warning(
                f"AuthenticateWithUDIDView: app_type inválido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"app_type={app_type}, ip={client_ip}"
            )
            return Response({
                "error": f"Invalid app_type. Must be one of: android_tv, samsung_tv, lg_tv, set_top_box, mobile_app, web_player",
                "received": app_type
            }, status=status.HTTP_400_BAD_REQUEST)

        # ========================================================================
        # FAST-FAIL: Rate limiting ANTES de tocar la BD
        # ========================================================================
        
        # 1. Rate limiting con token bucket (Redis, sin BD): 5 intentos, 1 min entre intentos
        client_token = get_client_token(request)
        if client_token:
            is_allowed, remaining, retry_after = check_token_bucket_lua(
                identifier=client_token,
                capacity=5,
                refill_rate=1,
                window_seconds=60,
                tokens_requested=1
            )
            
            if not is_allowed:
                logger.warning(
                    f"AuthenticateWithUDIDView: Token bucket rate limit excedido - "
                    f"token={client_token[:8] if len(client_token) > 8 else client_token}..., "
                    f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                    f"ip={client_ip}, retry_after={retry_after}s"
                )
                retry_at = timezone.now() + timedelta(seconds=retry_after)
                return Response({
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_after,
                    "retry_at": retry_at.isoformat(),
                    "remaining": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })

        # 2. Rate limiting por UDID (Redis, sin BD): 5 intentos cada 5 min (1 min entre intentos)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid,
            max_requests=5,
            window_minutes=5
        )
        
        if not is_allowed:
            logger.warning(
                f"AuthenticateWithUDIDView: Rate limit excedido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"ip={client_ip}, retry_after={retry_after}s"
            )
            retry_at = timezone.now() + timedelta(seconds=retry_after)
            return Response({
                "error_code": "UDID_AUTH_RATE_LIMIT_EXCEEDED",
                "retry_after": retry_after,
                "retry_at": retry_at.isoformat(),
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        # ========================================================================
        # AHORA SÍ: Operaciones que consultan la BD
        # ========================================================================
        
        # 3. Verificar si es reconexión legítima (consulta BD, pero sin lock)
        is_reconnection = is_legitimate_reconnection(udid)
        
        # 4. Verificar si se debe aplicar delay de retry (exponential backoff con jitter)
        should_delay, retry_delay, attempt_number = should_apply_retry_delay(
            udid, action_type='reconnection'
        )
        
        if should_delay and retry_delay > 0:
            # Aplicar delay de retry para distribuir reconexiones
            logger.info(
                f"AuthenticateWithUDIDView: Retry delay aplicado - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"retry_delay={retry_delay}s, attempt={attempt_number}, "
                f"is_reconnection={is_reconnection}, ip={client_ip}"
            )
            retry_at = timezone.now() + timedelta(seconds=retry_delay)
            return Response({
                "error_code": "SERVICE_TEMPORARILY_UNAVAILABLE",
                "retry_after": retry_delay,
                "retry_at": retry_at.isoformat(),
                "attempt": attempt_number,
                "is_reconnection": is_reconnection
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={
                "Retry-After": str(retry_delay),
                "X-Retry-After": str(retry_delay)
            })

        # 5. Rate limiting adaptativo para reconexiones (si aplica)
        if is_reconnection:
            is_allowed, remaining, retry_after, reason = check_adaptive_rate_limit(
                'udid', udid, is_reconnection=True
            )
            
            if not is_allowed:
                retry_delay, _ = get_retry_info(udid, 'reconnection')
                retry_at = timezone.now() + timedelta(seconds=retry_delay)
                return Response({
                    "error_code": "RECONNECTION_RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_delay,
                    "retry_at": retry_at.isoformat(),
                    "is_reconnection": True,
                    "reason": reason
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_delay)
                })

        # ========================================================================
        # OPERACIONES DE BD CON LOCKS (al final del flujo)
        # ========================================================================
        
        try:
            with transaction.atomic():
                # 6. AHORA SÍ: select_for_update() - al final, después de todas las validaciones
                try:
                    req = UDIDAuthRequest.objects.select_for_update().get(udid=udid)
                except UDIDAuthRequest.DoesNotExist:
                    return Response({"error": "Invalid UDID"}, status=status.HTTP_404_NOT_FOUND)

                if req.status != 'validated':
                    return Response({"error": f"UDID not valid. Status: {req.status}"}, status=status.HTTP_403_FORBIDDEN)

                if req.is_expired():
                    req.status = 'expired'
                    req.save()
                    return Response({"error": "UDID has expired"}, status=status.HTTP_403_FORBIDDEN)

                try:
                    subscriber = SubscriberInfo.objects.get(subscriber_code=req.subscriber_code, sn=req.sn)
                except SubscriberInfo.DoesNotExist:
                    return Response({"error": "Subscriber info not found or mismatched SN"}, status=status.HTTP_404_NOT_FOUND)

                credentials_payload = {
                    "subscriber_code": subscriber.subscriber_code,
                    "sn": subscriber.sn,
                    "login1": subscriber.login1,
                    "login2": subscriber.login2,
                    "password": subscriber.get_password(),
                    "pin": subscriber.get_pin(),
                    "packages": subscriber.packages,
                    "products": subscriber.products,
                    "timestamp": timezone.now().isoformat()
                }

                # Obtener AppCredentials válidas (con cache corto en memoria/Redis)
                app_credentials = get_cached_app_credentials(app_type, app_version)
                if not app_credentials:
                    return Response({
                        "error": f"No valid app credentials available for app_type='{app_type}'",
                        "solution": "Contact administrator"
                    }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

                # Encriptar credenciales
                try:
                    encrypted_result = hybrid_encrypt_for_app(
                        json_serialize_credentials(credentials_payload), app_type
                    )
                except Exception as e:
                    return response_encryption_unavailable(
                        "AuthenticateWithUDIDView:encrypt",
                        e,
                    )

                # Marcar como entregado
                req.app_type = app_type
                req.app_version = app_version
                req.app_credentials_used = app_credentials
                req.mark_credentials_delivered(app_credentials)
                req.mark_as_used()

                # Log de auditoría (asíncrono)
                log_audit_async(
                    action_type='udid_used',
                    udid=req.udid,
                    subscriber_code=req.subscriber_code,
                    client_ip=client_ip,
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    details={
                        "sn_assigned": subscriber.sn,
                        "app_type": app_type,
                        "app_version": app_version,
                        "encryption_method": "Hybrid AES-256 + RSA-OAEP",
                        "key_fingerprint": app_credentials.key_fingerprint
                    }
                )

                # Log de credenciales cifradas
                encrypted_hash = compute_encrypted_hash(encrypted_result['encrypted_data'])

                EncryptedCredentialsLog.objects.create(
                    udid=req.udid,
                    subscriber_code=req.subscriber_code,
                    sn=req.sn,
                    app_type=app_type,
                    app_version=app_version,
                    app_credentials_id=app_credentials,
                    encrypted_data_hash=encrypted_hash,
                    client_ip=client_ip,
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    delivered_successfully=True
                )
                
                # Incrementar contador de rate limiting
                increment_rate_limit_counter('udid', udid)
                
                # Si es exitoso, resetear retry info (reconexión exitosa)
                if is_reconnection:
                    reset_retry_info(udid, 'reconnection')

                logger.info(
                    f"AuthenticateWithUDIDView: Autenticación exitosa - "
                    f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                    f"subscriber_code={req.subscriber_code}, sn={req.sn}, "
                    f"app_type={app_type}, app_version={app_version}, "
                    f"is_reconnection={is_reconnection}, ip={client_ip}"
                )

                return Response({
                    "encrypted_credentials": encrypted_result,
                    "security_info": {
                        "encryption_method": "Hybrid AES-256 + RSA-OAEP",
                        "app_type": app_type,
                        "app_version": app_credentials.app_version,
                        # "key_fingerprint": app_credentials.key_fingerprint
                    },
                    "expires_at": req.expires_at,
                    "remaining_requests": remaining - 1,
                    "rate_limit": {
                        "remaining": remaining - 1,
                        "reset_in_seconds": 5 * 60
                    }
                }, status=status.HTTP_200_OK)

        except Exception as e:
            if is_reconnection:
                get_retry_info(udid, "reconnection")
            return handle_view_exception(
                f"AuthenticateWithUDIDView udid={udid[:8] if udid and len(udid) > 8 else udid!r}",
                e,
            )

class ValidateStatusUDIDView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        """
        Valida el estado de un UDID.
        PROTEGIDO POR: UDID Rate Limiting
        """
        # ✅ Obtener UDID solo de query parameters o headers, NO del body
        udid = request.query_params.get('udid') or request.META.get('HTTP_X_UDID')
        client_ip = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')

        logger.info(
            f"ValidateStatusUDIDView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., ip={client_ip}"
        )

        if not udid:
            logger.warning(
                f"ValidateStatusUDIDView: UDID faltante - ip={client_ip}"
            )
            return Response({
                "error": "UDID is required as query parameter or X-UDID header",
                "usage_examples": {
                    "query_param": "POST /validate/?udid=your_udid_here",
                    "header": "X-UDID: your_udid_here"
                }
            }, status=status.HTTP_400_BAD_REQUEST)

        # ========================================================================
        # FAST-FAIL: Rate limiting ANTES de tocar la BD
        # ========================================================================
        
        # 1. Rate limiting con token bucket (Redis, sin BD): 5 intentos, 1 min entre intentos
        client_token = get_client_token(request)
        if client_token:
            is_allowed, remaining, retry_after = check_token_bucket_lua(
                identifier=client_token,
                capacity=5,
                refill_rate=1,
                window_seconds=60,
                tokens_requested=1
            )
            
            if not is_allowed:
                logger.warning(
                    f"ValidateStatusUDIDView: Token bucket rate limit excedido - "
                    f"token={client_token[:8] if len(client_token) > 8 else client_token}..., "
                    f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                    f"ip={client_ip}, retry_after={retry_after}s"
                )
                retry_at = timezone.now() + timedelta(seconds=retry_after)
                return Response({
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "retry_after": retry_after,
                    "retry_at": retry_at.isoformat(),
                    "remaining": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })
        
        # 2. Rate limiting por UDID (Redis, sin BD): 5 intentos cada 5 min
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid,
            max_requests=5,
            window_minutes=5
        )
        
        if not is_allowed:
            retry_at = timezone.now() + timedelta(seconds=retry_after)
            return Response({
                "error_code": "UDID_STATUS_RATE_LIMIT_EXCEEDED",
                "retry_after": retry_after,
                "retry_at": retry_at.isoformat(),
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        # ========================================================================
        # AHORA SÍ: Operaciones de BD (lectura sin lock)
        # ========================================================================
        
        try:
            req = UDIDAuthRequest.objects.get(udid=udid)
        except OperationalError as e:
            return handle_view_exception("ValidateStatusUDIDView:get", e)
        except UDIDAuthRequest.DoesNotExist:
            # ✅ Log del intento con UDID inválido (asíncrono)
            logger.warning(
                f"ValidateStatusUDIDView: UDID no encontrado - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., ip={client_ip}"
            )
            log_audit_async(
                action_type='udid_validated',
                udid=udid,
                client_ip=client_ip,
                user_agent=request.META.get('HTTP_USER_AGENT'),
                details={'error': 'UDID not found'}
            )
            return Response({
                "error": "Invalid UDID"
            })

        # ✅ Verificar si está revocado
        if req.status == 'revoked':
            logger.info(
                f"ValidateStatusUDIDView: UDID revocado - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"subscriber_code={req.subscriber_code}, ip={client_ip}"
            )
            # Log del intento con UDID revocado (asíncrono)
            log_audit_async(
                action_type='udid_validated',
                subscriber_code=req.subscriber_code,
                udid=udid,
                client_ip=client_ip,
                user_agent=request.META.get('HTTP_USER_AGENT'),
                details={'error': 'UDID revoked'}
            )
            return Response({
                "error": "UDID has been revoked",
                "status": "revoked"
            }, status=status.HTTP_202_ACCEPTED)

        # ✅ NUEVA: Verificar expiración usando la nueva lógica
        if req.is_expired():
            # Marcar como expired si no lo está ya
            if req.status != 'expired':
                req.status = 'expired'
                req.save()
            
            # Log del intento con UDID expirado (asíncrono)
            log_audit_async(
                action_type='udid_validated',
                subscriber_code=req.subscriber_code,
                udid=udid,
                client_ip=client_ip,
                user_agent=request.META.get('HTTP_USER_AGENT'),
                details={'error': 'UDID expired'}
            )
            return Response({
                "error": "UDID has expired",
                "status": "expired"
            }, status=status.HTTP_410_GONE)

        # ✅ NUEVA: Obtener información detallada de expiración
        expiration_info = req.get_expiration_info()
        
        # ✅ Preparar respuesta con información completa
        response_data = {
            "udid": udid,
            "status": req.status,
            "subscriber_code": req.subscriber_code,
            "sn": req.sn,
            "expiration": expiration_info
        }
        
        # ✅ Ajustar campo 'valid' según el estado
        if req.status in ['validated', 'used']:
            # Para estados validados o usados, el UDID es válido
            response_data["valid"] = True
        elif req.status == 'pending':
            # Para pending, usar la lógica del modelo
            response_data["valid"] = req.is_valid()
        # Para 'expired' y 'revoked', omitir el campo 'valid' o usar False

        # ✅ Agregar información específica según el estado
        if req.status == 'validated':
            response_data.update({
                "validated_at": req.validated_at,
                "validated_by": req.validated_by_operator
            })
        elif req.status == 'used':
            response_data.update({
                "used_at": req.used_at,
                "credentials_delivered": req.credentials_delivered
            })
        elif req.status == 'pending':
            # Solo para pending, mostrar tiempo restante
            if expiration_info.get('time_remaining'):
                response_data["time_remaining_seconds"] = int(
                    expiration_info['time_remaining'].total_seconds()
                )

        # ✅ Log de validación exitosa (asíncrono)
        log_audit_async(
            action_type='udid_validated',
            subscriber_code=req.subscriber_code,
            udid=udid,
            client_ip=client_ip,
            user_agent=request.META.get('HTTP_USER_AGENT'),
            details={
                'status': req.status,
                'validation_successful': True
            }
        )

        # ✅ Actualizar contador de intentos si está pending
        if req.status == 'pending':
            req.attempts_count += 1
            req.save()

        # Incrementar contador de rate limiting
        increment_rate_limit_counter('udid', udid)
        
        # Agregar información de rate limit a la respuesta
        response_data["remaining_requests"] = remaining - 1
        response_data["rate_limit"] = {
            "remaining": remaining - 1,
            "reset_in_seconds": 5 * 60
        }

        return Response(response_data, status=status.HTTP_200_OK)

class DisassociateUDIDView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        """
        Paso 4: Desasociar el SN vinculado a un UDID específico
        """
        udid = request.data.get('udid')
        operator_id = request.data.get('operator_id')
        reason = request.data.get('reason', 'Voluntary disassociation')
        client_ip = get_client_ip(request)

        if not udid:
            return Response({"error": "UDID is required"}, status=status.HTTP_400_BAD_REQUEST)

        # ========================================================================
        # FAST-FAIL: Rate limiting ANTES de tocar la BD
        # ========================================================================
        
        # 1. Rate limiting con token bucket (Redis, sin BD)
        client_token = get_client_token(request)
        if client_token:
            is_allowed, remaining, retry_after = check_token_bucket_lua(
                identifier=client_token,
                capacity=5,  # 5 requests (más restrictivo para desasociación)
                refill_rate=1,  # 1 token por segundo
                window_seconds=60,
                tokens_requested=1
            )
            
            if not is_allowed:
                logger.warning(
                    f"DisassociateUDIDView: Token bucket rate limit excedido - "
                    f"token={client_token[:8] if len(client_token) > 8 else client_token}..., "
                    f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                    f"ip={client_ip}, retry_after={retry_after}s"
                )
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "Too many requests. Please retry later.",
                    "retry_after": retry_after,
                    "remaining": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })
        
        # 2. Rate limiting por UDID (Redis, sin BD)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid,
            max_requests=5,  # 5 desasociaciones por UDID cada hora
            window_minutes=60
        )
        
        if not is_allowed:
            logger.warning(
                f"DisassociateUDIDView: Rate limit excedido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"ip={client_ip}, retry_after={retry_after}s"
            )
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many disassociation attempts for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        # ========================================================================
        # OPERACIONES DE BD CON LOCKS (al final del flujo)
        # ========================================================================
        
        try:
            with transaction.atomic():
                # 3. AHORA SÍ: select_for_update() - al final, después de todas las validaciones
                try:
                    req = UDIDAuthRequest.objects.select_for_update().get(udid=udid)
                except UDIDAuthRequest.DoesNotExist:
                    return Response({"error": "UDID not found"}, status=status.HTTP_404_NOT_FOUND)

                if req.status not in ['validated', 'used', 'expired']:
                    return Response({
                        "error": f"Cannot disassociate: UDID is in state '{req.status}'"
                    }, status=status.HTTP_400_BAD_REQUEST)

                if not req.sn:
                    return Response({
                        "error": "No SN is currently associated with this UDID"
                    }, status=status.HTTP_400_BAD_REQUEST)

                old_sn = req.sn
                old_status = req.status

                # Cambiar estado y limpiar SN
                req.sn = None
                req.status = 'revoked'
                req.revoked_at = timezone.now()
                req.revoked_reason = reason
                req.save()

                # Log de auditoría (asíncrono)
                log_audit_async(
                    action_type='udid_revoked',
                    udid=req.udid,
                    subscriber_code=req.subscriber_code,
                    operator_id=operator_id,
                    client_ip=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                    details={
                        "old_sn": old_sn,
                        "old_status": old_status,
                        "revoked_at": timezone.now().isoformat(),
                        "reason": reason
                    }
                )

                return Response({
                    "message": f"UDID {req.udid} was successfully disassociated",
                    "revoked_at": req.revoked_at,
                    "subscriber_code": req.subscriber_code,
                }, status=status.HTTP_200_OK)

        except Exception as e:
            return handle_view_exception("DisassociateUDIDView", e)

class ListSubscribersWithUDIDView(APIView):
    permission_classes = [IsAuthenticated]
    """
    Devuelve una lista paginada de suscriptores con información de UDID si aplica.
    Requiere autenticación JWT.
    """
    def get(self, request):
        try:
            page_number, page_size, err = _parse_pagination_params(request)
            if err:
                return err

            subscribers = (
                SubscriberInfo.objects
                .filter(products__isnull=False)
                .exclude(Q(products__exact='') | Q(products=[]))
                .order_by('subscriber_code')
            )
            paginator = Paginator(subscribers, page_size)
            page_obj = paginator.get_page(page_number)

            udid_map = _latest_udid_map_for_subscribers(page_obj.object_list)

            data = []
            for subscriber in page_obj.object_list:
                udid_info = udid_map.get((subscriber.subscriber_code, subscriber.sn))

                # Construye el diccionario con todos los campos
                full_data = {
                    # Campos del Subscriber
                    "subscriber_code": subscriber.subscriber_code,
                    "first_name": subscriber.first_name,
                    "last_name": subscriber.last_name,
                    "sn": subscriber.sn,
                    "activated": subscriber.activated,
                    "products": subscriber.products,
                    "packages": subscriber.packages,
                    "packageNames": subscriber.packageNames,
                    "model": subscriber.model,
                    "lastActivation": subscriber.lastActivation,
                    "lastActivationIP": subscriber.lastActivationIP,
                    "lastServiceListDownload": subscriber.lastServiceListDownload,

                    # Campos del UDID (si existe)
                    "udid": udid_info.udid if udid_info else None,
                    "udid_status": udid_info.status if udid_info else None,
                    "created_at": udid_info.created_at if udid_info else None,
                    "validated_at": udid_info.validated_at if udid_info else None,
                    "user_agent": udid_info.user_agent if udid_info else None,
                    "app_type": udid_info.app_type if udid_info else None,
                    "app_version": udid_info.app_version if udid_info else None,
                    "method": udid_info.method if udid_info else None,
                    "validated_by_operator": udid_info.validated_by_operator if udid_info else None,
                }
                
                # Crea un nuevo diccionario excluyendo los campos con valores nulos, listas vacías, o strings vacíos.
                clean_data = {key: value for key, value in full_data.items() if value is not None and value != [] and value != ''}
                
                data.append(clean_data)

            return Response({
                "count": paginator.count,
                "total_pages": paginator.num_pages,
                "current_page": page_obj.number,
                "results": data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return handle_view_exception("ListSubscribersWithUDIDView", e)

class SubscriberInfoListView(APIView):
    permission_classes = [IsAuthenticated]
    """
    Lista suscriptores con filtros (?subscriber_code=, ?sn=) y búsqueda (?search=).
    Devuelve los mismos parámetros que ListSubscribersWithUDIDView (incl. campos UDID).
    """
    def get(self, request):
        try:
            page_number, page_size, err = _parse_pagination_params(request)
            if err:
                return err

            search = request.query_params.get('search', '').strip()
            subscriber_code_filter = request.query_params.get('subscriber_code', '').strip()
            sn_filter = request.query_params.get('sn', '').strip()

            subscribers = SubscriberInfo.objects.all().order_by('subscriber_code')

            if subscriber_code_filter:
                subscribers = subscribers.filter(subscriber_code=subscriber_code_filter)
            if sn_filter:
                subscribers = subscribers.filter(sn=sn_filter)
            if search:
                search_q = Q(subscriber_code__icontains=search) | Q(sn__icontains=search)
                if search.isdigit():
                    search_q |= Q(login1=int(search))
                subscribers = subscribers.filter(search_q)

            paginator = Paginator(subscribers, page_size)
            page_obj = paginator.get_page(page_number)

            udid_map = _latest_udid_map_for_subscribers(page_obj.object_list)

            data = []
            for subscriber in page_obj.object_list:
                udid_info = udid_map.get((subscriber.subscriber_code, subscriber.sn))

                full_data = {
                    "subscriber_code": subscriber.subscriber_code,
                    "first_name": subscriber.first_name,
                    "last_name": subscriber.last_name,
                    "sn": subscriber.sn,
                    "activated": subscriber.activated,
                    "products": subscriber.products,
                    "packages": subscriber.packages,
                    "packageNames": subscriber.packageNames,
                    "model": subscriber.model,
                    "lastActivation": subscriber.lastActivation,
                    "lastActivationIP": subscriber.lastActivationIP,
                    "lastServiceListDownload": subscriber.lastServiceListDownload,
                    "udid": udid_info.udid if udid_info else None,
                    "udid_status": udid_info.status if udid_info else None,
                    "created_at": udid_info.created_at if udid_info else None,
                    "validated_at": udid_info.validated_at if udid_info else None,
                    "user_agent": udid_info.user_agent if udid_info else None,
                    "app_type": udid_info.app_type if udid_info else None,
                    "app_version": udid_info.app_version if udid_info else None,
                    "method": udid_info.method if udid_info else None,
                    "validated_by_operator": udid_info.validated_by_operator if udid_info else None,
                }
                clean_data = {k: v for k, v in full_data.items() if v is not None and v != [] and v != ''}
                data.append(clean_data)

            return Response({
                "count": paginator.count,
                "total_pages": paginator.num_pages,
                "current_page": page_obj.number,
                "results": data
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return handle_view_exception("SubscriberInfoListView", e)

class MetricsDashboardView(APIView):
    """
    Vista del dashboard de métricas del sistema (solo JSON para pruebas).
    Muestra métricas de latencia, errores, concurrencia, CPU, RAM, Redis y WebSockets.
    """
    permission_classes = [IsAdminUser]
    
    def get(self, request):
        """
        Retorna métricas del sistema en formato JSON.
        Útil para pruebas y monitoreo durante desarrollo.
        """
        metrics = get_metrics()
        return Response(metrics, status=status.HTTP_200_OK)
    
    def post(self, request):
        """
        Resetea las métricas del sistema.
        """
        reset_metrics()
        return Response({
            "message": "Metrics reset successfully",
            "timestamp": timezone.now().isoformat()
        }, status=status.HTTP_200_OK)

class ManualSyncView(APIView):
    """
    Endpoint para ejecutar manualmente las tareas de sincronización.
    Permite actualizar suscriptores y SN desde el frontend.
    """
    permission_classes = [IsAuthenticated]  # Requiere autenticación
    
    def post(self, request):
        """
        Ejecuta todas las tareas de sincronización manualmente.
        
        Returns:
            Response con el resultado de la sincronización
        """
        logger.info(f"ManualSyncView: Sincronización manual solicitada por usuario {request.user.username if hasattr(request.user, 'username') else 'unknown'}")
        
        try:
            # Ejecutar las tareas de sincronización
            result = execute_sync_tasks()
            
            if result['success']:
                return Response({
                    'success': True,
                    'message': result['message'],
                    'tasks': result['tasks'],
                    'session_id': result['session_id']
                }, status=status.HTTP_200_OK)
            else:
                # Algunas tareas fallaron, pero devolvemos el resultado completo
                return Response({
                    'success': False,
                    'message': result['message'],
                    'tasks': result['tasks'],
                    'session_id': result['session_id']
                }, status=status.HTTP_207_MULTI_STATUS)  # 207 indica éxito parcial
            
        except Exception as e:
            return handle_view_exception("ManualSyncView", e)