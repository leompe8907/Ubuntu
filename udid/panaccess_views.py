"""
Vistas para la autenticación y gestión de Panaccess.

Endpoints para realizar login, verificar sesión y consultar el estado del singleton.
Útiles para testing, debugging y monitoreo de la conexión con Panaccess.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from .utils.panaccess import (
    PanaccessClient,
    logged_in,
    get_panaccess,
)
from .utils.panaccess.exceptions import (
    PanaccessAuthenticationError,
    PanaccessConnectionError,
    PanaccessTimeoutError,
    PanaccessAPIError,
    PanaccessException
)


@api_view(['GET'])
@permission_classes([AllowAny])
def panaccess_login(request):
    """
    Vista para la autenticación con Panaccess.
    
    Realiza el login usando el cliente y retorna el sessionId obtenido.
    Útil para testing y verificación de credenciales.
    
    Returns:
        Response con sessionId y estado de autenticación
    """
    client = PanaccessClient()
    
    try:
        session_id = client.authenticate()
        
        return Response({
            'success': True,
            'message': 'Login exitoso',
            'session_id': session_id,
            'session_id_length': len(session_id) if session_id else 0,
            'is_authenticated': client.is_authenticated()
        }, status=status.HTTP_200_OK)
        
    except PanaccessAuthenticationError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessAuthenticationError',
            'message': str(e)
        }, status=status.HTTP_401_UNAUTHORIZED)
        
    except PanaccessConnectionError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessConnectionError',
            'message': str(e)
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
    except PanaccessTimeoutError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessTimeoutError',
            'message': str(e)
        }, status=status.HTTP_504_GATEWAY_TIMEOUT)
        
    except PanaccessAPIError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessAPIError',
            'message': str(e),
            'status_code': e.status_code
        }, status=status.HTTP_502_BAD_GATEWAY)
        
    except PanaccessException as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessException',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': f'Error inesperado: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([AllowAny])
def panaccess_logged_in(request):
    """
    Vista para validar si un sessionId está vigente.
    
    Prueba la función logged_in() y el método check_session() del cliente.
    Útil para verificar el estado de una sesión existente.
    
    Returns:
        Response con el estado de validez de la sesión
    """
    client = PanaccessClient()
    
    try:
        # Primero autenticarse para obtener un sessionId
        session_id = client.authenticate()
        
        # Verificar si la sesión es válida usando la función directa
        is_valid_direct = logged_in(session_id)
        
        # Verificar usando el método del cliente
        is_valid_client = client.check_session()
        
        return Response({
            'success': True,
            'message': 'Verificación de sesión exitosa',
            'session_id': session_id,
            'is_valid_direct': is_valid_direct,
            'is_valid_client': is_valid_client,
            'both_match': is_valid_direct == is_valid_client
        }, status=status.HTTP_200_OK)
        
    except PanaccessAuthenticationError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessAuthenticationError',
            'message': str(e)
        }, status=status.HTTP_401_UNAUTHORIZED)
        
    except PanaccessConnectionError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessConnectionError',
            'message': str(e)
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        
    except PanaccessTimeoutError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessTimeoutError',
            'message': str(e)
        }, status=status.HTTP_504_GATEWAY_TIMEOUT)
        
    except PanaccessAPIError as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessAPIError',
            'message': str(e),
            'status_code': e.status_code
        }, status=status.HTTP_502_BAD_GATEWAY)
        
    except PanaccessException as e:
        return Response({
            'success': False,
            'error_type': 'PanaccessException',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': f'Error inesperado: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([AllowAny])
def panaccess_singleton(request):
    """
    Vista para el singleton de Panaccess.
    
    Demuestra cómo usar el singleton que se inicializa al arrancar Django.
    Muestra el estado actual de la sesión y permite hacer una llamada de prueba.
    
    Returns:
        Response con el estado del singleton y resultado de prueba
    """
    try:
        # Obtener el singleton (se inicializa automáticamente si no existe)
        panaccess = get_panaccess()
        
        # Verificar si hay sesión activa
        has_session = panaccess.client.is_authenticated()
        
        # Si hay sesión, hacer una llamada de prueba para verificar que funciona
        result = None
        if has_session:
            try:
                # Llamada de prueba usando el singleton
                result = panaccess.call("cvLoggedIn", {
                    "sessionId": panaccess.client.session_id
                })
            except Exception as e:
                result = {'error': str(e)}
        
        return Response({
            'success': True,
            'message': 'Singleton funcionando correctamente',
            'has_session': has_session,
            'session_id': panaccess.client.session_id[:20] + '...' if panaccess.client.session_id and len(panaccess.client.session_id) > 20 else panaccess.client.session_id,
            'session_id_length': len(panaccess.client.session_id) if panaccess.client.session_id else 0,
            'result': result,
            'validation_thread_running': panaccess._validation_thread is not None and panaccess._validation_thread.is_alive() if hasattr(panaccess, '_validation_thread') else False
        }, status=status.HTTP_200_OK)
        
    except PanaccessException as e:
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    except Exception as e:
        return Response({
            'success': False,
            'error_type': 'Exception',
            'message': f'Error inesperado: {str(e)}'
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

