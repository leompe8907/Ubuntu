"""
Vistas REST para ejecutar tareas de Celery manualmente.

Endpoints para ejecutar tareas de sincronización bajo demanda,
útil para testing, debugging y ejecución manual de tareas.
"""
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status

from .tasks import (
    initial_sync_all_data,
    update_subscribers_task,
    sync_smartcards_full_task,
)

logger = logging.getLogger(__name__)


@api_view(['POST', 'GET'])
@permission_classes([AllowAny])
def trigger_initial_sync(request):
    """
    Endpoint para ejecutar la tarea de sincronización inicial completa.
    
    Esta tarea descarga TODOS los datos desde Panaccess:
    - Suscriptores
    - Smartcards
    - Credenciales de login
    - Crea registros en todas las tablas
    
    IMPORTANTE: Esta tarea puede tomar varias horas.
    
    Returns:
        Response con el ID de la tarea y estado
    """
    try:
        logger.info("🚀 [API] Solicitada ejecución de sincronización inicial completa")
        
        # Ejecutar tarea de forma asíncrona
        task = initial_sync_all_data.delay()
        
        logger.info(f"✅ [API] Tarea de sincronización inicial iniciada. Task ID: {task.id}")
        
        return Response({
            'success': True,
            'message': 'Tarea de sincronización inicial iniciada',
            'task_id': task.id,
            'task_name': 'initial_sync_all_data',
            'status': 'PENDING',
            'note': 'Esta tarea puede tomar varias horas. Usa el task_id para consultar el estado.'
        }, status=status.HTTP_202_ACCEPTED)
        
    except Exception as e:
        error_msg = f"Error al iniciar tarea de sincronización inicial: {str(e)}"
        logger.error(f"❌ [API] {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': error_msg
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST', 'GET'])
@permission_classes([AllowAny])
def trigger_update_subscribers(request):
    """
    Endpoint para ejecutar la tarea de actualización rápida de suscriptores.
    
    Esta tarea actualiza rápidamente:
    - Suscriptores
    - Credenciales de login
    - Asociación de smartcards con suscriptores
    - Merge en SubscriberInfo
    
    Esta tarea es rápida (segundos/minutos) y se ejecuta normalmente cada 5 minutos.
    
    Returns:
        Response con el ID de la tarea y estado
    """
    try:
        logger.info("🔄 [API] Solicitada ejecución de actualización rápida de suscriptores")
        
        # Ejecutar tarea de forma asíncrona
        task = update_subscribers_task.delay()
        
        logger.info(f"✅ [API] Tarea de actualización rápida iniciada. Task ID: {task.id}")
        
        return Response({
            'success': True,
            'message': 'Tarea de actualización rápida iniciada',
            'task_id': task.id,
            'task_name': 'update_subscribers_task',
            'status': 'PENDING',
            'note': 'Esta tarea es rápida (segundos/minutos)'
        }, status=status.HTTP_202_ACCEPTED)
        
    except Exception as e:
        error_msg = f"Error al iniciar tarea de actualización rápida: {str(e)}"
        logger.error(f"❌ [API] {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': error_msg
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST', 'GET'])
@permission_classes([AllowAny])
def trigger_full_sync(request):
    """
    Endpoint para ejecutar la tarea de sincronización completa.
    
    Esta tarea sincroniza COMPLETAMENTE:
    - Smartcards completas (productos, paquetes, estado)
    - Suscriptores completos
    - Credenciales de login completas
    - Valida asociación de smartcards con suscriptores
    - Merge en SubscriberInfo
    
    IMPORTANTE: Esta tarea puede tomar varias horas (ej: 8-9 horas con 10,000 smartcards).
    Normalmente se ejecuta diariamente a las 00:00.
    
    Returns:
        Response con el ID de la tarea y estado
    """
    try:
        logger.info("🔄 [API] Solicitada ejecución de sincronización completa")
        
        # Ejecutar tarea de forma asíncrona
        task = sync_smartcards_full_task.delay()
        
        logger.info(f"✅ [API] Tarea de sincronización completa iniciada. Task ID: {task.id}")
        
        return Response({
            'success': True,
            'message': 'Tarea de sincronización completa iniciada',
            'task_id': task.id,
            'task_name': 'sync_smartcards_full_task',
            'status': 'PENDING',
            'note': 'Esta tarea puede tomar varias horas. Usa el task_id para consultar el estado.'
        }, status=status.HTTP_202_ACCEPTED)
        
    except Exception as e:
        error_msg = f"Error al iniciar tarea de sincronización completa: {str(e)}"
        logger.error(f"❌ [API] {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': error_msg
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_task_status(request, task_id):
    """
    Endpoint para consultar el estado de una tarea de Celery.
    
    Args:
        task_id: ID de la tarea de Celery
        
    Returns:
        Response con el estado y resultado de la tarea
    """
    try:
        from celery.result import AsyncResult
        
        task_result = AsyncResult(task_id)
        
        response_data = {
            'task_id': task_id,
            'status': task_result.status,
            'ready': task_result.ready(),
            'successful': task_result.successful() if task_result.ready() else None,
            'failed': task_result.failed() if task_result.ready() else None,
        }
        
        if task_result.ready():
            if task_result.successful():
                response_data['result'] = task_result.result
            else:
                response_data['error'] = str(task_result.result)
                response_data['traceback'] = task_result.traceback if hasattr(task_result, 'traceback') else None
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except Exception as e:
        error_msg = f"Error al consultar estado de tarea: {str(e)}"
        logger.error(f"❌ [API] {error_msg}", exc_info=True)
        
        return Response({
            'success': False,
            'error_type': type(e).__name__,
            'message': error_msg
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

