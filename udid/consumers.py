import json
import asyncio
import time

from channels.generic.websocket import AsyncWebsocketConsumer
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder

from .services import authenticate_with_udid_service
from .util import (
    generate_device_fingerprint,
    check_websocket_rate_limit,
    increment_websocket_connection,
    decrement_websocket_connection,
    check_websocket_limits,
    decrement_websocket_limits,
)

def _get_header(scope, key: str) -> str:
    """Obtiene un header HTTP del scope ASGI en minúsculas."""
    headers = dict(scope.get("headers", []))
    return headers.get(key.encode().lower(), b"").decode(errors="ignore")


class AuthWaitWS(AsyncWebsocketConsumer):
    """
    Protocolo:
      -> {"type":"auth_with_udid","udid":"...","app_type":"android_tv","app_version":"1.0"}
      <- Si YA está listo: {"type":"auth_with_udid:result","status":"ok","result":{...}} y cierra.
      <- Si NO está listo (not_validated o not_associated):
           {"type":"pending","status": "...", "timeout": ...} y queda esperando evento "udid.validated".
         Al recibir el evento:
           vuelve a invocar el servicio, envía credenciales cifradas y cierra.

      Heartbeat:
      -> {"type":"ping"}  <- {"type":"pong"}
    """

    # Configuraciones (podés sobreescribir en settings.py)
    TIMEOUT_SECONDS = getattr(settings, "UDID_WAIT_TIMEOUT", 600)
    ENABLE_POLLING = getattr(settings, "UDID_ENABLE_POLLING", False)
    POLL_INTERVAL = getattr(settings, "UDID_POLL_INTERVAL", 2)
    PING_INTERVAL = getattr(settings, "UDID_WS_PING_INTERVAL", 30)  # segundos
    INACTIVITY_TIMEOUT = getattr(settings, "UDID_WS_INACTIVITY_TIMEOUT", 60)  # segundos
    MAX_CONNECTIONS_PER_TOKEN = getattr(settings, "UDID_WS_MAX_PER_TOKEN", 5)
    MAX_GLOBAL_CONNECTIONS = getattr(settings, "UDID_WS_MAX_GLOBAL", 1000)

    async def connect(self):
        self.udid = None
        self.app_type = None
        self.app_version = None
        self.group_name = None
        self.done = False
        self.device_fingerprint = None
        self.last_activity = time.time()

        # tareas async opcionales
        self.timeout_task = None
        self.poll_task = None
        self.ping_task = None
        self.inactivity_task = None

        # Rate limiting: verificar antes de aceptar conexión
        # Obtener device fingerprint del scope
        self.device_fingerprint = await sync_to_async(generate_device_fingerprint)(self.scope)
        
        # Verificar límites de WebSocket (token y global) usando Redis
        is_allowed, reason, retry_after = await sync_to_async(check_websocket_limits)(
            udid=None,  # Aún no se conoce el UDID
            device_fingerprint=self.device_fingerprint,
            max_per_token=self.MAX_CONNECTIONS_PER_TOKEN,
            max_global=self.MAX_GLOBAL_CONNECTIONS
        )
        
        if not is_allowed:
            # Rechazar conexión si excede el límite
            await self.close(code=4001, reason=f"{reason}. Retry after {retry_after}s")
            return
        
        # También mantener compatibilidad con el rate limit anterior
        is_allowed_old, remaining, retry_after_old = await sync_to_async(check_websocket_rate_limit)(
            udid=None,
            device_fingerprint=self.device_fingerprint,
            max_connections=5,
            window_minutes=5
        )
        
        if not is_allowed_old:
            # Revertir incremento de límites nuevos
            await sync_to_async(decrement_websocket_limits)(None, self.device_fingerprint)
            await self.close(code=4001, reason=f"Too many connections. Retry after {retry_after_old}s")
            return
        
        # Incrementar contador de conexiones activas (sistema anterior)
        await sync_to_async(increment_websocket_connection)(
            udid=None,
            device_fingerprint=self.device_fingerprint,
            window_minutes=5
        )
        
        await self.accept()
        
        # Iniciar ping periódico
        self.ping_task = asyncio.create_task(self._ping_loop())
        
        # Iniciar timeout de inactividad
        self.inactivity_task = asyncio.create_task(self._inactivity_check())

    async def receive(self, text_data=None, bytes_data=None):
        if self.done:
            return

        # Parseo JSON
        try:
            data = json.loads(text_data or "{}")
        except Exception:
            return await self._send_err("bad_json", "El cuerpo debe ser JSON", close=True)

        # Actualizar última actividad
        self.last_activity = time.time()
        
        # Heartbeat (mantener viva la conexión)
        if data.get("type") == "ping":
            return await self._send_json({"type": "pong"})

        # Mensaje esperado
        if data.get("type") != "auth_with_udid":
            return await self._send_err("bad_type", "Usa type=auth_with_udid", close=True)

        # Parámetros mínimos
        self.udid = (data.get("udid") or "").strip()
        self.app_type = (data.get("app_type") or "android_tv").strip()
        self.app_version = (data.get("app_version") or "1.0").strip()
        if not self.udid:
            return await self._send_err("missing_udid", "UDID es requerido", close=True)
        
        # Verificar límites con UDID (ahora que lo conocemos)
        if self.udid:
            # Verificar límites nuevos (token y global) con UDID
            is_allowed_new, reason_new, retry_after_new = await sync_to_async(check_websocket_limits)(
                udid=self.udid,
                device_fingerprint=self.device_fingerprint,
                max_per_token=self.MAX_CONNECTIONS_PER_TOKEN,
                max_global=self.MAX_GLOBAL_CONNECTIONS
            )
            
            if not is_allowed_new:
                await self._send_err(
                    "rate_limit_exceeded",
                    f"{reason_new}. Retry after {retry_after_new}s",
                    close=True
                )
                return
            
            # También verificar límites antiguos
            is_allowed_old, remaining, retry_after_old = await sync_to_async(check_websocket_rate_limit)(
                udid=self.udid,
                device_fingerprint=self.device_fingerprint,
                max_connections=5,
                window_minutes=5
            )
            
            if not is_allowed_old:
                # Revertir incremento de límites nuevos
                await sync_to_async(decrement_websocket_limits)(self.udid, self.device_fingerprint)
                await self._send_err(
                    "rate_limit_exceeded",
                    f"Too many connections for this device. Retry after {retry_after_old}s",
                    close=True
                )
                return
            
            # Incrementar contador con UDID ahora que lo conocemos (sistema anterior)
            await sync_to_async(increment_websocket_connection)(
                udid=self.udid,
                device_fingerprint=self.device_fingerprint,
                window_minutes=5
            )

        # Metadatos de cliente (para auditoría del servicio)
        client_ip = (self.scope.get("client") or [""])[0] or ""
        user_agent = _get_header(self.scope, "user-agent")

        # 1) Intento inmediato
        res = await sync_to_async(authenticate_with_udid_service)(
            udid=self.udid,
            app_type=self.app_type,
            app_version=self.app_version,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        if res.get("ok"):
            await self._send_result(res)
            return await self.close()

        # Errores fatales (no se resuelven esperando)
        fatal_codes = {
            "invalid_udid",
            "expired",
            "subscriber_not_found",
            "no_app_credentials",
            "encryption_failed",
        }
        # OJO: "not_associated" NO es fatal; permite esperar a que complete la asociación.
        if res.get("code") in fatal_codes:
            await self._send_result(res, status="error")
            return await self.close()

        # 2) Aún no está listo → responder pending y suscribirse al grupo
        self.group_name = f"udid_{self.udid}"
        try:
            # Intentar suscribirse al grupo con retry
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    await self.channel_layer.group_add(self.group_name, self.channel_name)
                    break  # Éxito
                except Exception as e:
                    if attempt < max_retries - 1:
                        # Reintentar después de un breve delay
                        await asyncio.sleep(0.1 * (attempt + 1))
                        continue
                    else:
                        # Último intento falló
                        raise
        except Exception as e:
            # p.ej., channel layer no disponible o Redis saturado
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error suscribiendo WebSocket al grupo {self.group_name}: {e}", exc_info=True)
            await self._send_err("channel_layer_unavailable", f"Error de conexión: {str(e)}", close=True)
            return

        await self._send_json({
            "type": "pending",
            "status": res.get("status") or "not_validated",  # puede ser "validated" si es not_associated
            "detail": res.get("error") or "Esperando validación/asociación de UDID…",
            "timeout": self.TIMEOUT_SECONDS,
        })

        # Timeout
        self.timeout_task = asyncio.create_task(self._timeout())

        # Polling opcional como respaldo (si el evento no llega)
        if self.ENABLE_POLLING:
            self.poll_task = asyncio.create_task(self._poll_every(self.POLL_INTERVAL))

    async def udid_validated(self, event):
        """Handler para eventos de grupo con type 'udid.validated'."""
        if self.done or not self.udid or event.get("udid") != self.udid:
            return

        client_ip = (self.scope.get("client") or [""])[0] or ""
        user_agent = _get_header(self.scope, "user-agent")

        res = await sync_to_async(authenticate_with_udid_service)(
            udid=self.udid,
            app_type=self.app_type,
            app_version=self.app_version,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        await self._send_result(res, status=("ok" if res.get("ok") else "error"))
        await self._finish()

    async def disconnect(self, code):
        await self._cleanup()

    # ---------------- helpers ----------------

    async def _send_result(self, res: dict, status: str | None = None):
        """Envía la respuesta final; usa DjangoJSONEncoder para fechas/Decimal/etc."""
        self.done = True
        payload = {
            "type": "auth_with_udid:result",
            "status": status or ("ok" if res.get("ok") else "error"),
            "result": res,
        }
        await self._send_json(payload)

    async def _send_err(self, code: str, detail: str, close: bool = False):
        await self._send_json({"type": "error", "code": code, "detail": detail})
        if close:
            await self.close(code=1011)

    async def _send_json(self, obj: dict):
        """Serializa con DjangoJSONEncoder para evitar errores de datetime, Decimal, etc."""
        try:
            await self.send(text_data=json.dumps(obj, cls=DjangoJSONEncoder))
        except Exception as e:
            # Falla de serialización u otra — reporta y cierra limpio
            try:
                await self.send(text_data=json.dumps({
                    "type": "error",
                    "code": "serialization_error",
                    "detail": str(e),
                }, cls=DjangoJSONEncoder))
            finally:
                await self.close(code=1011)

    async def _timeout(self):
        await asyncio.sleep(self.TIMEOUT_SECONDS)
        if not self.done:
            await self._send_json({"type": "timeout", "detail": "No se recibió validación/asociación a tiempo."})
            await self._finish()

    async def _poll_every(self, seconds: int):
        """Reconsulta el servicio periódicamente (respaldo si el evento no llega)."""
        try:
            while not self.done:
                await asyncio.sleep(seconds)

                client_ip = (self.scope.get("client") or [""])[0] or ""
                user_agent = _get_header(self.scope, "user-agent")

                res = await sync_to_async(authenticate_with_udid_service)(
                    udid=self.udid,
                    app_type=self.app_type,
                    app_version=self.app_version,
                    client_ip=client_ip,
                    user_agent=user_agent,
                )

                if res.get("ok"):
                    await self._send_result(res, status="ok")
                    return await self._finish()

                fatal_codes = {
                    "invalid_udid",
                    "expired",
                    "subscriber_not_found",
                    "no_app_credentials",
                    "encryption_failed",
                }
                if res.get("code") in fatal_codes:
                    await self._send_result(res, status="error")
                    return await self._finish()

                # not_validated / not_associated -> seguir esperando
        except asyncio.CancelledError:
            pass

    async def _finish(self):
        await self._cleanup()
        try:
            await self.close()
        except Exception:
            pass

    async def _ping_loop(self):
        """Envía pings periódicos para mantener la conexión viva"""
        try:
            while not self.done:
                await asyncio.sleep(self.PING_INTERVAL)
                if not self.done:
                    try:
                        await self._send_json({"type": "ping"})
                    except Exception:
                        # Si falla el ping, la conexión probablemente se cerró
                        break
        except asyncio.CancelledError:
            pass

    async def _inactivity_check(self):
        """Cierra conexiones inactivas después de un timeout"""
        try:
            while not self.done:
                await asyncio.sleep(10)  # Verificar cada 10 segundos
                if not self.done:
                    inactivity_time = time.time() - self.last_activity
                    if inactivity_time > self.INACTIVITY_TIMEOUT:
                        await self._send_json({
                            "type": "error",
                            "code": "inactivity_timeout",
                            "detail": f"Connection closed due to inactivity ({self.INACTIVITY_TIMEOUT}s)"
                        })
                        await self._finish()
                        break
        except asyncio.CancelledError:
            pass

    async def _cleanup(self):
        self.done = True

        # Decrementar contador de conexiones WebSocket (sistema nuevo)
        if self.device_fingerprint:
            await sync_to_async(decrement_websocket_limits)(
                udid=self.udid,
                device_fingerprint=self.device_fingerprint
            )
        
        # Decrementar contador de conexiones WebSocket (sistema anterior)
        if self.device_fingerprint:
            await sync_to_async(decrement_websocket_connection)(
                udid=self.udid,
                device_fingerprint=self.device_fingerprint
            )

        if getattr(self, "group_name", None):
            try:
                await self.channel_layer.group_discard(self.group_name, self.channel_name)
            except Exception:
                pass

        # Cancelar todas las tareas
        for tname in ("timeout_task", "poll_task", "ping_task", "inactivity_task"):
            task = getattr(self, tname, None)
            if task and not task.done():
                task.cancel()
