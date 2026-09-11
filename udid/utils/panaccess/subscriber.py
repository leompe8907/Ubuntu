import logging
import time
from django.db import transaction, connection
from django.db.models import Max, DateField
from django.db.utils import OperationalError, DatabaseError
from typing import Optional
from .singleton import get_panaccess
from .exceptions import (
    PanaccessException, 
    PanaccessAPIError,
    PanaccessTimeoutError,
    PanaccessSessionError
)
from udid.models import ListOfSubscriber
from ...serializers import ListOfSubscriberSerializer
from ...utils.db_utils import is_connection_error, reconnect_database
from config import PanaccessConfig

logger = logging.getLogger(__name__)

# Configuración de timeouts y reintentos
DEFAULT_TIMEOUT = 30  # 30 segundos
MAX_RETRIES = 3
RETRY_DELAY = 2  # segundos entre reintentos


def DataBaseEmpty():
    """
    Verifica si la tabla ListOfSubscriber está vacía.
    """
    logger.info("Verificando si la base de datos de suscriptores está vacía...")
    return not ListOfSubscriber.objects.exists()

def LastSubscriber():
    """
    Retorna el último suscriptor registrado en la base de datos según el campo 'code'.
    """
    logger.info("Buscando el último suscriptor en la base de datos...")
    try:
        return ListOfSubscriber.objects.latest('code')
    except ListOfSubscriber.DoesNotExist:
        logger.warning("No se encontró ningún suscriptor en la base de datos.")
        return None

def store_or_update_subscribers(data_batch):
    """
    Inserta nuevos suscriptores o actualiza los existentes si hay cambios.
    """
    logger.info("Iniciando almacenamiento/actualización de suscriptores...")
    chunk_size = 100
    total_new = 0
    total_invalid = 0

    for i in range(0, len(data_batch), chunk_size):
        chunk = data_batch[i:i + chunk_size]
        codes = {item['code'] for item in chunk if 'code' in item}
        existing = {
            obj.code: obj for obj in ListOfSubscriber.objects.filter(code__in=codes)
        }

        with transaction.atomic():
            new_objects = []
            for item in chunk:
                serializer = ListOfSubscriberSerializer(data=item)
                if not serializer.is_valid():
                    logger.warning(f"Datos inválidos: {serializer.errors}")
                    total_invalid += 1
                    continue

                validated = serializer.validated_data
                code = validated.get('code')

                if code in existing:
                    obj = existing[code]
                    changed = False
                    for key, val in validated.items():
                        if getattr(obj, key, None) != val:
                            setattr(obj, key, val)
                            changed = True
                    if changed:
                        obj.save(update_fields=list(validated.keys()))
                else:
                    new_objects.append(ListOfSubscriber(**validated))
                    total_new += 1

            if new_objects:
                ListOfSubscriber.objects.bulk_create(new_objects, ignore_conflicts=True)
                logger.info(f"Insertados {len(new_objects)} nuevos suscriptores")

    logger.info(f"Suscriptores procesados: nuevos={total_new}, inválidos={total_invalid}")
    return total_new, total_invalid

