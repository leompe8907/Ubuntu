from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

from django.utils import timezone
from django.db import transaction
from django.db.utils import OperationalError

from datetime import timedelta

import uuid
import secrets
import hashlib
import json
import logging

from udid.models import UDIDAuthRequest, ListOfSubscriber, SubscriberInfo, AppCredentials, EncryptedCredentialsLog
from udid.utils.server.log_buffer import log_audit_async
from udid.util import (
    generate_device_fingerprint,
    check_device_fingerprint_rate_limit,
    check_udid_rate_limit,
    check_temp_token_rate_limit,
    check_combined_rate_limit,
    increment_rate_limit_counter,
    is_valid_app_type
)

from .management.commands.keyGenerator import hybrid_encrypt_for_app, rsa_encrypt_for_app
from .api_errors import handle_view_exception, response_encryption_unavailable

logger = logging.getLogger(__name__)

class RequestUDIDView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        """
        Genera un UDID único para el dispositivo.
        PROTEGIDO POR: Device Fingerprint Rate Limiting
        """
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        logger.info(
            f"RequestUDIDView: Request recibido - "
            f"ip={client_ip}, user_agent={user_agent[:100] if user_agent else 'N/A'}"
        )
        
        try:
            # CAPA 1: Rate limiting por Device Fingerprint
            device_fingerprint = generate_device_fingerprint(request)
            
            # Verificar rate limit: 3 solicitudes por dispositivo en 5 minutos
            is_allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
                device_fingerprint,
                max_requests=3,
                window_minutes=5
            )
            
            if not is_allowed:
                logger.warning(
                    f"RequestUDIDView: Rate limit excedido - "
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
            
            # Obtener IP y user agent del dispositivo
            client_ip = request.META.get('REMOTE_ADDR')
            user_agent = request.META.get('HTTP_USER_AGENT', '')

            # Generar UDID y token únicos
            generated_udid = str(uuid.uuid4())
            temp_token = secrets.token_urlsafe(32)

            # Crear la solicitud con device_fingerprint
            auth_request = UDIDAuthRequest.objects.create(
                udid=generated_udid,
                temp_token=temp_token,
                status='pending',
                client_ip=client_ip,
                user_agent=user_agent,
                device_fingerprint=device_fingerprint  # Guardar fingerprint
            )
            
            # Incrementar contador en cache
            increment_rate_limit_counter('device_fp', device_fingerprint)
            
            # Log de auditoría (asíncrono)
            log_audit_async(
                action_type='udid_generated',
                udid=generated_udid,
                client_ip=client_ip,
                user_agent=user_agent,
                details={
                    'method': 'automatic_request',
                    'device_fingerprint': device_fingerprint,
                    'rate_limit_remaining': remaining - 1
                }
            )

            logger.info(
                f"RequestUDIDView: UDID generado exitosamente - "
                f"udid={generated_udid}, device_fingerprint={device_fingerprint[:8]}..., "
                f"ip={client_ip}, expires_at={auth_request.expires_at}"
            )
            
            return Response({
                "udid": auth_request.udid,
                "temp_token": auth_request.temp_token,
                "expires_at": auth_request.expires_at,
                "status": auth_request.status,
                "rate_limit": {
                    "remaining": remaining - 1,
                    "reset_in_seconds": 10 * 60  # Actualizado a 10 minutos
                }
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            return handle_view_exception(f"RequestUDIDView ip={client_ip}", e)

class ValidateUDIDView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        """
        Valida un UDID con subscriber_code.
        PROTEGIDO POR: Combined Rate Limiting (UDID + Temp Token)
        """
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        # Intentar obtener parámetros del body primero, luego de query params
        udid = request.data.get('udid') or request.query_params.get('udid')
        temp_token = request.data.get('temp_token') or request.query_params.get('temp_token')
        subscriber_code = request.data.get('subscriber_code') or request.query_params.get('subscriber_code')
        operator_id = request.data.get('operator_id') or request.query_params.get('operator_id')

        logger.info(
            f"ValidateUDIDView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"subscriber_code={subscriber_code}, operator_id={operator_id}, ip={client_ip}"
        )

        # Validaciones iniciales
        if not all([udid, temp_token, subscriber_code]):
            logger.warning(
                f"ValidateUDIDView: Parámetros incompletos - "
                f"udid={'presente' if udid else 'faltante'}, "
                f"temp_token={'presente' if temp_token else 'faltante'}, "
                f"subscriber_code={'presente' if subscriber_code else 'faltante'}, ip={client_ip}"
            )
            return Response({
                "error": "Parámetros incompletos.",
                "required": ["udid", "temp_token", "subscriber_code"]
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # CAPA 4: Rate limiting combinado (UDID + Temp Token)
        is_allowed, remaining, retry_after, reason = check_combined_rate_limit(
            udid, temp_token,
            max_requests=10,  # 10 validaciones por combinación cada 5 minutos
            window_minutes=5
        )
        
        if not is_allowed:
            logger.warning(
                f"ValidateUDIDView: Rate limit excedido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"reason={reason}, ip={client_ip}, retry_after={retry_after}s"
            )
            return Response({
                "error": "Rate limit exceeded",
                "message": f"Too many validation attempts. {reason}",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        try:
            req = UDIDAuthRequest.objects.get(udid=udid, temp_token=temp_token)
        except UDIDAuthRequest.DoesNotExist:
            logger.warning(
                f"ValidateUDIDView: UDID o token inválido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., ip={client_ip}"
            )
            return Response({
                "error": "Solicitud inválida o token incorrecto."
            }, status=status.HTTP_404_NOT_FOUND)

        if req.status != "pending":
            logger.warning(
                f"ValidateUDIDView: UDID no está en estado pending - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"status={req.status}, ip={client_ip}"
            )
            return Response({
                "error": "El UDID ya fue validado, usado o revocado.",
                "current_status": req.status
            }, status=status.HTTP_400_BAD_REQUEST)

        if req.is_expired():
            logger.warning(
                f"ValidateUDIDView: Token expirado - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"expires_at={req.expires_at}, ip={client_ip}"
            )
            req.status = "expired"
            req.save()
            return Response({
                "error": "El token ha expirado."
            }, status=status.HTTP_400_BAD_REQUEST)

        if not ListOfSubscriber.objects.filter(code=subscriber_code).exists():
            logger.warning(
                f"ValidateUDIDView: Subscriber code inválido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"subscriber_code={subscriber_code}, ip={client_ip}"
            )
            return Response({
                "error": "Subscriber code no válido."
            }, status=status.HTTP_404_NOT_FOUND)

        # Expirar filas colgantes del mismo subscriber antes de validar (merge con segunda definición previa)
        UDIDAuthRequest.objects.filter(
            subscriber_code=subscriber_code,
            expires_at__lt=timezone.now(),
            status__in=['validated', 'pending'],
        ).update(status='expired', sn=None)

        # Actualizar registro
        req.status = "validated"
        req.validated_at = timezone.now()
        req.subscriber_code = subscriber_code
        req.validated_by_operator = operator_id
        req.save()

        # Incrementar contadores en cache
        increment_rate_limit_counter('udid', udid)
        increment_rate_limit_counter('temp_token', temp_token)

        # Log de auditoría (asíncrono)
        log_audit_async(
            action_type='udid_validated',
            udid=udid,
            subscriber_code=subscriber_code,
            operator_id=operator_id,
            client_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            details={
                "message": "UDID validado correctamente",
                "rate_limit_remaining": remaining - 1
            }
        )

        return Response({
            "message": "UDID validado exitosamente.",
            "udid": udid,
            "subscriber_code": subscriber_code,
            "expires_at": req.expires_at,
            "rate_limit": {
                "remaining": remaining - 1,
                "reset_in_seconds": 5 * 60
            }
        }, status=status.HTTP_200_OK)

class GetSubscriberInfoView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        udid = request.query_params.get('udid')
        app_type = request.query_params.get('app_type')
        app_version = request.query_params.get('app_version', '1.0')

        logger.info(
            f"GetSubscriberInfoView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"app_type={app_type}, app_version={app_version}, ip={client_ip}"
        )

        #✅ Validar que se haya pasado el UDID
        if not udid:
            logger.warning(
                f"GetSubscriberInfoView: UDID faltante - ip={client_ip}"
            )
            return Response({
                "error": "Parámetro 'udid' requerido."
            }, status=status.HTTP_400_BAD_REQUEST)

        # CAPA 3: Rate limiting por UDID (ajustado: más restrictivo)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid,
            max_requests=10,  # Reducido de 20 a 10 requests por UDID cada hora
            window_minutes=60
        )
        
        if not is_allowed:
            logger.warning(
                f"GetSubscriberInfoView: Rate limit excedido - "
                f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
                f"ip={client_ip}, retry_after={retry_after}s"
            )
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many requests for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        if not is_valid_app_type(app_type):
            return Response({
                "error": f"Invalid app_type. Must be one of: android_tv, samsung_tv, lg_tv, set_top_box, mobile_app, web_player",
                "received": app_type
            }, status=status.HTTP_400_BAD_REQUEST)

        return self._execute_get_subscriber_info(
            request, udid, app_type, app_version, client_ip, user_agent, remaining
        )

    @transaction.atomic
    def _execute_get_subscriber_info(self, request, udid, app_type, app_version, client_ip, user_agent, remaining):
        # Este método corre dentro de una transacción: si SQLite se bloquea o hay errores de BD
        # debemos convertirlos en 503/409/400 coherentes para no devolver 500 y “parecer” que el
        # servidor quedó colgado (típico de locks de SQLite bajo concurrencia).
        try:
            try:
                req = UDIDAuthRequest.objects.select_for_update().get(udid=udid)
            except UDIDAuthRequest.DoesNotExist:
                return Response({"error": "UDID no encontrado."}, status=status.HTTP_404_NOT_FOUND)

            if req.status != "validated":
                return Response({
                    "error": f"UDID no está validado. Estado actual: {req.status}"
                }, status=status.HTTP_403_FORBIDDEN)

            if req.is_expired():
                req.status = "expired"
                req.save()
                return Response({
                    "error": "El token ha expirado."
                }, status=status.HTTP_403_FORBIDDEN)

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
                    is_active=True
                ).exclude(
                    is_compromised=True
                ).order_by('-created_at').first()
                if not app_credentials:
                    return Response({
                        "error": f"No hay credenciales seguras disponibles para app_type='{app_type}'",
                        "details": {
                            "requested_version": app_version,
                            "app_type": app_type,
                            "solution": "Contacte al administrador para generar nuevas credenciales"
                        }
                    }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={"Retry-After": "10"})

            req.app_type = app_type
            req.app_version = app_version
            req.app_credentials_used = app_credentials
            req.save()

            if req.is_expired():
                req.status = "expired"
                req.save()
                return Response({"error": "El token ha expirado."}, status=status.HTTP_403_FORBIDDEN)

            #✅ PASO 1: Buscar subscriber code
            subscriber_code = req.subscriber_code

            #✅ PASO 2: Filtrar todas las SNs del subscriber con productos asociados
            subscriber_infos = SubscriberInfo.objects.filter(
                subscriber_code=subscriber_code
            ).exclude(
                products__isnull=True
            ).exclude(
                products=[]
            )

            if not subscriber_infos.exists():
                self._log_failed_attempt(req, "No smartcards with products", request)
                req.mark_as_used()
                return Response({
                    "error": "El usuario no tiene productos asociados a su cuenta."
                }, status=status.HTTP_404_NOT_FOUND)
        except OperationalError as e:
            return handle_view_exception("GetSubscriberInfoView:db", e)
        except Exception as e:
            return handle_view_exception("GetSubscriberInfoView:unexpected", e)

        #✅ PASO 3: Validar qué SNs están asociados a UDIDs activos (CUALQUIER APP_TYPE)
        used_sns_via_udid = UDIDAuthRequest.objects.filter(
            status__in=['validated', 'used'],
            subscriber_code=subscriber_code,
            expires_at__gte=timezone.now(),
            sn__isnull=False
            # ❌ NO filtrar por app_type - queremos ALL SNs ocupadas
        ).exclude(
            udid=udid  # Excluir el UDID actual
        ).values_list('sn', flat=True)

        # ✅ OBTENER DETALLES DE SNs EN USO PARA DEBUG
        used_sns_with_app_type = UDIDAuthRequest.objects.filter(
            status__in=['validated', 'used'],
            subscriber_code=subscriber_code,
            expires_at__gte=timezone.now(),
            sn__isnull=False
        ).exclude(
            udid=udid
        ).values('sn', 'app_type', 'udid')

        logger.debug(
            "GetSubscriberInfoView: subscriber=%s app_type=%s used_sns=%s details=%s total_candidates=%s",
            subscriber_code,
            app_type,
            list(used_sns_via_udid),
            list(used_sns_with_app_type),
            subscriber_infos.count(),
        )

        #✅ PASO 4: Buscar SN disponible (que NO esté en uso por NINGÚN tipo de app)
        selected_subscriber = None
        available_sns = []
        
        for sub in subscriber_infos:
            if sub.sn not in used_sns_via_udid:
                available_sns.append(sub.sn)
                if not selected_subscriber:
                    selected_subscriber = sub
        
        logger.debug("GetSubscriberInfoView: available_sns=%s", available_sns)
        
        #✅ PASO 5: Si no hay SNs disponibles, mostrar detalles específicos
        if not selected_subscriber:
            # Crear información detallada del uso de SNs
            usage_details = {}
            for usage in used_sns_with_app_type:
                sn = usage['sn']
                app_type_used = usage['app_type']
                if sn not in usage_details:
                    usage_details[sn] = []
                usage_details[sn].append(app_type_used)
            
            self._log_failed_attempt(req, "All SNs occupied by different app types", request, {
                "total_sns": subscriber_infos.count(),
                "sn_usage_details": usage_details,
                "requested_app_type": app_type
            })
            req.mark_as_used()
            
            return Response({
                "error": f"❌ El usuario {subscriber_code} no tiene smartcards disponibles. Todas están en uso por otros dispositivos.",
                "details": {
                    "subscriber_code": subscriber_code,
                    "requested_app_type": app_type,
                    "total_smartcards": subscriber_infos.count(),
                    "smartcards_in_use": len(used_sns_via_udid),
                    "available_smartcards": 0,
                    "usage_breakdown": usage_details,
                    "message": "Cada smartcard solo puede estar activa en un tipo de dispositivo a la vez",
                    "retry_after_minutes": 15
                }
            }, status=status.HTTP_409_CONFLICT)

        #✅ PASO 6: Asignar el SN seleccionado al UDIDAuthRequest
        req.sn = selected_subscriber.sn
        req.save()

        logger.debug("GetSubscriberInfoView: sn_assigned=%s udid=%s", selected_subscriber.sn, udid)

        # ✅ ENCRIPTACIÓN SEGURA
        try:
            plain_password = selected_subscriber.get_password()
            if not plain_password:
                raise Exception("Password no disponible")
            
            # Crear payload con todas las credenciales
            credentials_payload = {
                "password": plain_password,
                "subscriber_code": subscriber_code,
                "sn": selected_subscriber.sn,
                "timestamp": timezone.now().isoformat()
            }
            
            # Encriptar con sistema híbrido
            encrypted_result = hybrid_encrypt_for_app(
                json.dumps(credentials_payload), 
                app_type
            )
            
            # ✅ MARCAR ENTREGA EXITOSA
            req.mark_credentials_delivered(app_credentials)
            
        except Exception as e:
            self._log_failed_attempt(req, "Encryption error", request)
            return response_encryption_unavailable("GetSubscriberInfoView:encrypt", e)

        # ✅ PREPARAR RESPUESTA SEGURA
        response_data = {
            #"sn": selected_subscriber.sn,
            "products": selected_subscriber.products,
            #"packages": selected_subscriber.packages,
            #"packageNames": selected_subscriber.packageNames,
            "login1": selected_subscriber.login1,
            #"login2": selected_subscriber.login2,
            "model": selected_subscriber.model,
            
            # ✅ CREDENCIALES ENCRIPTADAS
            "encrypted_credentials": encrypted_result,
            "security_info": {
                "encryption_method": "Hybrid AES-256 + RSA-OAEP",
                "app_type": app_type,
                "app_version": app_credentials.app_version,
                "key_fingerprint": app_credentials.key_fingerprint
            }
        }

        # ✅ LOG DE AUDITORÍA DETALLADO
        self._log_successful_delivery(req, selected_subscriber, app_credentials, request, len(available_sns))
        
        # ✅ LOG DE CREDENCIALES ENCRIPTADAS
        self._log_encrypted_credentials(req, encrypted_result, app_credentials, request)

        # ✅ MARCAR COMO USADO
        req.mark_as_used()
        
        # Incrementar contador de rate limiting
        increment_rate_limit_counter('udid', udid)

        logger.info(
            f"GetSubscriberInfoView: Información obtenida exitosamente - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"subscriber_code={subscriber_code}, sn_assigned={selected_subscriber.sn}, "
            f"app_type={app_type}, available_smartcards={len(available_sns)}, ip={client_ip}"
        )

        return Response({
            "subscriber_code": subscriber_code,
            "data": response_data,
            "metadata": {
                "total_smartcards": subscriber_infos.count(),
                "available_smartcards": len(available_sns),
                "sn_assigned": selected_subscriber.sn,
                "security_level": "HIGH",
                "app_info": {
                    "app_type": app_type,
                    "app_version": app_credentials.app_version,
                    "credentials_fingerprint": app_credentials.key_fingerprint
                },
                "rate_limit": {
                    "remaining": remaining - 1,
                    "reset_in_seconds": 60 * 60
                }
            }
        }, status=status.HTTP_200_OK)

    def _log_failed_attempt(self, req, error_message, request, extra_details=None):
        """Log de intentos fallidos"""
        details = {
            "error": error_message,
            "app_type": req.app_type,
            "app_version": req.app_version
        }
        if extra_details:
            details.update(extra_details)
            
        # Log de auditoría (asíncrono)
        log_audit_async(
            action_type='login_failed',
            udid=req.udid,
            subscriber_code=req.subscriber_code,
            client_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            details=details
        )

    def _log_successful_delivery(self, req, subscriber, app_credentials, request, available_count):
        """Log de entrega exitosa (asíncrono)"""
        log_audit_async(
            action_type='udid_used',
            udid=req.udid,
            subscriber_code=req.subscriber_code,
            client_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            details={
                "sn_assigned": subscriber.sn,
                "app_type": req.app_type,
                "app_version": req.app_version,
                "key_fingerprint": app_credentials.key_fingerprint,
                "available_smartcards": available_count,
                "encryption_method": "Hybrid AES-256 + RSA-OAEP",
                "security_level": "HIGH"
            }
        )

    def _log_encrypted_credentials(self, req, encrypted_result, app_credentials, request):
        """Log específico de credenciales encriptadas"""
        # Hash del payload encriptado para auditoría
        encrypted_hash = hashlib.sha256(
            encrypted_result["encrypted_data"].encode()
        ).hexdigest()
        
        EncryptedCredentialsLog.objects.create(
            udid=req.udid,
            subscriber_code=req.subscriber_code,
            sn=req.sn,
            app_type=req.app_type,
            app_version=req.app_version,
            app_credentials_id=app_credentials,
            encrypted_data_hash=encrypted_hash,
            client_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            delivered_successfully=True
        )

class RevokeUDIDView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        udid = request.data.get('udid')
        operator = request.data.get('operator_id', 'manual')
        reason = request.data.get('reason', 'Revocación manual')

        logger.info(
            f"RevokeUDIDView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"operator={operator}, reason={reason}, ip={client_ip}"
        )

        if not udid:
            logger.warning(
                f"RevokeUDIDView: UDID faltante - ip={client_ip}"
            )
            return Response({"error": "Parámetro 'udid' es requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Rate limiting por UDID (previene revocaciones masivas)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid, max_requests=3, window_minutes=60
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many revocation attempts for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        # Incrementar contador
        increment_rate_limit_counter('udid', udid)

        try:
            req = UDIDAuthRequest.objects.get(udid=udid)
        except UDIDAuthRequest.DoesNotExist:
            return Response({"error": "UDID no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        if req.status in ['revoked', 'expired', 'used']:
            return Response({"error": f"No se puede revocar. Estado actual: {req.status}"}, status=status.HTTP_403_FORBIDDEN)

        req.status = 'revoked'
        req.validated_by_operator = operator
        req.save()

        # Guardar log incluyendo el SN si está disponible (asíncrono)
        log_audit_async(
            action_type='account_locked',
            subscriber_code=req.subscriber_code,
            udid=req.udid,
            operator_id=operator,
            details={
                "reason": reason,
                "sn": req.sn  # Incluir el SN en los detalles
            },
            client_ip=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

        return Response({
            "message": "UDID revocado correctamente.",
            "udid": udid,
            "sn": req.sn  # Incluir SN en la respuesta
        }, status=status.HTTP_200_OK)

class ListUDIDRequestsView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        # Rate limiting por device fingerprint (previene abuso de queries)
        device_fingerprint = generate_device_fingerprint(request)
        is_allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
            device_fingerprint,
            max_requests=10,  # 10 consultas por dispositivo cada 5 minutos
            window_minutes=5
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many requests. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        # Incrementar contador
        increment_rate_limit_counter('device_fp', device_fingerprint)
        
        subscriber_code = request.query_params.get('subscriber_code')
        status_filter = request.query_params.get('status')
        udid = request.query_params.get('udid')
        active_only = request.query_params.get('active') == 'true'

        qs = UDIDAuthRequest.objects.all()

        if subscriber_code:
            qs = qs.filter(subscriber_code=subscriber_code)

        if status_filter:
            qs = qs.filter(status=status_filter)

        if udid:
            qs = qs.filter(udid=udid)

        if active_only:
            qs = qs.filter(
                status__in=['pending', 'validated'],
                expires_at__gte=timezone.now()
            )

        qs = qs.order_by('-created_at')[:100]  # Máximo 100 resultados

        data = []
        for obj in qs:
            data.append({
                "udid": obj.udid,
                "subscriber_code": obj.subscriber_code,
                "status": obj.status,
                "created_at": obj.created_at,
                "expires_at": obj.expires_at,
                "validated_at": obj.validated_at,
                "used_at": obj.used_at,
                "validated_by_operator": obj.validated_by_operator,
                "device_fingerprint": obj.device_fingerprint,
                "client_ip": obj.client_ip,
                "attempts_count": obj.attempts_count,
            })

        return Response({
            "results": data,
            "count": len(data),
            "rate_limit": {
                "remaining": remaining - 1,
                "reset_in_seconds": 5 * 60
            }
        }, status=status.HTTP_200_OK)

class SNUsageStatsView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        subscriber_code = request.query_params.get('subscriber_code')
        
        logger.info(
            f"SNUsageStatsView: Request recibido - "
            f"subscriber_code={subscriber_code}, ip={client_ip}"
        )
        
        # Rate limiting por device fingerprint
        device_fingerprint = generate_device_fingerprint(request)
        is_allowed, remaining, retry_after = check_device_fingerprint_rate_limit(
            device_fingerprint,
            max_requests=10,  # 10 consultas por dispositivo cada 5 minutos
            window_minutes=5
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many requests. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        # Incrementar contador
        increment_rate_limit_counter('device_fp', device_fingerprint)
        
        if not subscriber_code:
            logger.warning(
                f"SNUsageStatsView: subscriber_code faltante - ip={client_ip}"
            )
            return Response({"error": "Parámetro 'subscriber_code' requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Obtener todas las smartcards del subscriber
        all_smartcards = SubscriberInfo.objects.filter(
            subscriber_code=subscriber_code
        ).exclude(
            products__isnull=True
        ).exclude(
            products=[]
        ).values('sn', 'products', 'model')
        
        # Obtener UDIDs activos para este subscriber
        active_udids = UDIDAuthRequest.objects.filter(
            subscriber_code=subscriber_code,
            status__in=['validated', 'used'],
            expires_at__gte=timezone.now(),
            sn__isnull=False
        ).values('udid', 'sn', 'status', 'app_type', 'created_at', 'validated_at', 'used_at')
        
        # Crear mapeo de SNs en uso (cada SN solo puede tener un app_type activo)
        sns_in_use = {}
        for udid in active_udids:
            sn = udid['sn']
            # Si ya existe esta SN, es un error de lógica (no debería pasar)
            if sn in sns_in_use:
                logger.warning(
                    "SNUsageStatsView: SN %s usado por múltiples app_types existente=%s nuevo=%s",
                    sn,
                    sns_in_use[sn].get("app_type"),
                    udid.get("app_type"),
                )
            sns_in_use[sn] = udid
        
        # Preparar respuesta detallada
        smartcards_status = []
        available_count = 0
        
        for smartcard in all_smartcards:
            sn = smartcard['sn']
            usage_info = sns_in_use.get(sn)
            is_available = usage_info is None
            
            if is_available:
                available_count += 1
            
            status_info = {
                "sn": sn,
                "products": smartcard['products'],
                "model": smartcard['model'],
                "is_available": is_available,
                "current_usage": usage_info if usage_info else None,
                "status": "available" if is_available else f"in_use_by_{usage_info['app_type']}"
            }
            smartcards_status.append(status_info)
        
        # Estadísticas por tipo de app
        app_type_stats = {}
        for udid in active_udids:
            app_type = udid['app_type']
            if app_type not in app_type_stats:
                app_type_stats[app_type] = 0
            app_type_stats[app_type] += 1
        
        return Response({
            "subscriber_code": subscriber_code,
            "total_smartcards": len(smartcards_status),
            "smartcards_in_use": len(sns_in_use),
            "available_smartcards": available_count,
            "usage_by_app_type": app_type_stats,
            "smartcards": smartcards_status,
            "policy": "Each smartcard can only be active on one app type at a time",
            "rate_limit": {
                "remaining": remaining - 1,
                "reset_in_seconds": 5 * 60
            }
        }, status=status.HTTP_200_OK)

def validate_device_sn_association(udid, device_fingerprint=None, client_ip=None):
    """
    Valida si un dispositivo tiene una SN asociada y está autorizado para continuar
    
    Args:
        udid (str): UDID del dispositivo
        device_fingerprint (str, optional): Huella digital del dispositivo
        client_ip (str, optional): IP del cliente
    
    Returns:
        dict: Resultado de la validación con status y detalles
    """
    try:
        # Buscar el UDID en la base de datos
        udid_request = UDIDAuthRequest.objects.get(udid=udid)
        
        # Verificar estados básicos
        if udid_request.status not in ['validated', 'used']:
            return {
                'valid': False,
                'error': 'DEVICE_NOT_VALIDATED',
                'message': 'El dispositivo no ha sido validado correctamente',
                'details': {
                    'current_status': udid_request.status,
                    'required_status': ['validated', 'used']
                }
            }
        
        # Verificar si el token ha expirado
        if udid_request.is_expired():
            udid_request.status = 'expired'
            udid_request.save()
            return {
                'valid': False,
                'error': 'TOKEN_EXPIRED',
                'message': 'El token de autenticación ha expirado',
                'details': {
                    'expired_at': udid_request.expires_at,
                    'current_time': timezone.now()
                }
            }
        
        # ✅ VALIDACIÓN PRINCIPAL: Verificar asociación con SN
        if not udid_request.sn:
            return {
                'valid': False,
                'error': 'NO_SN_ASSOCIATED',
                'message': 'Este dispositivo no tiene una smartcard (SN) asociada',
                'details': {
                    'udid': udid,
                    'subscriber_code': udid_request.subscriber_code,
                    'app_type': udid_request.app_type,
                    'action_required': 'El dispositivo debe solicitar credenciales primero'
                }
            }
        
        # Verificar que la SN sigue siendo válida
        try:
            subscriber_info = SubscriberInfo.objects.get(
                subscriber_code=udid_request.subscriber_code,
                sn=udid_request.sn
            )
            
            # Verificar que tenga productos
            if not subscriber_info.products or subscriber_info.products == []:
                return {
                    'valid': False,
                    'error': 'SN_NO_PRODUCTS',
                    'message': 'La smartcard asociada no tiene productos activos',
                    'details': {
                        'sn': udid_request.sn,
                        'subscriber_code': udid_request.subscriber_code
                    }
                }
                
        except SubscriberInfo.DoesNotExist:
            return {
                'valid': False,
                'error': 'SN_NOT_FOUND',
                'message': 'La smartcard asociada ya no existe en el sistema',
                'details': {
                    'sn': udid_request.sn,
                    'subscriber_code': udid_request.subscriber_code
                }
            }
        
        # Verificar que no haya conflictos con otros dispositivos
        conflicting_udids = UDIDAuthRequest.objects.filter(
            sn=udid_request.sn,
            status__in=['validated', 'used'],
            expires_at__gte=timezone.now()
        ).exclude(udid=udid)
        
        if conflicting_udids.exists():
            return {
                'valid': False,
                'error': 'SN_CONFLICT',
                'message': 'La smartcard está siendo utilizada por otro dispositivo',
                'details': {
                    'sn': udid_request.sn,
                    'conflicting_devices': conflicting_udids.count(),
                    'app_types_in_conflict': list(conflicting_udids.values_list('app_type', flat=True))
                }
            }
        
        # Validación adicional de device fingerprint si se proporciona
        if device_fingerprint and udid_request.device_fingerprint:
            if device_fingerprint != udid_request.device_fingerprint:
                return {
                    'valid': False,
                    'error': 'DEVICE_FINGERPRINT_MISMATCH',
                    'message': 'El dispositivo no coincide con el registrado',
                    'details': {
                        'expected_fingerprint': udid_request.device_fingerprint[:10] + '...',
                        'received_fingerprint': device_fingerprint[:10] + '...'
                    }
                }
        
        # ✅ VALIDACIÓN EXITOSA
        return {
            'valid': True,
            'message': 'Dispositivo validado correctamente',
            'details': {
                'udid': udid,
                'sn': udid_request.sn,
                'subscriber_code': udid_request.subscriber_code,
                'app_type': udid_request.app_type,
                'validation_time': timezone.now()
            }
        }
        
    except UDIDAuthRequest.DoesNotExist:
        return {
            'valid': False,
            'error': 'UDID_NOT_FOUND',
            'message': 'El dispositivo no está registrado en el sistema',
            'details': {
                'udid': udid,
                'action_required': 'El dispositivo debe solicitar un nuevo UDID'
            }
        }
    except Exception as e:
        return {
            'valid': False,
            'error': 'VALIDATION_ERROR',
            'message': f'Error durante la validación: {str(e)}',
            'details': {
                'udid': udid,
                'exception': str(e)
            }
        }

class ValidateDeviceAssociationView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        """
        Endpoint para validar si un dispositivo tiene SN asociada
        """
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        udid = request.data.get('udid')
        device_fingerprint = request.data.get('device_fingerprint')
        
        logger.info(
            f"ValidateDeviceAssociationView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"device_fingerprint={'presente' if device_fingerprint else 'faltante'}, ip={client_ip}"
        )
        
        if not udid:
            logger.warning(
                f"ValidateDeviceAssociationView: UDID faltante - ip={client_ip}"
            )
            return Response({
                "error": "Parámetro 'udid' requerido"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Rate limiting por UDID
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid, max_requests=10, window_minutes=5
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many validation attempts for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        # Incrementar contador
        increment_rate_limit_counter('udid', udid)
        
        # Obtener información del cliente
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        # Ejecutar validación
        validation_result = validate_device_sn_association(
            udid=udid,
            device_fingerprint=device_fingerprint,
            client_ip=client_ip
        )
        
        # Log de auditoría (asíncrono)
        log_audit_async(
            action_type='device_validation',
            udid=udid,
            client_ip=client_ip,
            user_agent=user_agent,
            details={
                'validation_result': validation_result['valid'],
                'error_code': validation_result.get('error'),
                'message': validation_result['message']
            }
        )
        
        if validation_result['valid']:
            return Response({
                "status": "success",
                "message": validation_result['message'],
                "device_info": validation_result['details']
            }, status=status.HTTP_200_OK)
        else:
            # Determinar el código de estado HTTP apropiado
            error_code = validation_result.get('error')
            if error_code in ['UDID_NOT_FOUND', 'SN_NOT_FOUND']:
                http_status = status.HTTP_404_NOT_FOUND
            elif error_code in ['DEVICE_NOT_VALIDATED', 'TOKEN_EXPIRED']:
                http_status = status.HTTP_401_UNAUTHORIZED
            elif error_code in ['SN_CONFLICT', 'DEVICE_FINGERPRINT_MISMATCH']:
                http_status = status.HTTP_409_CONFLICT
            elif error_code in ['NO_SN_ASSOCIATED', 'SN_NO_PRODUCTS']:
                http_status = status.HTTP_403_FORBIDDEN
            else:
                http_status = status.HTTP_400_BAD_REQUEST
            
            return Response({
                "status": "error",
                "error_code": error_code,
                "message": validation_result['message'],
                "details": validation_result['details'],
                "can_continue": False
            }, status=http_status)

class OperatorRevokeUDIDView(APIView):
    """
    Vista exclusiva para operadores: revoca directamente un UDID y lo desasocia de su SN
    """
    permission_classes = [AllowAny]

    def post(self, request):
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        udid = request.data.get("udid")
        operator_id = request.data.get("operator_id", "manual")
        reason = request.data.get("reason", "Revocación operativa")

        logger.info(
            f"OperatorRevokeUDIDView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"operator_id={operator_id}, reason={reason}, ip={client_ip}"
        )

        if not udid:
            logger.warning(
                f"OperatorRevokeUDIDView: UDID faltante - ip={client_ip}"
            )
            return Response({"error": "El parámetro 'udid' es requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Rate limiting por UDID (previene revocaciones masivas)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid, max_requests=3, window_minutes=60
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many revocation attempts for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        # Incrementar contador
        increment_rate_limit_counter('udid', udid)

        try:
            req = UDIDAuthRequest.objects.get(udid=udid)
        except UDIDAuthRequest.DoesNotExist:
            return Response({"error": "UDID no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        if req.status in ['revoked', 'expired']:
            return Response({
                "message": f"El UDID ya está revocado o expirado. Estado actual: {req.status}",
                "udid": udid
            }, status=status.HTTP_200_OK)

        original_sn = req.sn
        req.status = 'revoked'
        req.sn = None
        req.validated_by_operator = operator_id
        req.save()

        # Log de auditoría (asíncrono)
        log_audit_async(
            action_type='account_locked',
            subscriber_code=req.subscriber_code,
            udid=req.udid,
            operator_id=operator_id,
            details={
                "action": "operator_revoked",
                "sn": original_sn,
                "reason": reason
            },
            client_ip=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", "")
        )

        return Response({
            "message": "UDID revocado exitosamente.",
            "udid": udid,
            "sn_released": original_sn
        }, status=status.HTTP_200_OK)

class UserReleaseUDIDView(APIView):
    """
    Vista para usuarios finales: permite liberar un dispositivo propio con confirmación
    """
    permission_classes = [AllowAny]

    def post(self, request):
        client_ip = request.META.get('REMOTE_ADDR')
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        udid = request.data.get("udid")
        confirm = request.data.get("confirm", False)
        reason = request.data.get("reason", "Liberación solicitada por el usuario")
        operator_id = "user_request"

        logger.info(
            f"UserReleaseUDIDView: Request recibido - "
            f"udid={udid[:8] if udid and len(udid) > 8 else udid}..., "
            f"confirm={confirm}, reason={reason}, ip={client_ip}"
        )

        if not udid:
            logger.warning(
                f"UserReleaseUDIDView: UDID faltante - ip={client_ip}"
            )
            return Response({"error": "El parámetro 'udid' es requerido."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Rate limiting por UDID (previene liberaciones masivas)
        is_allowed, remaining, retry_after = check_udid_rate_limit(
            udid, max_requests=3, window_minutes=60
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many release attempts for this UDID. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })
        
        # Incrementar contador solo si no es confirmación
        if not confirm:
            increment_rate_limit_counter('udid', udid)

        try:
            req = UDIDAuthRequest.objects.get(udid=udid)
        except UDIDAuthRequest.DoesNotExist:
            return Response({"error": "UDID no encontrado."}, status=status.HTTP_404_NOT_FOUND)

        if req.status in ['revoked', 'expired']:
            return Response({
                "message": f"Este dispositivo ya no está activo.",
                "udid": udid,
                "status": req.status
            }, status=status.HTTP_200_OK)

        if not confirm:
            return Response({
                "message": "Confirmación requerida para liberar el dispositivo.",
                "confirmation_required": True,
                "device_info": {
                    "subscriber_code": req.subscriber_code,
                    "sn": req.sn,
                    "app_type": req.app_type,
                    "app_version": req.app_version,
                    "validated_at": req.validated_at,
                    "used_at": req.used_at,
                },
                "instructions": "Envía de nuevo con 'confirm': true para completar la liberación."
            }, status=status.HTTP_200_OK)

        original_sn = req.sn
        req.status = 'revoked'
        req.sn = None
        req.validated_by_operator = operator_id
        req.save()

        # Log de auditoría (asíncrono)
        log_audit_async(
            action_type='account_locked',
            subscriber_code=req.subscriber_code,
            udid=req.udid,
            operator_id=operator_id,
            details={
                "action": "user_release",
                "sn": original_sn,
                "reason": reason
            },
            client_ip=request.META.get("REMOTE_ADDR"),
            user_agent=request.META.get("HTTP_USER_AGENT", "")
        )

        return Response({
            "message": "Dispositivo liberado exitosamente.",
            "udid": udid,
            "sn_released": original_sn
        }, status=status.HTTP_200_OK)