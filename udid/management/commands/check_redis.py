"""
Comando de gestión para verificar la conexión a Redis.
Uso: python manage.py check_redis
"""
from django.core.management.base import BaseCommand
from django.conf import settings
from django.core.cache import cache
from udid.utils.server.redis_ha import (
    get_redis_client_safe,
    is_redis_available,
    get_circuit_breaker_state
)
import redis
import time

# Importar channels solo si está disponible
try:
    from channels.layers import get_channel_layer
    CHANNELS_AVAILABLE = True
except ImportError:
    CHANNELS_AVAILABLE = False
    get_channel_layer = None


class Command(BaseCommand):
    help = 'Verifica la conexión y configuración de Redis'

    def add_arguments(self, parser):
        parser.add_argument(
            '--detailed',
            action='store_true',
            help='Muestra información detallada del servidor Redis'
        )

    def handle(self, *args, **options):
        detailed = options.get('detailed', False)
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('  VERIFICACIÓN DE CONEXIÓN A REDIS'))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))
        
        # 1. Verificar configuración
        self.stdout.write(self.style.WARNING('1. CONFIGURACIÓN:'))
        self._check_configuration()
        
        # 2. Verificar conexión directa con redis_ha
        self.stdout.write(self.style.WARNING('\n2. CONEXIÓN DIRECTA (redis_ha):'))
        redis_client = self._check_direct_connection(detailed)
        
        # 3. Verificar Django Cache
        self.stdout.write(self.style.WARNING('\n3. DJANGO CACHE:'))
        self._check_django_cache()
        
        # 4. Verificar Channel Layers
        self.stdout.write(self.style.WARNING('\n4. CHANNEL LAYERS (WebSockets):'))
        self._check_channel_layers()
        
        # 5. Verificar Circuit Breaker
        self.stdout.write(self.style.WARNING('\n5. CIRCUIT BREAKER:'))
        self._check_circuit_breaker()
        
        # 6. Información detallada del servidor
        if detailed and redis_client:
            self.stdout.write(self.style.WARNING('\n6. INFORMACIÓN DEL SERVIDOR:'))
            self._show_server_info(redis_client)
        
        self.stdout.write(self.style.SUCCESS('\n' + '='*70 + '\n'))

    def _check_configuration(self):
        """Verifica la configuración de Redis en settings"""
        redis_url = getattr(settings, 'REDIS_URL', None)
        redis_sentinel = getattr(settings, 'REDIS_SENTINEL', None)
        redis_channel_layer_url = getattr(settings, 'REDIS_CHANNEL_LAYER_URL', None)
        redis_rate_limit_url = getattr(settings, 'REDIS_RATE_LIMIT_URL', None)
        
        self.stdout.write(f'  REDIS_URL: {redis_url or "No configurado"}')
        self.stdout.write(f'  REDIS_SENTINEL: {redis_sentinel or "No configurado"}')
        self.stdout.write(f'  REDIS_CHANNEL_LAYER_URL: {redis_channel_layer_url or "No configurado"}')
        self.stdout.write(f'  REDIS_RATE_LIMIT_URL: {redis_rate_limit_url or "No configurado"}')
        
        # Verificar backend de cache
        cache_backend = getattr(settings, 'CACHES', {}).get('default', {}).get('BACKEND', 'No configurado')
        self.stdout.write(f'  Cache Backend: {cache_backend}')
        
        # Verificar channel layers
        channel_backend = getattr(settings, 'CHANNEL_LAYERS', {}).get('default', {}).get('BACKEND', 'No configurado')
        self.stdout.write(f'  Channel Layer Backend: {channel_backend}')

    def _check_direct_connection(self, detailed=False):
        """Verifica la conexión directa usando redis_ha"""
        try:
            client = get_redis_client_safe()
            if client:
                # Test de ping
                start_time = time.time()
                pong = client.ping()
                latency = (time.time() - start_time) * 1000  # en ms
                
                if pong:
                    self.stdout.write(self.style.SUCCESS(f'  ✅ Conexión exitosa (latencia: {latency:.2f}ms)'))
                    
                    # Test de escritura/lectura
                    test_key = 'udid:test:connection'
                    test_value = f'test_{int(time.time())}'
                    
                    client.set(test_key, test_value, ex=10)  # Expira en 10 segundos
                    retrieved = client.get(test_key)
                    
                    if retrieved and retrieved.decode('utf-8') == test_value:
                        self.stdout.write(self.style.SUCCESS('  ✅ Test de escritura/lectura: OK'))
                        client.delete(test_key)  # Limpiar
                    else:
                        self.stdout.write(self.style.ERROR('  ❌ Test de escritura/lectura: FALLÓ'))
                    
                    return client
                else:
                    self.stdout.write(self.style.ERROR('  ❌ Ping falló'))
                    return None
            else:
                self.stdout.write(self.style.ERROR('  ❌ No se pudo obtener cliente Redis'))
                return None
        except redis.ConnectionError as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Error de conexión: {str(e)}'))
            return None
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Error inesperado: {str(e)}'))
            return None

    def _check_django_cache(self):
        """Verifica el cache de Django"""
        try:
            # Test de escritura
            test_key = 'udid:test:cache'
            test_value = f'cache_test_{int(time.time())}'
            
            cache.set(test_key, test_value, 10)  # Expira en 10 segundos
            
            # Test de lectura
            retrieved = cache.get(test_key)
            
            if retrieved == test_value:
                self.stdout.write(self.style.SUCCESS('  ✅ Cache de Django: OK'))
                
                # Verificar backend
                cache_backend = cache.__class__.__name__
                self.stdout.write(f'     Backend: {cache_backend}')
                
                # Limpiar
                cache.delete(test_key)
            else:
                self.stdout.write(self.style.ERROR('  ❌ Cache de Django: FALLÓ (no se pudo leer el valor)'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Cache de Django: ERROR - {str(e)}'))

    def _check_channel_layers(self):
        """Verifica los Channel Layers"""
        if not CHANNELS_AVAILABLE:
            self.stdout.write(self.style.WARNING('  ⚠️  Channels no está instalado o disponible'))
            return
        
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                # Test de envío de mensaje
                test_channel = 'udid:test:channel'
                test_message = {'type': 'test', 'data': 'test_message'}
                
                # Intentar enviar un mensaje (puede fallar si no hay consumidores, pero eso está bien)
                try:
                    channel_layer.send(test_channel, test_message)
                    self.stdout.write(self.style.SUCCESS('  ✅ Channel Layer: OK (conexión establecida)'))
                except Exception as e:
                    # Si falla por falta de consumidores, la conexión está bien
                    if 'channel' in str(e).lower() or 'group' in str(e).lower():
                        self.stdout.write(self.style.SUCCESS('  ✅ Channel Layer: OK (conexión establecida, error esperado sin consumidores)'))
                    else:
                        self.stdout.write(self.style.ERROR(f'  ❌ Channel Layer: ERROR - {str(e)}'))
                
                # Mostrar backend
                backend_name = channel_layer.__class__.__name__
                self.stdout.write(f'     Backend: {backend_name}')
            else:
                self.stdout.write(self.style.ERROR('  ❌ Channel Layer: No configurado'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Channel Layer: ERROR - {str(e)}'))

    def _check_circuit_breaker(self):
        """Verifica el estado del circuit breaker"""
        try:
            state = get_circuit_breaker_state()
            is_available = is_redis_available()
            
            state_colors = {
                'closed': self.style.SUCCESS,
                'open': self.style.ERROR,
                'half_open': self.style.WARNING
            }
            
            color = state_colors.get(state, self.style.WARNING)
            state_display = state.upper().replace('_', ' ')
            
            self.stdout.write(color(f'  Estado: {state_display}'))
            self.stdout.write(
                self.style.SUCCESS('  ✅ Disponible') if is_available 
                else self.style.ERROR('  ❌ No disponible')
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Error al verificar circuit breaker: {str(e)}'))

    def _show_server_info(self, client):
        """Muestra información detallada del servidor Redis"""
        try:
            info = client.info()
            
            # Información básica
            self.stdout.write(f'  Versión Redis: {info.get("redis_version", "N/A")}')
            self.stdout.write(f'  Modo: {info.get("redis_mode", "N/A")}')
            self.stdout.write(f'  OS: {info.get("os", "N/A")}')
            
            # Memoria
            used_memory = info.get('used_memory_human', 'N/A')
            max_memory = info.get('maxmemory_human', '0B')
            self.stdout.write(f'  Memoria usada: {used_memory}')
            self.stdout.write(f'  Memoria máxima: {max_memory if max_memory != "0B" else "Sin límite"}')
            
            # Conexiones
            connected_clients = info.get('connected_clients', 'N/A')
            self.stdout.write(f'  Clientes conectados: {connected_clients}')
            
            # Base de datos
            db_size = client.dbsize()
            self.stdout.write(f'  Claves en DB 0: {db_size}')
            
            # Estadísticas
            total_commands = info.get('total_commands_processed', 'N/A')
            self.stdout.write(f'  Comandos procesados: {total_commands}')
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'  ❌ Error al obtener información del servidor: {str(e)}'))

