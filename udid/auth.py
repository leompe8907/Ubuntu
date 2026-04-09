# views.py
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken

from django.db.utils import IntegrityError

from udid.api_errors import handle_view_exception
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.contrib.auth.hashers import make_password

from udid.models import UserProfile
from udid.util import (
    generate_device_fingerprint,
    check_login_rate_limit,
    increment_login_attempt,
    reset_login_attempts,
    check_register_rate_limit,
    increment_register_attempt,
    check_adaptive_rate_limit,
    get_system_load,
    check_circuit_breaker,
    get_client_ip,
)
import logging

logger = logging.getLogger(__name__)

class RegisterUserView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        client_ip = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        
        data = request.data
        username = data.get('username')
        password = data.get('password')
        first_name = data.get('first_name')
        last_name = data.get('last_name')
        email = data.get('email')
        operador = data.get('operador')
        documento = data.get('documento')
        
        logger.info(
            f"RegisterUserView: Request recibido - "
            f"username={username}, email={email}, operador={operador}, ip={client_ip}"
        )

        # Verificar circuit breaker antes de procesar
        breaker_active, breaker_retry_after = check_circuit_breaker()
        if breaker_active:
            return Response({
                "error": "Service temporarily unavailable",
                "message": "System is under high load. Please try again later.",
                "retry_after": breaker_retry_after
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(breaker_retry_after)
            })

        # Rate limiting por device fingerprint
        device_fingerprint = generate_device_fingerprint(request)
        
        # Usar rate limiting adaptativo si la carga del sistema es alta
        system_load = get_system_load()
        if system_load in ['high', 'critical']:
            # Durante alta carga, usar rate limiting adaptativo más restrictivo
            is_allowed, remaining, retry_after, reason = check_adaptive_rate_limit(
                'device_fp', device_fingerprint, is_reconnection=False,
                base_max_requests=2, base_window_minutes=60
            )
        else:
            # Carga normal: usar rate limiting estándar
            is_allowed, remaining, retry_after = check_register_rate_limit(
                device_fingerprint, max_requests=3, window_minutes=60
            )
            reason = None
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many registration attempts from this device. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining,
                "system_load": system_load,
                "reason": reason
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        # ... (Validaciones de campos requeridos y de duplicados de User) ...
        missing_fields = []
        if not username: missing_fields.append('username')
        if not password: missing_fields.append('password')
        if not first_name: missing_fields.append('first_name')
        if not last_name: missing_fields.append('last_name')
        if not email: missing_fields.append('email')
        if not operador: missing_fields.append('operador')
        if not documento: missing_fields.append('documento')

        if missing_fields:
            # Incrementar contador aunque falle la validación (previene abuso)
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({
                "error": f"Faltan campos requeridos: {', '.join(missing_fields)}"
            }, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(username=username).exists():
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({"error": "El nombre de usuario ya existe."}, status=status.HTTP_400_BAD_REQUEST)
        if User.objects.filter(email=email).exists():
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({"error": "El correo electrónico ya está registrado."}, status=status.HTTP_400_BAD_REQUEST)

        # **ATENCIÓN**: Si `document_number` debería ser único,
        # DEBES añadir `unique=True` en tu modelo UserProfile.
        # De lo contrario, esta validación solo previene duplicados en la misma ejecución
        # pero la DB los permitirá si se inserta desde otro lado o si se remueve esta validación.
        if UserProfile.objects.filter(document_number=documento).exists():
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({"error": "Este documento ya está registrado."}, status=status.HTTP_400_BAD_REQUEST)

        # ✅ Crear usuario y actualizar perfil
        try:
            # Crear el usuario
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                is_active=True,
                is_staff=False # Asegúrate de que esto sea lo que quieres
            )

            # 🟢 CAMBIO CLAVE: Acceder al perfil creado automáticamente por el signal
            # y actualizarlo con los datos adicionales.
            # El signal post_save ya creó el UserProfile.
            user_profile = user.userprofile 
            user_profile.operator_code = operador
            user_profile.document_number = documento
            user_profile.save() # Guardar los cambios en el perfil

            # Incrementar contador de registro exitoso
            increment_register_attempt(device_fingerprint, window_minutes=60)
            
            logger.info(
                f"RegisterUserView: Usuario registrado exitosamente - "
                f"username={username}, user_id={user.id}, email={email}, "
                f"device_fingerprint={device_fingerprint[:8]}..., ip={client_ip}"
            )
            
            # Si todo sale bien, devuelve una respuesta de éxito 201 Created
            return Response({
                "message": "Usuario registrado exitosamente.",
                "user_id": user.id,
                "username": user.username,
                "rate_limit": {
                    "remaining": remaining - 1,
                    "reset_in_seconds": 60 * 60
                }
            }, status=status.HTTP_201_CREATED)

        except IntegrityError:
            logger.warning(
                f"RegisterUserView: Error de integridad - "
                f"username={username}, email={email}, ip={client_ip}",
                exc_info=True,
            )
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response(
                {
                    "error": "No se pudo completar el registro por conflicto de datos. Intente de nuevo.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ValidationError as e:
            logger.warning(
                f"RegisterUserView: Error de validación - "
                f"username={username}, ip={client_ip}, errors={e.message_dict}"
            )
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({
                "error": "Error de validación de datos del perfil.",
                "details": e.message_dict
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return handle_view_exception(
                f"RegisterUserView username={username!r}",
                e,
            )
class LoginView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')
        client_ip = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')

        logger.info(
            f"LoginView: Request recibido - "
            f"username={username}, ip={client_ip}"
        )

        if not all([username, password]):
            logger.warning(
                f"LoginView: Credenciales faltantes - "
                f"username={'presente' if username else 'faltante'}, "
                f"password={'presente' if password else 'faltante'}, ip={client_ip}"
            )
            return Response({"error": "username y password son requeridos"}, status=400)

        # Verificar circuit breaker antes de procesar
        breaker_active, breaker_retry_after = check_circuit_breaker()
        if breaker_active:
            return Response({
                "error": "Service temporarily unavailable",
                "message": "System is under high load. Please try again later.",
                "retry_after": breaker_retry_after
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(breaker_retry_after)
            })

        # Rate limiting por username + device fingerprint
        device_fingerprint = generate_device_fingerprint(request)
        
        # Usar rate limiting adaptativo si la carga del sistema es alta
        system_load = get_system_load()
        if system_load in ['high', 'critical']:
            # Durante alta carga, usar rate limiting adaptativo más restrictivo
            is_allowed, remaining, retry_after, reason = check_adaptive_rate_limit(
                'device_fp', device_fingerprint, is_reconnection=False,
                base_max_requests=3, base_window_minutes=15
            )
        else:
            # Carga normal: usar rate limiting estándar
            is_allowed, remaining, retry_after = check_login_rate_limit(
                username, device_fingerprint, max_attempts=5, window_minutes=15
            )
            reason = None
        
        if not is_allowed:
            logger.warning(
                f"LoginView: Rate limit excedido - "
                f"username={username}, device_fingerprint={device_fingerprint[:8]}..., "
                f"system_load={system_load}, ip={client_ip}, retry_after={retry_after}s"
            )
            return Response({
                "error": "Too many login attempts",
                "message": "Please try again later",
                "retry_after": retry_after,
                "remaining_attempts": remaining,
                "system_load": system_load,
                "reason": reason
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        user = authenticate(username=username, password=password)

        if user is None:
            # Incrementar contador de intentos fallidos
            increment_login_attempt(username, device_fingerprint, window_minutes=15)
            
            logger.warning(
                f"LoginView: Credenciales inválidas - "
                f"username={username}, device_fingerprint={device_fingerprint[:8]}..., "
                f"remaining_attempts={remaining - 1}, ip={client_ip}"
            )
            
            return Response({
                "error": "Credenciales inválidas",
                "remaining_attempts": remaining - 1
            }, status=status.HTTP_401_UNAUTHORIZED)

        # Login exitoso: resetear contador de intentos
        reset_login_attempts(username, device_fingerprint)

        refresh = RefreshToken.for_user(user)

        # Obtener operador si existe
        try:
            operator_code = user.userprofile.operator_code
        except UserProfile.DoesNotExist:
            operator_code = None
        except AttributeError:
            operator_code = None

        logger.info(
            f"LoginView: Login exitoso - "
            f"username={username}, user_id={user.id}, operator_code={operator_code}, "
            f"device_fingerprint={device_fingerprint[:8]}..., ip={client_ip}"
        )

        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'username': user.username,
            'email': user.email,
            'operator_code': operator_code,
            'rate_limit': {
                'remaining_attempts': 5,  # Reseteado después de login exitoso
                'reset_in_seconds': 0
            }
        }, status=status.HTTP_200_OK)
