"""
Middleware para rastrear carga del sistema y aplicar protección DDoS.
"""
from django.utils.deprecation import MiddlewareMixin
from .util import track_system_request


class SystemLoadTrackingMiddleware(MiddlewareMixin):
    """
    Middleware que rastrea cada request para monitoreo de carga del sistema.
    Esto permite que el rate limiting adaptativo funcione correctamente.
    """
    
    def process_request(self, request):
        """
        Rastrea cada request para calcular la carga del sistema.
        """
        # Solo rastrear requests a endpoints de la API
        if request.path.startswith('/udid/') or request.path.startswith('/auth/'):
            track_system_request()
        
        return None

