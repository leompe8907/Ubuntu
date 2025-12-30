"""
Sistema de checkpoints para guardar el progreso de sincronización.

Permite reanudar la descarga desde el último punto procesado en caso de fallos.
"""
import logging
import json
from typing import Optional
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Tiempo de expiración de checkpoints (7 días)
CHECKPOINT_TTL = 7 * 24 * 60 * 60  # 7 días en segundos


def save_checkpoint(sync_type: str, offset: int, metadata: dict = None):
    """
    Guarda un checkpoint del progreso de sincronización.
    
    Args:
        sync_type: Tipo de sincronización ('smartcards', 'subscribers', etc.)
        offset: Último offset procesado
        metadata: Información adicional (opcional)
    """
    try:
        key = f"sync_checkpoint:{sync_type}"
        data = {
            'offset': offset,
            'metadata': metadata or {}
        }
        cache.set(key, json.dumps(data), CHECKPOINT_TTL)
        logger.debug(f"✅ Checkpoint guardado: {sync_type} en offset {offset}")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo guardar checkpoint: {str(e)}")


def get_checkpoint(sync_type: str) -> Optional[dict]:
    """
    Obtiene el último checkpoint guardado.
    
    Args:
        sync_type: Tipo de sincronización ('smartcards', 'subscribers', etc.)
    
    Returns:
        Dict con 'offset' y 'metadata', o None si no hay checkpoint
    """
    try:
        key = f"sync_checkpoint:{sync_type}"
        data = cache.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.warning(f"⚠️ No se pudo obtener checkpoint: {str(e)}")
        return None


def clear_checkpoint(sync_type: str):
    """
    Elimina un checkpoint.
    
    Args:
        sync_type: Tipo de sincronización
    """
    try:
        key = f"sync_checkpoint:{sync_type}"
        cache.delete(key)
        logger.debug(f"🗑️ Checkpoint eliminado: {sync_type}")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo eliminar checkpoint: {str(e)}")


def get_last_processed_offset(sync_type: str) -> int:
    """
    Obtiene el último offset procesado desde el checkpoint.
    
    Args:
        sync_type: Tipo de sincronización
    
    Returns:
        Último offset procesado, o 0 si no hay checkpoint
    """
    checkpoint = get_checkpoint(sync_type)
    if checkpoint:
        return checkpoint.get('offset', 0)
    return 0

