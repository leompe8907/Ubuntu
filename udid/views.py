from rest_framework.views import APIView
from rest_framework import status, filters
from rest_framework.response import Response
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from django.db.models import Q
from django.db import transaction
from django.utils import timezone
from django.core.paginator import Paginator

from asgiref.sync import async_to_sync

from channels.layers import get_channel_layer

from datetime import timedelta

import logging
import secrets
import hashlib
import json

from .management.commands.keyGenerator import hybrid_encrypt_for_app
from .serializers import UDIDAssociationSerializer, PublicSubscriberInfoSerializer
from .util import (
    get_client_ip, compute_encrypted_hash, json_serialize_credentials, is_valid_app_type,
    generate_device_fingerprint, check_device_fingerprint_rate_limit, check_udid_rate_limit,
    check_combined_rate_limit, increment_rate_limit_counter,
    is_legitimate_reconnection, check_adaptive_rate_limit,
    should_apply_retry_delay, reset_retry_info, get_retry_info
)
from .models import UDIDAuthRequest, AuthAuditLog, SubscriberInfo, AppCredentials, EncryptedCredentialsLog

logger = logging.getLogger(__name__)

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
            # CAPA 1: Rate limiting por Device Fingerprint (en lugar de IP)
            device_fingerprint = generate_device_fingerprint(request)
            
            is_allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
                device_fingerprint,
                max_requests=2,  # Reducido de 3 a 2 requests por fingerprint
                window_minutes=10  # Aumentado de 5 a 10 minutos
            )
            
            if not is_allowed:
                logger.warning(
                    f"RequestUDIDManualView: Rate limit excedido - "
                    f"device_fingerprint={device_fingerprint[:8]}..., ip={client_ip}, "
                    f"retry_after={retry_after}s"
                )
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "Too many requests from this device. Please try again later.",
                    "retry_after": retry_after,
                    "remaining_requests": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })

            # Generar UDID único
            udid = self.generate_unique_udid()
            
            # Crear solicitud con device_fingerprint
            auth_request = UDIDAuthRequest.objects.create(
                udid=udid,
                status='pending',
                client_ip=client_ip,
                user_agent=request.META.get('HTTP_USER_AGENT', ''),
                device_fingerprint=device_fingerprint
            )
            
            # ✅ Verificar que se guardó correctamente (recargar desde BD)
            auth_request.refresh_from_db()
            
            # Incrementar contador
            increment_rate_limit_counter('device_fp', device_fingerprint)
            
            # Log de auditoría
            AuthAuditLog.objects.create(
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
                "expires_in_minutes": 15,
                "device_fingerprint": auth_request.device_fingerprint,  # ✅ Agregado a la respuesta
                "rate_limit": {
                    "remaining": remaining - 1,
                    "reset_in_seconds": 10 * 60  # Actualizado a 10 minutos
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(
                f"RequestUDIDManualView: Error interno - "
                f"ip={client_ip}, error={str(e)}", exc_info=True
            )
            return Response({
                "error": "Internal server error"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def generate_unique_udid(self):
        """Generar UDID único de 8 caracteres"""
        while True:
            udid = secrets.token_hex(4)  # 8 caracteres hexadecimales
            if not UDIDAuthRequest.objects.filter(udid=udid).exists():
                return udid

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
        
        serializer = UDIDAssociationSerializer(data=request.data)
        
        if not serializer.is_valid():
            logger.warning(
                f"ValidateAndAssociateUDIDView: Datos inválidos - "
                f"ip={client_ip}, errors={serializer.errors}"
            )
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        
        data = serializer.validated_data
        subscriber   = data["subscriber"]
        udid_request = data["udid_request"]   # instancia ya validada por el serializer
        sn           = data["sn"]
        operator_id  = data["operator_id"]
        method       = data["method"]
        
        # Obtener UDID del request
        udid = udid_request.udid if hasattr(udid_request, 'udid') else None
        
        # Inicializar variables para rate limiting
        remaining = None
        retry_after = None
        
        # CAPA 3: Rate limiting por UDID
        if udid:
            is_allowed, remaining, retry_after = check_udid_rate_limit(
                udid,
                max_requests=5,  # Reducido de 10 a 5 para operaciones críticas
                window_minutes=60
            )
            
            if not is_allowed:
                logger.warning(
                    f"ValidateAndAssociateUDIDView: Rate limit excedido - "
                    f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                    f"ip={client_ip}, retry_after={retry_after}s"
                )
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "Too many association attempts for this UDID. Please try again later.",
                    "retry_after": retry_after,
                    "remaining_requests": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })

        # Hacemos todo atómicamente y notificamos al WS SOLO tras el commit
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
            response_data["rate_limit"] = {
                "remaining": remaining - 1,
                "reset_in_seconds": 60 * 60
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

        # Auditoría
        AuthAuditLog.objects.create(
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
        
        # Validar app_type usando la función centralizada
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

        # Verificar si es reconexión legítima
        is_reconnection = is_legitimate_reconnection(udid)
        
        # Verificar si se debe aplicar delay de retry (exponential backoff con jitter)
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
            return Response({
                "error": "Service temporarily unavailable",
                "message": "System is handling high reconnection volume. Please retry after a short delay.",
                "retry_after": retry_delay,
                "attempt": attempt_number,
                "is_reconnection": is_reconnection
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE, headers={
                "Retry-After": str(retry_delay),
                "X-Retry-After": str(retry_delay)
            })

        # Rate limiting adaptativo (usa check_adaptive_rate_limit si es reconexión)
        if is_reconnection:
            is_allowed, remaining, retry_after, reason = check_adaptive_rate_limit(
                'udid', udid, is_reconnection=True
            )
        else:
            is_allowed, remaining, retry_after = check_udid_rate_limit(
                udid,
                max_requests=5,  # Reducido de 10 a 5 para operaciones críticas
                window_minutes=60
            )
        
        if not is_allowed:
            # Si es reconexión y fue rechazada, calcular delay adicional
            if is_reconnection:
                retry_delay, _ = get_retry_info(udid, 'reconnection')
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "System is handling high reconnection volume. Please retry.",
                    "retry_after": retry_delay,
                    "is_reconnection": True,
                    "reason": reason if is_reconnection else None
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_delay)
                })
            else:
                return Response({
                    "error": "Rate limit exceeded",
                    "message": "Too many authentication attempts for this UDID. Please try again later.",
                    "retry_after": retry_after,
                    "remaining_requests": remaining
                }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                    "Retry-After": str(retry_after)
                })

        try:
            with transaction.atomic():
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

                # Obtener AppCredentials válidas
                try:
                    app_credentials = AppCredentials.objects.get(
                        app_type=app_type,
                        app_version=app_version,
                        is_active=True
                    )
                    if not app_credentials.is_usable():
                        raise AppCredentials.DoesNotExist()
                except AppCredentials.DoesNotExist:
                    app_credentials = AppCredentials.objects.filter(
                        app_type=app_type,
                        is_active=True,
                        is_compromised=False
                    ).order_by('-created_at').first()
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
                    return Response({
                        "error": "Encryption failed",
                        "details": str(e)
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

                # Marcar como entregado
                req.app_type = app_type
                req.app_version = app_version
                req.app_credentials_used = app_credentials
                req.mark_credentials_delivered(app_credentials)
                req.mark_as_used()

                # Log de auditoría
                AuthAuditLog.objects.create(
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
                    "rate_limit": {
                        "remaining": remaining - 1,
                        "reset_in_seconds": 60 * 60
                    }
                }, status=status.HTTP_200_OK)

        except Exception as e:
            # En caso de error, incrementar retry info (para next retry)
            if is_reconnection:
                get_retry_info(udid, 'reconnection')  # Esto incrementa el contador
            
            logger.error(
                f"AuthenticateWithUDIDView: Error interno - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"app_type={app_type}, ip={client_ip}, error={str(e)}", exc_info=True
            )
            
            return Response({
                "error": "Internal server error",
                "details": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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

        # CAPA 3: Rate limiting por UDID (ajustado: más restrictivo)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid,
            max_requests=20,  # Reducido de 30 a 20 validaciones por UDID cada 5 minutos
            window_minutes=5
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many status checks for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        try:
            req = UDIDAuthRequest.objects.get(udid=udid)
        except UDIDAuthRequest.DoesNotExist:
            # ✅ Log del intento con UDID inválido
            logger.warning(
                f"ValidateStatusUDIDView: UDID no encontrado - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., ip={client_ip}"
            )
            AuthAuditLog.objects.create(
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
            # Log del intento con UDID revocado
            AuthAuditLog.objects.create(
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
            
            # Log del intento con UDID expirado
            AuthAuditLog.objects.create(
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

        # ✅ Log de validación exitosa
        AuthAuditLog.objects.create(
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

        if not udid:
            return Response({"error": "UDID is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
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

                # Log de auditoría
                AuthAuditLog.objects.create(
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
            return Response({
                "error": "Internal server error",
                "details": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class ListSubscribersWithUDIDView(APIView):
    permission_classes = [IsAuthenticated]
    """
    Devuelve una lista paginada de suscriptores con información de UDID si aplica.
    """
    def get(self, request):
        try:
            page_number = request.query_params.get('page', 1)
            page_size = request.query_params.get('page_size', 20)

            subscribers = (
                SubscriberInfo.objects
                .filter(products__isnull=False)
                .exclude(Q(products__exact='') | Q(products=[]))
                .order_by('subscriber_code')
            )
            paginator = Paginator(subscribers, page_size)
            page_obj = paginator.get_page(page_number)

            data = []
            for subscriber in page_obj.object_list:
                udid_info = UDIDAuthRequest.objects.filter(
                    subscriber_code=subscriber.subscriber_code,
                    sn=subscriber.sn,
                    status__in=['validated','used', 'revoked']
                ).order_by('-validated_at').first()

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
            return Response({
                "error": "Error al obtener la información",
                "details": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

class SubscriberInfoListView(ListAPIView):
    permission_classes = [IsAuthenticated]
    queryset = SubscriberInfo.objects.all().order_by('subscriber_code')
    serializer_class = PublicSubscriberInfoSerializer
    filter_backends = [DjangoFilterBackend, filters.SearchFilter]
    
    # 🔍 Filtros exactos (parámetros: ?subscriber_code=123&sn=XYZ)
    filterset_fields = ['subscriber_code', 'sn']
    
    # 🔎 Búsqueda parcial (parámetro: ?search=juan)
    search_fields = ['subscriber_code', 'sn', 'login1']