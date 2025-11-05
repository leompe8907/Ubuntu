"""
ASGI config for server project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.1/howto/deployment/asgi/
"""

# server/asgi.py
import os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.settings")

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
# from channels.security.websocket import AllowedHostsOriginValidator # <-- Eliminar o comentar

django_asgi_app = get_asgi_application()

import udid.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # El URLRouter directamente, sin el validador de hosts
    "websocket": URLRouter(udid.routing.websocket_urlpatterns),
})