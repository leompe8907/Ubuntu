"""
Módulo de integración con Panaccess API.
Contiene el cliente y todas las funciones de sincronización.
"""
from .client import CVClient, PanaccessClient
from .auth import login, logged_in, hash_password
from .exceptions import (
    PanaccessException,
    PanaccessAuthenticationError,
    PanaccessSessionError,
    PanaccessRateLimitError,
    PanaccessConnectionError,
    PanaccessTimeoutError,
    PanaccessAPIError,
)
from .singleton import (
    PanaccessSingleton,
    get_panaccess,
    initialize_panaccess,
)
from .smartcard import sync_smartcards, update_smartcards_from_subscribers
from .subscriber import sync_subscribers
from .login import sync_subscriber_logins
from .subscriberinfo import sync_merge_all_subscribers

__all__ = [
    # Cliente
    'CVClient',  # Alias para compatibilidad
    'PanaccessClient',
    # Autenticación
    'login',
    'logged_in',
    'hash_password',
    # Excepciones
    'PanaccessException',
    'PanaccessAuthenticationError',
    'PanaccessSessionError',
    'PanaccessRateLimitError',
    'PanaccessConnectionError',
    'PanaccessTimeoutError',
    'PanaccessAPIError',
    # Singleton
    'PanaccessSingleton',
    'get_panaccess',
    'initialize_panaccess',
    # Sincronización
    'sync_smartcards',
    'update_smartcards_from_subscribers',
    'sync_subscribers',
    'sync_subscriber_logins',
    'sync_merge_all_subscribers',
]
