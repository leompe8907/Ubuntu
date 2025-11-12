"""
Middleware para rastrear carga del sistema y aplicar protección DDoS.
"""
import time
from django.utils.deprecation import MiddlewareMixin
from django.http import JsonResponse
from .util import (
    track_system_request,
    acquire_global_semaphore,
    release_global_semaphore,
    check_plan_rate_limit
)
from .utils.metrics import record_request_latency, record_error, get_metrics
from .models import APIKey
from .utils.request_queue import get_request_queue
from .utils.degradation import get_degradation_manager, should_degrade
import uuid


class SystemLoadTrackingMiddleware(MiddlewareMixin):
    """
    Middleware que rastrea cada request para monitoreo de carga del sistema.
    Esto permite que el rate limiting adaptativo funcione correctamente.
    También registra métricas de latencia y errores.
    """
    
    def process_request(self, request):
        """
        Rastrea cada request para calcular la carga del sistema.
        """
        # Solo rastrear requests a endpoints de la API
        if request.path.startswith('/udid/') or request.path.startswith('/auth/'):
            track_system_request()
            # Registrar tiempo de inicio para calcular latencia
            request._start_time = time.time()
        
        return None
    
    def process_response(self, request, response):
        """
        Registra latencia y errores después de procesar el request.
        """
        if hasattr(request, '_start_time'):
            # Calcular latencia en milisegundos
            latency_ms = (time.time() - request._start_time) * 1000
            record_request_latency(latency_ms)
            
            # Registrar errores
            if response.status_code >= 400:
                record_error(response.status_code)
        
        return response


class GlobalConcurrencyMiddleware(MiddlewareMixin):
    """
    Middleware que aplica semáforo global de concurrencia.
    Rechaza requests con 503 cuando se supera el límite de slots simultáneos.
    
    El semáforo limita la concurrencia total del sistema para evitar saturación
    y proteger recursos críticos como la base de datos.
    """
    
    def process_request(self, request):
        """
        Intenta adquirir un slot en el semáforo global antes de procesar el request.
        """
        # Solo aplicar a endpoints de API
        if not (request.path.startswith('/udid/') or 
                request.path.startswith('/auth/')):
            return None
        
        # Timeout se calcula dinámicamente basado en p95
        acquired, slot_id, retry_after = acquire_global_semaphore(
            timeout=None,  # Se calcula dinámicamente
            max_slots=None  # Usa configuración de settings
        )
        
        if not acquired:
            # Retornar respuesta 503 con Retry-After
            return JsonResponse({
                "error": "Service temporarily unavailable",
                "message": "System is handling high load. Please retry after the specified time.",
                "retry_after": retry_after
            }, status=503, headers={"Retry-After": str(retry_after)})
        
        # Almacenar slot_id en request para liberarlo después
        request._semaphore_slot_id = slot_id
        return None
    
    def process_response(self, request, response):
        """
        Libera el slot del semáforo después de procesar el request.
        """
        # Liberar slot después de procesar request
        if hasattr(request, '_semaphore_slot_id') and request._semaphore_slot_id:
            release_global_semaphore(request._semaphore_slot_id)
        return response
    
    def process_exception(self, request, exception):
        """
        Asegura que el slot se libere incluso si hay una excepción.
        """
        if hasattr(request, '_semaphore_slot_id') and request._semaphore_slot_id:
            release_global_semaphore(request._semaphore_slot_id)
        return None


class APIKeyAuthMiddleware(MiddlewareMixin):
    """
    Middleware que autentica requests por API key y aplica cuotas del plan.
    
    La API key se debe enviar en:
    - Header: X-API-Key
    
    NOTA: El header Authorization: Bearer está reservado para JWT tokens
    y NO debe usarse para API keys para evitar conflictos.
    
    Si no se proporciona API key, el request continúa normalmente (no es obligatorio).
    Si se proporciona, se valida y se aplican las cuotas del plan asociado.
    """
    
    def process_request(self, request):
        """
        Autentica request por API key y aplica cuotas del plan.
        """
        # Solo aplicar a endpoints de API
        if not (request.path.startswith('/udid/') or 
                request.path.startswith('/auth/')):
            return None
        
        # Obtener API key SOLO del header X-API-Key
        # NO usar Authorization: Bearer para API keys (está reservado para JWT)
        api_key = request.META.get('HTTP_X_API_KEY')
        
        # Si no hay API key, continuar sin autenticación (opcional)
        if not api_key:
            return None
        
        # Buscar API key en BD
        try:
            api_key_obj = APIKey.find_by_key(api_key)
            
            if not api_key_obj:
                return JsonResponse({
                    "error": "Invalid API key",
                    "message": "The provided API key is not valid or not found."
                }, status=401)
            
            # Verificar si la API key es válida (activa y no expirada)
            if not api_key_obj.is_valid():
                return JsonResponse({
                    "error": "API key expired or inactive",
                    "message": "The API key has expired or has been deactivated."
                }, status=401)
            
            # Verificar que el tenant esté activo
            if not api_key_obj.tenant.is_active:
                return JsonResponse({
                    "error": "Tenant inactive",
                    "message": "The tenant associated with this API key is inactive."
                }, status=403)
            
            # Verificar que el plan esté activo
            if not api_key_obj.plan.is_active:
                return JsonResponse({
                    "error": "Plan inactive",
                    "message": "The plan associated with this API key is inactive."
                }, status=403)
            
            # Aplicar cuotas del plan (rate limiting por minuto)
            is_allowed, remaining, retry_after = check_plan_rate_limit(
                tenant_id=api_key_obj.tenant_id,
                plan=api_key_obj.plan,
                window='minute'
            )
            
            if not is_allowed:
                return JsonResponse({
                    "error": "Rate limit exceeded",
                    "message": f"Plan limit exceeded. Maximum {api_key_obj.plan.max_requests_per_minute} requests per minute.",
                    "retry_after": retry_after,
                    "remaining": remaining,
                    "limit": api_key_obj.plan.max_requests_per_minute
                }, status=429, headers={"Retry-After": str(retry_after)})
            
            # Marcar API key como usada (actualizar last_used_at)
            api_key_obj.mark_as_used()
            
            # Almacenar en request para uso posterior
            request.api_key = api_key_obj
            request.tenant = api_key_obj.tenant
            request.plan = api_key_obj.plan
            
            return None
            
        except Exception as e:
            # Log error pero no bloquear (fail-open)
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error in APIKeyAuthMiddleware: {e}", exc_info=True)
            # Continuar sin autenticación en caso de error
            return None


