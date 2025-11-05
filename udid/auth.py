# views.py
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken

from django.db.utils import IntegrityError
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
)

class RegisterUserView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        data = request.data
        username = data.get('username')
        password = data.get('password')
        first_name = data.get('first_name')
        last_name = data.get('last_name')
        email = data.get('email')
        operador = data.get('operador')
        documento = data.get('documento')

        # Rate limiting por device fingerprint
        device_fingerprint = generate_device_fingerprint(request)
        is_allowed, remaining, retry_after = check_register_rate_limit(
            device_fingerprint, max_requests=3, window_minutes=60
        )
        
        if not is_allowed:
            return Response({
                "error": "Rate limit exceeded",
                "message": "Too many registration attempts from this device. Please try again later.",
                "retry_after": retry_after,
                "remaining_requests": remaining
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

        except IntegrityError as e:
            # Si una IntegrityError ocurre aquí, probablemente sea por algo más,
            # pero con esta corrección ya no debería ser por el OneToOneField.
            # Podría ser si document_number fuera unique=True y se intentara un duplicado.
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({
                "error": f"Error de integridad en la base de datos: {str(e)}. El usuario pudo haberse creado pero el perfil no se completó."
            }, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as e:
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({
                "error": "Error de validación de datos del perfil.",
                "details": e.message_dict
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # Si ocurre un error aquí, es un problema del servidor.
            # Es importante que si el usuario se creó pero el perfil no,
            # sepas que ocurrió.
            increment_register_attempt(device_fingerprint, window_minutes=60)
            return Response({
                "error": "Error inesperado al registrar el usuario.",
                "details": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
class LoginView(APIView):
    permission_classes = [AllowAny]
    
    def post(self, request):
        username = request.data.get('username')
        password = request.data.get('password')

        if not all([username, password]):
            return Response({"error": "username y password son requeridos"}, status=400)

        # Rate limiting por username + device fingerprint
        device_fingerprint = generate_device_fingerprint(request)
        is_allowed, remaining, retry_after = check_login_rate_limit(
            username, device_fingerprint, max_attempts=5, window_minutes=15
        )
        
        if not is_allowed:
            return Response({
                "error": "Too many login attempts",
                "message": "Please try again later",
                "retry_after": retry_after,
                "remaining_attempts": remaining
            }, status=status.HTTP_429_TOO_MANY_REQUESTS, headers={
                "Retry-After": str(retry_after)
            })

        user = authenticate(username=username, password=password)

        if user is None:
            # Incrementar contador de intentos fallidos
            increment_login_attempt(username, device_fingerprint, window_minutes=15)
            
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
        except:
            operator_code = None

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