def fetch_all_subscribers(session_id=None, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Descarga todos los suscriptores desde Panaccess y los almacena en la base de datos.
    
    Guarda cada lote inmediatamente para evitar pérdida de datos en caso de fallos.
    Implementa reintentos automáticos y manejo de timeouts.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos para cada llamada (default: 30)
    
    Returns:
        Dict con estadísticas de la descarga
    """
    logger.info(f"🔄 Iniciando descarga completa de suscriptores (timeout: {timeout}s)...")
    
    # Siempre comenzar desde offset 0
    offset = 0
    
    total_saved = 0
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        retry_count = 0
        batch_saved = False
        
        while retry_count < MAX_RETRIES:
            try:
                # Llamar API con timeout configurable
                result = CallListSubscribers(session_id, offset, limit, timeout=timeout)
                rows = result.get("rows", [])
                
                if not rows:
                    logger.info("✅ No hay más suscriptores. Descarga completada.")
                    break
                
                # Procesar y guardar INMEDIATAMENTE en BD
                saved_count = store_subscribers_batch(rows)
                total_saved += saved_count
                
                offset += limit
                consecutive_errors = 0
                batch_saved = True
                
                logger.info(f"✅ Guardados {total_saved} suscriptores (offset: {offset}, lote: {len(rows)})")
                break  # Salir del loop de reintentos
                
            except PanaccessTimeoutError as e:
                retry_count += 1
                consecutive_errors += 1
                
                if retry_count >= MAX_RETRIES:
                    logger.error(f"❌ Timeout después de {MAX_RETRIES} reintentos en offset {offset}")
                    raise
                
                logger.warning(f"⏱️ Timeout en offset {offset} (intento {retry_count}/{MAX_RETRIES}), reintentando...")
                time.sleep(RETRY_DELAY * retry_count)  # Backoff exponencial
                
            except PanaccessSessionError as e:
                # Refrescar sesión y reintentar
                logger.warning(f"🔑 Sesión expirada en offset {offset}, refrescando...")
                panaccess = get_panaccess()
                panaccess.reset_session()
                panaccess.ensure_session()
                time.sleep(1)
                # No incrementar retry_count para errores de sesión
                
            except (OperationalError, DatabaseError) as e:
                if is_connection_error(e):
                    logger.warning(f"🔌 Conexión a BD perdida en offset {offset}. Reconectando...")
                    reconnect_database()
                    time.sleep(2)
                    # No incrementar retry_count, reintentar inmediatamente
                    continue
                else:
                    retry_count += 1
                    consecutive_errors += 1
                    
                    if retry_count >= MAX_RETRIES:
                        logger.error(f"❌ Error de BD después de {MAX_RETRIES} reintentos: {str(e)}")
                        raise
                    
                    logger.warning(f"⚠️ Error de BD en offset {offset} (intento {retry_count}/{MAX_RETRIES}): {str(e)}")
                    time.sleep(RETRY_DELAY * retry_count)
                    
            except PanaccessAPIError as e:
                # Manejar errores del servidor que pueden ser temporales
                if hasattr(e, 'error_code') and e.error_code == 'unknown_error_serverside':
                    retry_count += 1
                    consecutive_errors += 1
                    
                    if retry_count >= MAX_RETRIES:
                        logger.error(f"❌ Error del servidor después de {MAX_RETRIES} reintentos en offset {offset}")
                        raise
                    
                    logger.warning(
                        f"⚠️ Error del servidor en offset {offset} (intento {retry_count}/{MAX_RETRIES}): {str(e)}. "
                        f"Reintentando después de {RETRY_DELAY * retry_count}s..."
                    )
                    # Resetear sesión por si acaso el problema es con la sesión del servidor
                    panaccess = get_panaccess()
                    panaccess.reset_session()
                    panaccess.ensure_session()
                    time.sleep(RETRY_DELAY * retry_count)
                else:
                    # Para otros errores de API, no reintentar
                    logger.error(f"❌ Error de API no recuperable en offset {offset}: {str(e)}")
                    raise
                    
            except PanaccessException as e:
                retry_count += 1
                consecutive_errors += 1
                
                if retry_count >= MAX_RETRIES:
                    logger.error(f"❌ Error después de {MAX_RETRIES} reintentos: {str(e)}")
                    raise
                
                logger.warning(f"⚠️ Error en offset {offset} (intento {retry_count}/{MAX_RETRIES}): {str(e)}")
                time.sleep(RETRY_DELAY * retry_count)
        
        # Si no se pudo guardar el lote después de todos los reintentos, salir
        if not batch_saved:
            logger.error(f"❌ No se pudo procesar el lote en offset {offset} después de {MAX_RETRIES} intentos")
            break
        
        # Si hay muchos errores consecutivos, puede ser un problema mayor
        if consecutive_errors >= max_consecutive_errors:
            logger.error(f"❌ Demasiados errores consecutivos ({consecutive_errors}). Deteniendo descarga.")
            break
    
    logger.info(f"✅ Descarga completada. Total guardados: {total_saved} suscriptores")
    
    return {
        'total_saved': total_saved,
        'last_offset': offset
    }

def store_all_subscribers_in_chunks(data_batch, chunk_size=100):
    """
    Almacena suscriptores en la base de datos en bloques para mejorar el rendimiento.
    """
    total = len(data_batch)
    logger.info(f"Almacenando {total} suscriptores en chunks de {chunk_size}...")
    for i in range(0, total, chunk_size):
        chunk = data_batch[i:i + chunk_size]
        try:
            registros = [ListOfSubscriber(**item) for item in chunk]
            ListOfSubscriber.objects.bulk_create(registros, ignore_conflicts=True)
            logger.info(f"Chunk {i//chunk_size + 1}: insertados {len(registros)} suscriptores")
        except Exception as e:
            logger.error(f"Error insertando chunk desde {i} hasta {i+chunk_size}: {str(e)}")

def store_subscribers_batch(rows, chunk_size=100, max_db_retries=3):
    """
    Guarda un lote de suscriptores inmediatamente en la base de datos.
    Maneja reconexión automática en caso de pérdida de conexión.
    
    Args:
        rows: Lista de filas de suscriptores desde la API
        chunk_size: Tamaño del chunk para bulk_create
        max_db_retries: Número máximo de reintentos por errores de BD
    
    Returns:
        Número de suscriptores guardados exitosamente
    """
    if not rows:
        return 0
    
    total_saved = 0
    db_retry_count = 0
    
    while db_retry_count < max_db_retries:
        try:
            with transaction.atomic():
                # Validar y preparar registros
                registros = []
                for row in rows:
                    # Validar estructura
                    if not isinstance(row.get("cell"), list) or len(row.get("cell", [])) < 12:
                        logger.warning(f"Fila inválida omitida: {row.get('id', 'unknown')}")
                        continue
                    
                    cell = row["cell"]
                    try:
                        subscriber_data = {
                            "id": str(row.get("id")),
                            "code": cell[0] if len(cell) > 0 and cell[0] else None,
                            "lastName": cell[1] if len(cell) > 1 and cell[1] else None,
                            "firstName": cell[2] if len(cell) > 2 and cell[2] else None,
                            "smartcards": cell[3] if len(cell) > 3 and cell[3] else [],
                            "hcId": cell[4] if len(cell) > 4 and cell[4] else None,
                            "hcName": cell[5] if len(cell) > 5 and cell[5] else None,
                            "country": cell[6] if len(cell) > 6 and cell[6] else None,
                            "city": cell[7] if len(cell) > 7 and cell[7] else None,
                            "zip": cell[8] if len(cell) > 8 and cell[8] else None,
                            "address": cell[9] if len(cell) > 9 and cell[9] else None,
                            "created": cell[10] if len(cell) > 10 and cell[10] else None,
                            "modified": cell[11] if len(cell) > 11 and cell[11] else None,
                        }
                        registros.append(ListOfSubscriber(**subscriber_data))
                    except Exception as e:
                        logger.warning(f"Error creando objeto para código {cell[0] if len(cell) > 0 else 'unknown'}: {str(e)}")
                        continue
                
                if not registros:
                    return 0
                
                # Guardar en chunks
                for i in range(0, len(registros), chunk_size):
                    chunk = registros[i:i + chunk_size]
                    ListOfSubscriber.objects.bulk_create(chunk, ignore_conflicts=True)
                    total_saved += len(chunk)
                
                logger.debug(f"💾 Guardados {total_saved} suscriptores en BD")
                return total_saved  # Éxito, salir del loop de reintentos
                
        except (OperationalError, DatabaseError) as e:
            if is_connection_error(e):
                db_retry_count += 1
                logger.warning(f"🔌 Conexión a BD perdida (intento {db_retry_count}/{max_db_retries}). Reconectando...")
                reconnect_database()
                
                if db_retry_count < max_db_retries:
                    time.sleep(2 * db_retry_count)  # Backoff exponencial
                    continue
                else:
                    logger.error(f"❌ No se pudo reconectar a la BD después de {max_db_retries} intentos")
                    raise DatabaseError(f"No se pudo reconectar a la BD después de {max_db_retries} intentos: {str(e)}")
            else:
                logger.error(f"❌ Error de base de datos: {str(e)}")
                raise
                
        except Exception as e:
            logger.error(f"❌ Error guardando lote de suscriptores: {str(e)}")
            raise
    
    raise DatabaseError(f"No se pudo guardar el lote después de {max_db_retries} intentos de reconexión")

def download_subscribers_since_last(session_id=None, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Descarga suscriptores nuevos desde el último registrado (modo incremental).
    Guarda cada lote inmediatamente.

    FIX v2 (2026-09): el corte YA NO se decide por el 'code' más alto local.
    Se detectó en producción que los 'code' de Panaccess no tienen un formato
    uniforme: conviven códigos cortos (ej: '9', aparentemente datos viejos/de
    prueba) con códigos reales largos (ej: '00073420L16'). Al comparar como
    texto (que es como Django ordena un CharField), '9' resulta "mayor" que
    '00073420L16' -- aunque este último sea un suscriptor real dado de alta
    después. Con el criterio anterior (incluso ya con el fix v1 que seguía
    paginando después del corte), cualquier código real que empezara con un
    dígito del 0 al 8 quedaba "antes" del checkpoint y nunca se descargaba.

    Ahora el corte usa la fecha 'created' más reciente que ya tenemos en la
    tabla local, pidiendo el catálogo a Panaccess ordenado por 'created' DESC
    (los más nuevos primero) en vez de por 'code' (ver
    CallListSubscribersOrderedByCreated). En cuanto aparece una fila con
    'created' anterior a esa fecha, se asume que de ahí en adelante ya está
    todo sincronizado.

    Precisión: el campo 'created' del modelo es un DateField (solo día, sin
    hora) -- no hay 'created' con hora en este endpoint, a diferencia de Wind
    que sí tiene DateTimeField. Por eso se vuelve a procesar (upsert seguro,
    sin duplicar por 'id') todo lo que tenga la MISMA fecha que el corte, para
    no perder altas del mismo día ocurridas después de la corrida anterior.

    VALIDACIÓN DE SEGURIDAD: si la respuesta de Panaccess no viene realmente
    en orden descendente por 'created' (por ejemplo, porque el endpoint no
    soporta ese orderBy y lo ignora en silencio), se aborta esta corrida con
    un error explícito en el log en vez de asumir un orden que no existe --
    de lo contrario se podría marcar por error el fin de los nuevos y perder
    suscriptores otra vez. Si esto llegara a pasar, revisar el log
    'no viene ordenado por created DESC' y avisar para ajustar el criterio.

    Si no hay ningún 'created' local confiable para anclar el corte (tabla
    vacía de fechas, o recién migrada), se hace un fetch_all_subscribers()
    completo en vez de arriesgar un corte mal calculado.

    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos para cada llamada

    Returns:
        dict con 'total_saved', 'checkpoint_found' y, si algo salió mal,
        'order_error' (True si Panaccess no respetó el orden pedido).
    """
    logger.info("🔄 Iniciando descarga incremental de suscriptores desde Panaccess (por fecha de creación)...")

    last_created = ListOfSubscriber.objects.exclude(created__isnull=True).aggregate(
        latest=Max('created')
    )['latest']

    if last_created is None:
        logger.warning(
            "⚠️ No hay ninguna fecha 'created' local confiable para anclar el corte incremental "
            "(tabla vacía de fechas). Se hace un fetch_all_subscribers() completo."
        )
        return fetch_all_subscribers(session_id, limit, timeout=timeout)

    max_pages = PanaccessConfig.INCREMENTAL_SYNC_MAX_PAGES
    logger.info(f"🔍 Corte por fecha de creación: se guarda todo con created >= {last_created}")

    offset = 0
    total_saved = 0
    pages_scanned = 0
    reached_cutoff = False
    order_checked = False
    prev_created = None

    while True:
        retry_count = 0
        batch_processed = False
        rows = []

        while retry_count < MAX_RETRIES:
            try:
                result = CallListSubscribersOrderedByCreated(session_id, offset, limit, timeout=timeout)
                rows = result.get("rows", [])

                if not rows:
                    logger.info("✅ Fin del catálogo remoto.")
                    batch_processed = True
                    break

                batch_to_save = []
                order_error = False
                for row in rows:
                    if not isinstance(row.get("cell"), list) or len(row.get("cell", [])) < 12:
                        logger.warning(f"Fila inválida omitida: {row.get('id', 'unknown')}")
                        continue

                    cell = row["cell"]
                    row_created = _parse_created_value(cell[10] if len(cell) > 10 else None)

                    # Validación de orden: la lista debe venir en 'created' DESC.
                    # Si encontramos una fecha mayor que la anterior, Panaccess no
                    # está respetando el orderBy pedido -- abortamos en vez de
                    # asumir un corte que no es confiable.
                    if not order_checked:
                        order_checked = True
                    elif prev_created is not None and row_created is not None and row_created > prev_created:
                        logger.error(
                            f"❌ La respuesta de Panaccess no viene ordenada por 'created' DESC "
                            f"(fila con created={row_created} apareció después de otra con "
                            f"created={prev_created}). Se aborta la descarga incremental para no "
                            f"perder registros por un corte mal calculado -- revisar si el endpoint "
                            f"soporta orderBy=created."
                        )
                        order_error = True
                        break
                    if row_created is not None:
                        prev_created = row_created

                    if row_created is not None and row_created < last_created:
                        reached_cutoff = True
                        logger.info(
                            f"✅ Corte alcanzado en offset {offset}: fila con created={row_created} "
                            f"anterior a {last_created}. A partir de aquí ya está todo sincronizado."
                        )
                        break

                    batch_to_save.append(row)

                if order_error:
                    return {'total_saved': total_saved, 'checkpoint_found': False, 'order_error': True}

                if batch_to_save:
                    saved_count = store_subscribers_batch(batch_to_save)
                    total_saved += saved_count
                    logger.info(f"✅ Guardados {total_saved} suscriptores nuevos hasta ahora (offset: {offset})")

                batch_processed = True
                break  # Salir del loop de reintentos

            except (PanaccessTimeoutError, PanaccessSessionError) as e:
                retry_count += 1
                if retry_count >= MAX_RETRIES:
                    logger.error(f"❌ Error después de {MAX_RETRIES} reintentos: {str(e)}")
                    raise

                logger.warning(f"⚠️ Error en offset {offset} (intento {retry_count}/{MAX_RETRIES}): {str(e)}")
                if isinstance(e, PanaccessSessionError):
                    panaccess = get_panaccess()
                    panaccess.reset_session()
                    panaccess.ensure_session()
                time.sleep(RETRY_DELAY * retry_count)

            except PanaccessAPIError as e:
                # Manejar errores del servidor que pueden ser temporales
                if hasattr(e, 'error_code') and e.error_code == 'unknown_error_serverside':
                    retry_count += 1
                    if retry_count >= MAX_RETRIES:
                        logger.error(f"❌ Error del servidor después de {MAX_RETRIES} reintentos: {str(e)}")
                        raise

                    logger.warning(
                        f"⚠️ Error del servidor en offset {offset} (intento {retry_count}/{MAX_RETRIES}): {str(e)}. "
                        f"Reintentando después de {RETRY_DELAY * retry_count}s..."
                    )
                    # Resetear sesión por si acaso el problema es con la sesión del servidor
                    panaccess = get_panaccess()
                    panaccess.reset_session()
                    panaccess.ensure_session()
                    time.sleep(RETRY_DELAY * retry_count)
                else:
                    # Para otros errores de API, no reintentar
                    logger.error(f"❌ Error de API no recuperable: {str(e)}")
                    raise

        if not batch_processed:
            logger.error(f"❌ No se pudo procesar el lote en offset {offset}")
            break

        if not rows or reached_cutoff:
            break

        pages_scanned += 1
        if pages_scanned >= max_pages:
            logger.error(
                f"❌ Se alcanzó el tope de seguridad de {max_pages} páginas sin cruzar el corte por fecha "
                f"({last_created}). Se aborta esta corrida para no escanear el catálogo completo cada vez; "
                f"se recomienda revisar manualmente o ejecutar una sincronización completa."
            )
            return {'total_saved': total_saved, 'checkpoint_found': False}

        offset += limit

    logger.info(f"✅ Descarga incremental completada. Total guardados: {total_saved} suscriptores nuevos.")
    return {'total_saved': total_saved, 'checkpoint_found': True}

def _parse_created_value(value):
    """
    Convierte el valor crudo de 'created' que devuelve Panaccess (string) a un
    objeto date/datetime usable para comparar, reutilizando el mismo parser
    que usa el propio campo del modelo (django.db.models.DateField.to_python),
    así no hace falta agregar una dependencia nueva (python-dateutil) y el
    parseo queda consistente con lo que se guarda realmente en la BD.
    """
    if not value:
        return None
    try:
        return DateField().to_python(value)
    except Exception:
        logger.warning(f"⚠️ No se pudo interpretar la fecha 'created' recibida: {value!r}")
        return None

def compare_and_update_all_subscribers(session_id=None, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Compara todos los suscriptores de Panaccess con los de la base local y actualiza si hay diferencias.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos para cada llamada
    """
    logger.info("🔄 Comparando suscriptores de Panaccess con la base de datos...")
    local_data = {
        obj.code: obj for obj in ListOfSubscriber.objects.all() if obj.code
    }
    offset = 0
    total_updated = 0
    while True:
        try:
            response = CallListSubscribers(session_id, offset, limit, timeout=timeout)
        except (PanaccessTimeoutError, PanaccessSessionError) as e:
            logger.warning(f"⚠️ Error en offset {offset}: {str(e)}. Continuando...")
            offset += limit
            continue
        
        remote_list = response.get("rows", [])
        if not remote_list:
            break
        for row in remote_list:
            if not isinstance(row.get("cell"), list) or len(row.get("cell", [])) < 12:
                continue
            
            cell = row["cell"]
            code = cell[0] if len(cell) > 0 and cell[0] else None
            if not code or code not in local_data:
                continue
            
            remote = {
                "lastName": cell[1] if len(cell) > 1 and cell[1] else None,
                "firstName": cell[2] if len(cell) > 2 and cell[2] else None,
                "smartcards": cell[3] if len(cell) > 3 and cell[3] else [],
                "hcId": cell[4] if len(cell) > 4 and cell[4] else None,
                "hcName": cell[5] if len(cell) > 5 and cell[5] else None,
                "country": cell[6] if len(cell) > 6 and cell[6] else None,
                "city": cell[7] if len(cell) > 7 and cell[7] else None,
                "zip": cell[8] if len(cell) > 8 and cell[8] else None,
                "address": cell[9] if len(cell) > 9 and cell[9] else None,
                "created": cell[10] if len(cell) > 10 and cell[10] else None,
                "modified": cell[11] if len(cell) > 11 and cell[11] else None,
            }
            local_obj = local_data[code]
            changed_fields = []
            for key, val in remote.items():
                if hasattr(local_obj, key):
                    local_val = getattr(local_obj, key)
                    # Comparar valores, manejando None y listas
                    if isinstance(local_val, list) and isinstance(val, list):
                        if local_val != val:
                            setattr(local_obj, key, val)
                            changed_fields.append(key)
                    elif str(local_val) != str(val):
                        setattr(local_obj, key, val)
                        changed_fields.append(key)
            if changed_fields:
                try:
                    local_obj.save(update_fields=changed_fields)
                    total_updated += 1
                    logger.debug(f"Código {code} actualizado. Campos: {changed_fields}")
                except Exception as e:
                    logger.error(f"Error actualizando código {code}: {str(e)}")
        offset += limit
        logger.info(f"Procesados {offset} registros, {total_updated} actualizados hasta ahora...")
    logger.info(f"Actualización completa. Total modificados: {total_updated}")

def sync_subscribers(session_id=None, limit=100):
    """
    Ejecuta el proceso de sincronización de suscriptores:
    - Si la base está vacía, descarga todos los registros.
    - Si no, descarga solo los nuevos desde el último code.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
    
    Returns:
        Resultado de la sincronización
    """
    logger.info("Iniciando sincronización de suscriptores")

    try:
        if DataBaseEmpty():
            logger.info("Base vacía: descarga completa")
            return fetch_all_subscribers(session_id, limit)
        else:
            last = LastSubscriber()
            highest_code = last.code if last else None
            logger.info(f"Último código: {highest_code}")
            
            logger.info("Base existente: descarga incremental + actualización")
            # 1. Nuevos registros
            logger.info("Inicio de Descarga de suscriptores nuevos desde el último registrado")
            new_result = download_subscribers_since_last(session_id, limit)
            logger.info(f"Fin de Descarga de suscriptores nuevos completada.")
            
            # 2. Actualizar existentes
            logger.info("Inicio de Actualización de suscriptores existentes")
            compare_and_update_all_subscribers(session_id, limit)
            logger.info("Fin de Actualización de suscriptores existentes completada.")

            return new_result

    except PanaccessException as e:
        logger.error(f"Error de PanAccess durante sincronización: {str(e)}")
        raise
    except (ConnectionError, ValueError) as e:
        logger.error(f"Error específico durante sincronización: {str(e)}")
        raise
    except Exception as e:
        logger.error(f"Error inesperado: {str(e)}", exc_info=True)
        raise

def CallListSubscribers(session_id=None, offset=0, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Llama a la API de Panaccess para obtener la lista de suscriptores.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        offset: Índice de inicio para paginación
        limit: Cantidad máxima de registros a obtener
        timeout: Timeout en segundos (default: 30)
    
    Returns:
        Diccionario con la respuesta de PanAccess
    
    Raises:
        PanaccessTimeoutError: Si la llamada excede el timeout
        PanaccessSessionError: Si la sesión ha expirado
        PanaccessAPIError: Si hay un error en la API
    """
    timeout_msg = f"{timeout}s" if timeout else "sin límite"
    logger.info(f"📞 Llamando API Panaccess: offset={offset}, limit={limit} (timeout: {timeout_msg})")
    
    try:
        # Usar el singleton de PanAccess
        panaccess = get_panaccess()
        
        # Preparar parámetros
        parameters = {
            'offset': offset,
            'limit': limit,
            'orderDir': 'ASC',
            'orderBy': 'code'
        }
        
        # Hacer la llamada con timeout configurable
        response = panaccess.call('getListOfSubscribers', parameters, timeout=timeout)

        if response.get('success'):
            answer = response.get('answer', {})
            rows = answer.get('rows', [])
            logger.debug(f"✅ Respuesta recibida: {len(rows)} suscriptores")
            return answer
        else:
            error_message = response.get('errorMessage', 'Error desconocido al obtener suscriptores')
            error_code = response.get('errorCode', None)
            
            # Detectar errores de sesión
            if 'session' in error_message.lower() or 'logged' in error_message.lower():
                logger.error(f"🔑 Error de sesión: {error_message}")
                raise PanaccessSessionError(f"Sesión expirada o inválida: {error_message}")
            
            # Detectar errores del servidor que pueden ser temporales
            if error_code == 'unknown_error_serverside':
                logger.warning(f"⚠️ Error del servidor de PanAccess (puede ser temporal): {error_message}")
                # Crear excepción con el código de error para que pueda ser manejada específicamente
                raise PanaccessAPIError(error_message, error_code=error_code)
            
            logger.error(f"❌ Error en respuesta de PanAccess: {error_message} (código: {error_code})")
            raise PanaccessAPIError(error_message, error_code=error_code)

    except (PanaccessTimeoutError, PanaccessSessionError):
        # Re-lanzar excepciones específicas
        raise
    except PanaccessException:
        raise
    except Exception as e:
        logger.error(f"💥 Fallo en la llamada a getListOfSubscribers: {str(e)}", exc_info=True)
        raise PanaccessAPIError(f"Error inesperado: {str(e)}")

def CallListSubscribersOrderedByCreated(session_id=None, offset=0, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Igual que CallListSubscribers, pero pide el catálogo ordenado por
    'created' DESC en vez de por 'code' ASC.

    Se usa solo para download_subscribers_since_last(): el 'code' de
    Panaccess no sirve como indicador de antigüedad porque conviven formatos
    distintos (ver el docstring de esa función). El resto de las funciones
    (fetch_all_subscribers, compare_and_update_all_subscribers) siguen usando
    CallListSubscribers sin cambios, porque a ellas el orden no les importa:
    recorren el catálogo completo de punta a punta.
    """
    timeout_msg = f"{timeout}s" if timeout else "sin límite"
    logger.info(f"📞 Llamando API Panaccess (orden por created DESC): offset={offset}, limit={limit} (timeout: {timeout_msg})")

    try:
        panaccess = get_panaccess()

        parameters = {
            'offset': offset,
            'limit': limit,
            'orderDir': 'DESC',
            'orderBy': 'created'
        }

        response = panaccess.call('getListOfSubscribers', parameters, timeout=timeout)

        if response.get('success'):
            answer = response.get('answer', {})
            rows = answer.get('rows', [])
            logger.debug(f"✅ Respuesta recibida: {len(rows)} suscriptores")
            return answer
        else:
            error_message = response.get('errorMessage', 'Error desconocido al obtener suscriptores')
            error_code = response.get('errorCode', None)

            if 'session' in error_message.lower() or 'logged' in error_message.lower():
                logger.error(f"🔑 Error de sesión: {error_message}")
                raise PanaccessSessionError(f"Sesión expirada o inválida: {error_message}")

            if error_code == 'unknown_error_serverside':
                logger.warning(f"⚠️ Error del servidor de PanAccess (puede ser temporal): {error_message}")
                raise PanaccessAPIError(error_message, error_code=error_code)

            logger.error(f"❌ Error en respuesta de PanAccess: {error_message} (código: {error_code})")
            raise PanaccessAPIError(error_message, error_code=error_code)

    except (PanaccessTimeoutError, PanaccessSessionError):
        raise
    except PanaccessException:
        raise
    except Exception as e:
        logger.error(f"💥 Fallo en la llamada a getListOfSubscribers (orden created): {str(e)}", exc_info=True)
        raise PanaccessAPIError(f"Error inesperado: {str(e)}")
