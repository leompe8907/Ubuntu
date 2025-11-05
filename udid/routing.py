from django.urls import re_path
from .consumers import AuthWaitWS

# Rutas de WebSocket para Channels
websocket_urlpatterns = [
    # ws://<host>/ws/auth/
    re_path(r"^ws/auth/$", AuthWaitWS.as_asgi()),
]
