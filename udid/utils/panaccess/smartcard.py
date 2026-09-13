import logging
import json
import time
from django.db import transaction, connection
from django.db.utils import OperationalError, DatabaseError
from typing import Optional
from .singleton import get_panaccess
from .exceptions import (
    PanaccessException, 
    PanaccessAPIError, 
    PanaccessTimeoutError,
    PanaccessSessionError
)
from udid.models import ListOfSmartcards, ListOfSubscriber
from ...serializers import ListOfSmartcardsSerializer
from ...utils.db_utils import is_connection_error, reconnect_database

logger = logging.getLogger(__name__)

# Configuración de timeouts y reintentos
DEFAULT_TIMEOUT = 30  # 30 segundos (el servidor tiene timeout de ~20s)
MAX_RETRIES = 3
RETRY_DELAY = 2  # segundos entre reintentos


def DataBaseEmpty():
    """
    Verifica si la base de datos de smartcards está vacía.
    """
    logger.info("Verificando si la base de datos de smartcards está vacía...")
    return not ListOfSmartcards.objects.exists()

def LastSmartcard():
    """
    Retorna la última smartcard registrada según el número de serie (sn).
    """
    logger.info("Obteniendo la última smartcard registrada...")
    try:
        return ListOfSmartcards.objects.latest('sn')
    except ListOfSmartcards.DoesNotExist:
        logger.warning("No se encontraron smartcards en la base de datos.")
        return None

