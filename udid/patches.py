"""
Parches para compatibilidad con Django 4.2+
Este archivo debe importarse en settings.py antes de que Django cargue los modelos.
"""
import sys
from django.db import models

# Parche para django-cron: interceptar la creación del modelo y remover index_together
def patch_django_cron_meta():
    """
    Parchea la clase Options de Django para remover index_together antes de que se use.
    """
    original_contribute_to_class = models.options.Options.contribute_to_class
    
    def patched_contribute_to_class(self, cls, name):
        # Si tiene index_together, convertirlo a indexes antes de contribuir
        if hasattr(self, 'index_together') and self.index_together:
            # Convertir index_together a indexes
            if not hasattr(self, 'indexes'):
                self.indexes = []
            
            for fields in self.index_together:
                if isinstance(fields, (list, tuple)):
                    self.indexes.append(models.Index(fields=list(fields)))
            
            # Remover index_together
            self.index_together = None
        
        # Llamar al método original
        return original_contribute_to_class(self, cls, name)
    
    # Aplicar el parche
    models.options.Options.contribute_to_class = patched_contribute_to_class

# Ejecutar el parche automáticamente al importar este módulo
patch_django_cron_meta()

