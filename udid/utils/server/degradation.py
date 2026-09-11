"""
Lógica de degradación elegante del sistema.
Implementa degradación automática basada en métricas del sistema.
"""
import logging
import threading
from typing import Optional, Tuple, Dict
from django.conf import settings

logger = logging.getLogger(__name__)


class DegradationManager:
    """
    Gestiona la degradación elegante del sistema basada en métricas.
    
    Niveles de degradación:
    - none: Sin degradación
    - medium: Degradación mínima (carga 1.5-2x)
    - high: Degradación moderada (carga 2-3x)
    - critical: Degradación máxima (carga >3x)
    """
    
    def __init__(self):
        self.baseline_load = getattr(settings, 'DEGRADATION_BASELINE_LOAD', 100)
        self.medium_threshold = getattr(settings, 'DEGRADATION_MEDIUM_THRESHOLD', 1.5)
        self.high_threshold = getattr(settings, 'DEGRADATION_HIGH_THRESHOLD', 2.0)
        self.critical_threshold = getattr(settings, 'DEGRADATION_CRITICAL_THRESHOLD', 3.0)
        self._current_level = 'none'
    
    def should_degrade(self, current_load: float, 
                      latency_p95: Optional[float] = None,
                      error_rate: Optional[float] = None,
                      cpu_percent: Optional[float] = None) -> str:
        """
        Determina el nivel de degradación necesario basado en métricas.
        
        Args:
            current_load: Carga actual del sistema (requests/segundo o concurrentes)
            latency_p95: Latencia p95 en milisegundos (opcional)
            error_rate: Tasa de errores (0-1) (opcional)
            cpu_percent: Porcentaje de CPU (0-100) (opcional)
            
        Returns:
            str: Nivel de degradación ('none', 'medium', 'high', 'critical')
        """
        # Calcular ratio de carga
        load_ratio = current_load / self.baseline_load if self.baseline_load > 0 else 1.0
        
        # Determinar nivel basado en ratio de carga
        if load_ratio >= self.critical_threshold:
            level = 'critical'
        elif load_ratio >= self.high_threshold:
            level = 'high'
        elif load_ratio >= self.medium_threshold:
            level = 'medium'
        else:
            level = 'none'
        
        # Ajustar nivel basado en métricas adicionales
        if latency_p95 and latency_p95 > 10000:  # > 10 segundos
            if level == 'none':
                level = 'medium'
            elif level == 'medium':
                level = 'high'
        
        if error_rate and error_rate > 0.1:  # > 10% errores
            if level != 'critical':
                level = 'critical'
        
        if cpu_percent and cpu_percent > 90:  # > 90% CPU
            if level == 'none':
                level = 'medium'
            elif level == 'medium':
                level = 'high'
        
        # Actualizar nivel actual
        if level != self._current_level:
            logger.info(
                f"Degradation level changed: {self._current_level} -> {level} "
                f"(load_ratio={load_ratio:.2f}, latency_p95={latency_p95}, "
                f"error_rate={error_rate}, cpu={cpu_percent}%)"
            )
            self._current_level = level
        
        return level
    
    def get_degraded_response(self, level: str) -> Tuple[Optional[Dict], int]:
        """
        Retorna una respuesta degradada según el nivel.
        
        Args:
            level: Nivel de degradación
            
        Returns:
            tuple: (response_data: dict or None, status_code: int)
        """
        if level == 'critical':
            return {
                'error': 'Service temporarily unavailable',
                'message': 'System is under extreme load. Please try again later.',
                'retry_after': 60,
                'degradation_level': 'critical'
            }, 503
        
        elif level == 'high':
            return {
                'warning': 'Service degraded',
                'message': 'System is experiencing high load. Some features may be unavailable.',
                'retry_after': 30,
                'degradation_level': 'high'
            }, 200
        
        elif level == 'medium':
            return {
                'info': 'Service operating under load',
                'message': 'System is experiencing moderate load. Response times may be slower.',
                'degradation_level': 'medium'
            }, 200
        
        else:
            return None, 200
    
    def get_current_level(self) -> str:
        """Retorna el nivel actual de degradación"""
        return self._current_level
    
    def should_skip_non_critical_features(self, level: str) -> bool:
        """
        Determina si se deben saltar features no críticas.
        
        Args:
            level: Nivel de degradación
            
        Returns:
            bool: True si se deben saltar features no críticas
        """
        return level in ('high', 'critical')
    
    def should_reject_low_priority_requests(self, level: str) -> bool:
        """
        Determina si se deben rechazar requests de baja prioridad.
        
        Args:
            level: Nivel de degradación
            
        Returns:
            bool: True si se deben rechazar requests de baja prioridad
        """
        return level == 'critical'


# Instancia global del gestor de degradación
_degradation_manager = None
_degradation_lock = threading.Lock()


def get_degradation_manager() -> DegradationManager:
    """
    Obtiene la instancia global del gestor de degradación.
    
    Returns:
        DegradationManager: Instancia global
    """
    global _degradation_manager
    
    if _degradation_manager is None:
        with _degradation_lock:
            if _degradation_manager is None:
                _degradation_manager = DegradationManager()
    
    return _degradation_manager


def should_degrade(current_load: float, **kwargs) -> str:
    """
    Función helper para determinar degradación.
    
    Args:
        current_load: Carga actual del sistema
        **kwargs: Métricas adicionales (latency_p95, error_rate, cpu_percent)
        
    Returns:
        str: Nivel de degradación
    """
    manager = get_degradation_manager()
    return manager.should_degrade(current_load, **kwargs)


def get_degraded_response(level: str) -> Tuple[Optional[Dict], int]:
    """
    Función helper para obtener respuesta degradada.
    
    Args:
        level: Nivel de degradación
        
    Returns:
        tuple: (response_data, status_code)
    """
    manager = get_degradation_manager()
    return manager.get_degraded_response(level)