def fetch_all_smartcards(session_id=None, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Descarga todos los smartcards desde Panaccess y los almacena en la base de datos.
    
    Guarda cada lote inmediatamente para evitar pérdida de datos en caso de fallos.
    Implementa reintentos automáticos y manejo de timeouts.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos para cada llamada (default: 30)
    
    Returns:
        Dict con estadísticas de la descarga
    """
    logger.info(f"🔄 Iniciando descarga completa de smartcards (timeout: {timeout}s)...")
    
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
                result = CallListSmartcards(session_id, offset, limit, timeout=timeout)
                smartcard_entries = result.get("smartcardEntries", [])
                
                if not smartcard_entries:
                    logger.info("✅ No hay más smartcards. Descarga completada.")
                    break
                
                # Guardar INMEDIATAMENTE en BD
                saved_count = store_smartcards_batch(smartcard_entries)
                total_saved += saved_count
                
                offset += limit
                consecutive_errors = 0
                batch_saved = True
                
                logger.info(f"✅ Guardados {total_saved} smartcards (offset: {offset}, lote: {len(smartcard_entries)})")
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
                    # Otro error de BD, tratarlo como error general
                    retry_count += 1
                    consecutive_errors += 1
                    
                    if retry_count >= MAX_RETRIES:
                        logger.error(f"❌ Error de BD después de {MAX_RETRIES} reintentos: {str(e)}")
                        raise
                    
                    logger.warning(f"⚠️ Error de BD en offset {offset} (intento {retry_count}/{MAX_RETRIES}): {str(e)}")
                    time.sleep(RETRY_DELAY * retry_count)
                    
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
    
    logger.info(f"✅ Descarga completada. Total guardados: {total_saved} smartcards")
    
    return {
        'total_saved': total_saved,
        'last_offset': offset
    }

def store_all_smartcards_in_chunks(data_batch, chunk_size=100):
    """
    Inserta los registros en la base de datos en lotes para optimizar el rendimiento.

    Args:
        data_batch (List[Dict]): Lista de smartcards.
        chunk_size (int): Tamaño del lote a insertar en cada iteración.
    """
    total = len(data_batch)
    logger.info(f"Almacenando {total} smartcards en chunks de {chunk_size}...")

    for i in range(0, total, chunk_size):
        chunk = data_batch[i:i + chunk_size]
        try:
            registros = [ListOfSmartcards(**item) for item in chunk]
            ListOfSmartcards.objects.bulk_create(registros, ignore_conflicts=True)
            logger.info(f"Chunk {i//chunk_size + 1}: insertadas {len(registros)} smartcards.")
        except Exception as e:
            logger.error(f"Error al insertar chunk desde {i} hasta {i+chunk_size}: {str(e)}")

def store_smartcards_batch(smartcard_entries, chunk_size=100, max_db_retries=3):
    """
    Guarda un lote de smartcards inmediatamente en la base de datos.
    Maneja reconexión automática en caso de pérdida de conexión.
    
    Args:
        smartcard_entries: Lista de smartcards a guardar
        chunk_size: Tamaño del chunk para bulk_create
        max_db_retries: Número máximo de reintentos por errores de BD
    
    Returns:
        Número de smartcards guardadas exitosamente
    """
    if not smartcard_entries:
        return 0
    
    total_saved = 0
    db_retry_count = 0
    
    while db_retry_count < max_db_retries:
        try:
            with transaction.atomic():
                # Validar y preparar registros
                registros = []
                for entry in smartcard_entries:
                    if not isinstance(entry, dict) or 'sn' not in entry:
                        logger.warning(f"Entrada inválida omitida: {entry.get('sn', 'unknown')}")
                        continue
                    
                    try:
                        registros.append(ListOfSmartcards(**entry))
                    except Exception as e:
                        logger.warning(f"Error creando objeto para SN {entry.get('sn')}: {str(e)}")
                        continue
                
                if not registros:
                    return 0
                
                # Guardar en chunks
                for i in range(0, len(registros), chunk_size):
                    chunk = registros[i:i + chunk_size]
                    ListOfSmartcards.objects.bulk_create(chunk, ignore_conflicts=True)
                    total_saved += len(chunk)
                
                logger.debug(f"💾 Guardados {total_saved} smartcards en BD")
                return total_saved  # Éxito, salir del loop de reintentos
                
        except (OperationalError, DatabaseError) as e:
            if is_connection_error(e):
                db_retry_count += 1
                logger.warning(f"🔌 Conexión a BD perdida (intento {db_retry_count}/{max_db_retries}). Reconectando...")
                
                # Cerrar conexión actual para forzar reconexión
                reconnect_database()
                
                if db_retry_count < max_db_retries:
                    time.sleep(2 * db_retry_count)  # Backoff exponencial
                    continue
                else:
                    logger.error(f"❌ No se pudo reconectar a la BD después de {max_db_retries} intentos")
                    raise DatabaseError(f"No se pudo reconectar a la BD después de {max_db_retries} intentos: {str(e)}")
            else:
                # Otro error de BD, no reintentar
                logger.error(f"❌ Error de base de datos: {str(e)}")
                raise
                
        except Exception as e:
            logger.error(f"❌ Error guardando lote de smartcards: {str(e)}")
            raise
    
    # Si llegamos aquí, se agotaron los reintentos
    raise DatabaseError(f"No se pudo guardar el lote después de {max_db_retries} intentos de reconexión")

def download_smartcards_since_last(session_id=None, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Descarga smartcards nuevos desde el último registrado (modo incremental).
    Guarda cada lote inmediatamente.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos para cada llamada
    """
    logger.info("🔄 Iniciando descarga incremental de smartcards desde Panaccess...")
    last = LastSmartcard()
    if not last:
        logger.warning("⚠️ No hay smartcards registradas. Se recomienda usar descarga total.")
        return {'total_saved': 0}
    
    highest_sn = last.sn
    logger.info(f"🔍 Buscando smartcards posteriores al SN: {highest_sn}")
    offset = 0
    total_saved = 0
    found = False
    
    while True:
        retry_count = 0
        batch_processed = False
        
        while retry_count < MAX_RETRIES:
            try:
                result = CallListSmartcards(session_id, offset, limit, timeout=timeout)
                smartcard_entries = result.get("smartcardEntries", [])
                
                if not smartcard_entries:
                    logger.info("✅ No hay más smartcards nuevos.")
                    break
                
                # Procesar y guardar inmediatamente
                batch_to_save = []
                for entry in smartcard_entries:
                    if not isinstance(entry, dict) or 'sn' not in entry:
                        logger.warning(f"Entrada inválida omitida: {entry.get('sn', 'unknown')}")
                        continue
                    
                    sn = entry.get('sn')
                    
                    if sn == highest_sn:
                        found = True
                        logger.info(f"✅ SN {highest_sn} encontrado. Fin de descarga incremental.")
                        break
                    
                    batch_to_save.append(entry)
                
                # Guardar lote inmediatamente
                if batch_to_save:
                    saved_count = store_smartcards_batch(batch_to_save)
                    total_saved += saved_count
                    logger.info(f"✅ Guardados {total_saved} smartcards nuevos (offset: {offset})")
                
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
        
        if not batch_processed:
            logger.error(f"❌ No se pudo procesar el lote en offset {offset}")
            break
        
        if found or not smartcard_entries:
            break
        
        offset += limit
    
    logger.info(f"✅ Descarga incremental completada. Total guardados: {total_saved} smartcards nuevos")
    return {'total_saved': total_saved}

def compare_and_update_all_smartcards(session_id=None, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Compara todos los smartcards de Panaccess con los de la base local y actualiza si hay diferencias.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos para cada llamada
    """
    logger.info("🔄 Comparando smartcards de Panaccess con la base de datos...")
    local_data = {
        obj.sn: obj for obj in ListOfSmartcards.objects.all() if obj.sn
    }
    offset = 0
    total_updated = 0
    
    while True:
        try:
            response = CallListSmartcards(session_id, offset, limit, timeout=timeout)
        except (PanaccessTimeoutError, PanaccessSessionError) as e:
            logger.warning(f"⚠️ Error en offset {offset}: {str(e)}. Continuando...")
            offset += limit
            continue
        
        remote_list = response.get("smartcardEntries", [])
        if not remote_list:
            break
        
        for remote in remote_list:
            if not isinstance(remote, dict) or 'sn' not in remote:
                continue
            
            sn = remote.get('sn')
            if not sn or sn not in local_data:
                continue
            
            local_obj = local_data[sn]
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
                    logger.debug(f"SN {sn} actualizado. Campos: {changed_fields}")
                except Exception as e:
                    logger.error(f"Error actualizando SN {sn}: {str(e)}")
        
        offset += limit
        logger.info(f"Procesados {offset} registros, {total_updated} actualizados hasta ahora...")
    
    logger.info(f"Actualización completa. Total modificados: {total_updated}")

def sync_new_smartcards_only(session_id=None, limit=100):
    """
    Igual que sync_smartcards() pero SIN el paso de comparar/actualizar todas
    las smartcards existentes (compare_and_update_all_smartcards). Ese paso
    pagina TODO el catálogo de Panaccess y, con catálogos grandes, puede
    tardar mucho más de 5 minutos (en producción se vio una sola corrida
    seguir corriendo más de 10 minutos, bloqueando los 2 ciclos siguientes
    vía el lock global de tareas y sin siquiera llegar a completar).

    Pensada para llamarse desde tareas de alta frecuencia (cada 5 min) donde
    solo importa detectar smartcards NUEVAS rápido (para que suscriptores
    -nuevos o existentes- que reciben una tarjeta recién habiliten
    credenciales pronto). La comparación completa (que además detecta
    reasignaciones de tarjetas ya existentes a otro suscriptor) se sigue
    corriendo en check_and_sync_smartcards_monthly y en
    validate_and_sync_all_data_daily, que sí toleran ese costo por su baja
    frecuencia.

    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página

    Returns:
        Resultado de la descarga (nuevo o completo si la base estaba vacía)
    """
    logger.info("Iniciando sincronización incremental de smartcards (solo nuevas)")

    try:
        if DataBaseEmpty():
            logger.info("Base vacía: descarga completa")
            return fetch_all_smartcards(session_id, limit)
        else:
            last = LastSmartcard()
            logger.info(f"Último SN: {last.sn if last else None}")
            logger.info("Descargando smartcards nuevas desde el último registrado...")
            new_result = download_smartcards_since_last(session_id, limit)
            logger.info("Descarga de smartcards nuevas completada.")
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


def sync_smartcards(session_id=None, limit=100):
    """
    Ejecuta el proceso de sincronización de smartcards:
    - Si la base está vacía, descarga todos los registros.
    - Si no, descarga solo los nuevos desde el último sn.

    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página

    Returns:
        Resultado de la sincronización
    """
    logger.info("Iniciando sincronización de smartcards")

    try:
        if DataBaseEmpty():
            logger.info("Base vacía: descarga completa")
            return fetch_all_smartcards(session_id, limit)
        else:
            last = LastSmartcard()
            highest_sn = last.sn if last else None
            logger.info(f"Último SN: {highest_sn}")

            logger.info("Base existente: descarga incremental + actualización")
            # 1. Nuevos registros
            logger.info("Inicio de Descarga de smartcards nuevos desde el último registrado")
            new_result = download_smartcards_since_last(session_id, limit)
            logger.info(f"Fin de Descarga de smartcards nuevos completada.")

            # 2. Actualizar existentes
            logger.info("Inicio de Actualización de smartcards existentes")
            compare_and_update_all_smartcards(session_id, limit)
            logger.info("Fin de Actualización de smartcards existentes completada.")

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

def CallListSmartcards(session_id=None, offset=0, limit=100, timeout=DEFAULT_TIMEOUT):
    """
    Llama a la API de Panaccess para obtener la lista de smartcards.
    
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
            'orderBy': 'sn'
        }
        
        # Hacer la llamada con timeout configurable
        response = panaccess.call('getListOfSmartcards', parameters, timeout=timeout)

        if response.get('success'):
            answer = response.get('answer', {})
            count = answer.get('count', 0)
            entries = answer.get('smartcardEntries', [])
            logger.debug(f"✅ Respuesta recibida: {len(entries)} smartcards (total: {count})")
            return answer
        else:
            error_message = response.get('errorMessage', 'Error desconocido al obtener smartcards')
            
            # Detectar errores de sesión
            if 'session' in error_message.lower() or 'logged' in error_message.lower():
                logger.error(f"🔑 Error de sesión: {error_message}")
                raise PanaccessSessionError(f"Sesión expirada o inválida: {error_message}")
            
            logger.error(f"❌ Error en respuesta de PanAccess: {error_message}")
            raise PanaccessAPIError(error_message)

    except (PanaccessTimeoutError, PanaccessSessionError):
        # Re-lanzar excepciones específicas
        raise
    except PanaccessException:
        raise
    except Exception as e:
        logger.error(f"💥 Fallo en la llamada a getListOfSmartcards: {str(e)}", exc_info=True)
        raise PanaccessAPIError(f"Error inesperado: {str(e)}")

def extract_sns_from_smartcards_field(smartcards_data):
    """
    Extrae los números de serie (SN) del campo smartcards de un suscriptor.
    Maneja diferentes formatos posibles del JSON.
    
    Args:
        smartcards_data: Datos del campo smartcards (puede ser lista, dict, string, etc.)
    
    Returns:
        list: Lista de SNs (strings) extraídas
    """
    if not smartcards_data:
        return []
    
    sns = []
    
    # Si es una lista
    if isinstance(smartcards_data, list):
        for item in smartcards_data:
            if isinstance(item, str):
                # Lista de strings (SNs directos)
                sns.append(item.strip())
            elif isinstance(item, dict):
                # Lista de objetos, buscar 'sn' o 'serialNumber' o similar
                sn = item.get('sn') or item.get('serialNumber') or item.get('serial_number') or item.get('SN')
                if sn:
                    sns.append(str(sn).strip())
    
    # Si es un diccionario
    elif isinstance(smartcards_data, dict):
        # Puede tener SNs como keys o en un campo 'sn' o 'sns'
        if 'sn' in smartcards_data:
            sn_value = smartcards_data['sn']
            if isinstance(sn_value, list):
                sns.extend([str(s).strip() for s in sn_value])
            else:
                sns.append(str(sn_value).strip())
        elif 'sns' in smartcards_data:
            sn_value = smartcards_data['sns']
            if isinstance(sn_value, list):
                sns.extend([str(s).strip() for s in sn_value])
            else:
                sns.append(str(sn_value).strip())
        else:
            # Asumir que las keys son los SNs
            sns.extend([str(k).strip() for k in smartcards_data.keys() if k])
    
    # Si es un string, intentar parsearlo como JSON
    elif isinstance(smartcards_data, str):
        try:
            parsed = json.loads(smartcards_data)
            return extract_sns_from_smartcards_field(parsed)
        except (json.JSONDecodeError, ValueError):
            # Si no es JSON válido, asumir que es un SN directo
            sns.append(smartcards_data.strip())
    
    # Filtrar SNs vacíos y duplicados
    return list(set([sn for sn in sns if sn]))

def update_smartcards_from_subscribers():
    """
    Actualiza la tabla ListOfSmartcards con información de los suscriptores.
    Toma los suscriptores, extrae las SNs del campo smartcards (JSON),
    y actualiza/crea los registros en ListOfSmartcards con:
    - subscriberCode del suscriptor
    - lastName, firstName del suscriptor
    - hcId del suscriptor
    - Y otros campos relevantes del suscriptor
    
    Returns:
        dict: Resultado con estadísticas de la actualización
    """
    logger.info("[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Iniciando actualización de smartcards desde suscriptores")
    
    result = {
        'total_subscribers_processed': 0,
        'total_sns_found': 0,
        'total_smartcards_created': 0,
        'total_smartcards_updated': 0,
        'total_errors': 0
    }
    
    try:
        # Obtener todos los suscriptores
        subscribers = ListOfSubscriber.objects.all()
        total_subscribers = subscribers.count()
        logger.info(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Procesando {total_subscribers} suscriptores")
        
        # Obtener todas las smartcards existentes en memoria para comparación rápida
        existing_smartcards = {
            obj.sn: obj for obj in ListOfSmartcards.objects.all() if obj.sn
        }
        logger.info(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] {len(existing_smartcards)} smartcards existentes en BD")
        
        with transaction.atomic():
            for subscriber in subscribers:
                if not subscriber.code:
                    continue
                
                result['total_subscribers_processed'] += 1
                
                try:
                    # Extraer SNs del campo smartcards
                    smartcards_data = subscriber.smartcards
                    sns = extract_sns_from_smartcards_field(smartcards_data)
                    
                    if not sns:
                        logger.debug(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] No se encontraron SNs para suscriptor {subscriber.code}")
                        continue
                    
                    result['total_sns_found'] += len(sns)
                    logger.debug(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Suscriptor {subscriber.code}: {len(sns)} SNs encontradas")
                    
                    # Para cada SN, actualizar o crear el registro en ListOfSmartcards
                    for sn in sns:
                        try:
                            # Verificar si la smartcard ya existe
                            if sn in existing_smartcards:
                                smartcard = existing_smartcards[sn]
                                changed_fields = []
                                
                                # Actualizar campos del suscriptor si han cambiado
                                if str(smartcard.subscriberCode) != str(subscriber.code):
                                    smartcard.subscriberCode = subscriber.code
                                    changed_fields.append('subscriberCode')
                                
                                if str(smartcard.lastName) != str(subscriber.lastName):
                                    smartcard.lastName = subscriber.lastName
                                    changed_fields.append('lastName')
                                
                                if str(smartcard.firstName) != str(subscriber.firstName):
                                    smartcard.firstName = subscriber.firstName
                                    changed_fields.append('firstName')
                                
                                if str(smartcard.hcId) != str(subscriber.hcId):
                                    smartcard.hcId = subscriber.hcId
                                    changed_fields.append('hcId')
                                
                                # Guardar solo si hay cambios
                                if changed_fields:
                                    smartcard.save(update_fields=changed_fields)
                                    result['total_smartcards_updated'] += 1
                                    logger.debug(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] SN {sn} actualizada. Campos: {changed_fields}")
                            else:
                                # Crear nueva smartcard
                                smartcard = ListOfSmartcards.objects.create(
                                    sn=sn,
                                    subscriberCode=subscriber.code,
                                    lastName=subscriber.lastName,
                                    firstName=subscriber.firstName,
                                    hcId=subscriber.hcId
                                )
                                existing_smartcards[sn] = smartcard  # Agregar al cache
                                result['total_smartcards_created'] += 1
                                logger.debug(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] SN {sn} creada para suscriptor {subscriber.code}")
                        
                        except Exception as e:
                            result['total_errors'] += 1
                            logger.error(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Error procesando SN {sn} del suscriptor {subscriber.code}: {str(e)}")
                
                except Exception as e:
                    result['total_errors'] += 1
                    logger.error(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Error procesando suscriptor {subscriber.code}: {str(e)}")
        
        logger.info(
            f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Actualización completada. "
            f"Procesados: {result['total_subscribers_processed']}, "
            f"SNs encontradas: {result['total_sns_found']}, "
            f"Creadas: {result['total_smartcards_created']}, "
            f"Actualizadas: {result['total_smartcards_updated']}, "
            f"Errores: {result['total_errors']}"
        )
    
    except Exception as e:
        logger.error(f"[UPDATE_SMARTCARDS_FROM_SUBSCRIBERS] Error inesperado: {str(e)}", exc_info=True)
        result['error'] = str(e)
    
    return result