class BackpressureMiddleware(MiddlewareMixin):
    """
    Middleware que implementa backpressure multicapa.
    
    Características:
    - Cola de entrada para requests cuando el sistema está bajo carga
    - Degradación elegante basada en métricas
    - Rechazo de requests de baja prioridad en situaciones críticas
    """
    
    def process_request(self, request):
        """
        Aplica backpressure antes de procesar el request.
        """
        # Solo aplicar a endpoints de API
        if not (request.path.startswith('/udid/') or 
                request.path.startswith('/auth/')):
            return None
        
        # Obtener métricas actuales
        try:
            metrics = get_metrics()
            current_concurrency = metrics.get('concurrency_current', 0)
            latency_p95 = metrics.get('p95_ms', 0)
            cpu_percent = metrics.get('cpu_percent', 0)
            
            # Calcular tasa de errores
            total_requests = metrics.get('total_requests', 1)
            error_429 = metrics.get('errors', {}).get('429', 0)
            error_503 = metrics.get('errors', {}).get('503', 0)
            error_500 = metrics.get('errors', {}).get('500', 0)
            total_errors = error_429 + error_503 + error_500
            error_rate = total_errors / total_requests if total_requests > 0 else 0
            
            # Determinar nivel de degradación
            degradation_manager = get_degradation_manager()
            degradation_level = degradation_manager.should_degrade(
                current_load=current_concurrency,
                latency_p95=latency_p95,
                error_rate=error_rate,
                cpu_percent=cpu_percent
            )
            
            # Obtener prioridad del request (si está disponible)
            request_priority = getattr(request, 'priority', 0)
            
            # Si es crítico y el request es de baja prioridad, rechazarlo
            if degradation_manager.should_reject_low_priority_requests(degradation_level):
                if request_priority < 0:  # Prioridad negativa = baja prioridad
                    response_data, status_code = degradation_manager.get_degraded_response(degradation_level)
                    return JsonResponse(
                        response_data,
                        status=status_code,
                        headers={"Retry-After": str(response_data.get('retry_after', 60))}
                    )
            
            # Si hay degradación, intentar encolar el request
            if degradation_level in ('high', 'critical'):
                request_queue = get_request_queue()
                request_id = str(uuid.uuid4())
                
                # Intentar encolar
                success, queue_position, estimated_wait = request_queue.enqueue(
                    request_id=request_id,
                    priority=request_priority
                )
                
                if not success:
                    # Cola llena, rechazar request
                    response_data, status_code = degradation_manager.get_degraded_response('critical')
                    return JsonResponse(
                        response_data,
                        status=status_code,
                        headers={"Retry-After": str(response_data.get('retry_after', 60))}
                    )
                
                # Si está en cola, esperar o rechazar según timeout
                if queue_position > 0:
                    # Para simplicidad, rechazamos si hay espera significativa
                    # En producción, se podría implementar espera real
                    if estimated_wait > 5:  # Más de 5 segundos de espera
                        response_data, status_code = degradation_manager.get_degraded_response(degradation_level)
                        return JsonResponse(
                            {
                                **response_data,
                                'queue_position': queue_position,
                                'estimated_wait': estimated_wait
                            },
                            status=status_code,
                            headers={"Retry-After": str(estimated_wait)}
                        )
                
                # Almacenar request_id para liberarlo después
                request._queue_request_id = request_id
            
            # Agregar headers de degradación si aplica
            if degradation_level != 'none':
                response_data, _ = degradation_manager.get_degraded_response(degradation_level)
                if response_data:
                    request._degradation_info = response_data
            
        except Exception as e:
            # En caso de error, continuar sin backpressure (fail-open)
            logger.error(f"Error in BackpressureMiddleware: {e}", exc_info=True)
        
        return None
    
    def process_response(self, request, response):
        """
        Libera recursos de la cola después de procesar el request.
        """
        # Liberar slot de la cola si estaba encolado
        if hasattr(request, '_queue_request_id'):
            try:
                request_queue = get_request_queue()
                request_queue.release(request._queue_request_id)
            except Exception as e:
                logger.error(f"Error releasing queue slot: {e}", exc_info=True)
        
        # Agregar headers de degradación si aplica
        if hasattr(request, '_degradation_info'):
            degradation_info = request._degradation_info
            if degradation_info:
                # Agregar header X-Degradation-Level
                response['X-Degradation-Level'] = degradation_info.get('degradation_level', 'none')
                if 'retry_after' in degradation_info:
                    response['Retry-After'] = str(degradation_info['retry_after'])
        
        return response
    
    def process_exception(self, request, exception):
        """
        Asegura que los recursos se liberen incluso si hay una excepción.
        """
        if hasattr(request, '_queue_request_id'):
            try:
                request_queue = get_request_queue()
                request_queue.release(request._queue_request_id)
            except Exception:
                pass
        return None

