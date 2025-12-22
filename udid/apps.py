import logging
import os
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class UdidConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'udid'
    
    def ready(self):
        """
        Se ejecuta cuando Django está completamente cargado.
        Aquí inicializamos el singleton de PanAccess.
        
        Nota: En modo desarrollo (runserver), Django crea dos procesos:
        - Proceso principal (monitor de archivos)
        - Proceso hijo (servidor real)
        
        Solo inicializamos en el proceso hijo para evitar duplicación.
        """
        # No inicializar durante tests
        if os.environ.get('DJANGO_TEST') == 'true':
            logger.debug("Modo test detectado, omitiendo inicialización de PanAccess")
            return
        
        # En modo desarrollo, solo ejecutar en el proceso hijo (servidor real)
        # Django establece RUN_MAIN solo en el proceso hijo
        if os.environ.get('RUN_MAIN') != 'true':
            # Estamos en el proceso principal (monitor), no inicializar
            return
        
        try:
            from udid.utils.panaccess import initialize_panaccess
            logger.info("🚀 Inicializando PanAccess singleton...")
            initialize_panaccess()
        except Exception as e:
            logger.error(f"❌ Error al inicializar PanAccess en ready(): {str(e)}")
            # No lanzamos excepción para que Django pueda arrancar
            logger.warning("⚠️ El sistema intentará autenticarse en el primer request")
