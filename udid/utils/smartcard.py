import logging
import time
import json
from django.db import transaction
from .auth import CVClient
from ..models import ListOfSmartcards, ListOfSubscriber
from ..serializers import ListOfSmartcardsSerializer

logger = logging.getLogger(__name__)


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

def fetch_all_smartcards(session_id, limit=100):
    """
    Descarga todos los registros de smartcards desde Panaccess.
    """
    logger.info("Descargando todos los registros de smartcards desde Panaccess...")
    offset = 0
    all_data = []

    while True:
        result = CallListSmartcards(session_id, offset, limit)
        rows = result.get("smartcardEntries", [])
        if not rows:
            break
        all_data.extend(rows)
        logger.info(f"Offset {offset}: {len(rows)} registros obtenidos")
        offset += limit

    return store_all_smartcards_in_chunks(all_data)

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

def download_smartcards_since_last(session_id, limit=100):
    """
    Descarga registros de smartcards desde Panaccess, a partir del último SN conocido.

    Args:
        session_id (str): ID de sesión de Panaccess.
        limit (int): Cantidad de registros por página.

    Returns:
        List[Dict]: Lista de smartcards nuevas (posteriores al último SN).
    """
    last = LastSmartcard()
    if not last:
        logger.warning("No hay smartcards registradas. Se recomienda usar descarga total.")
        return []

    highest_sn = last.sn
    logger.info(f"Buscando smartcards posteriores a SN: {highest_sn}")
    
    offset = 0
    new_data = []
    found = False

    while True:
        result = CallListSmartcards(session_id, offset, limit)
        rows = result.get("smartcardEntries", [])
        if not rows:
            break

        for row in rows:
            sn = row.get('sn')
            if sn == highest_sn:
                found = True
                logger.info(f"SN {highest_sn} encontrado. Fin de descarga.")
                break
            new_data.append(row)

        if found:
            break

        offset += limit

    logger.info(f"Descarga incremental: {len(new_data)} registros nuevos encontrados.")
    return store_all_smartcards_in_chunks(new_data)

def compare_and_update_all_existing(session_id, limit=100):
    """
    Compara todos los registros de Panaccess con la BD y actualiza solo los campos
    que hayan cambiado. No crea nuevos registros.

    Args:
        session_id (str): ID de sesión activo.
        limit (int): Tamaño del lote para la descarga paginada.
    """
    logger.info("Comparando smartcards de Panaccess con la base de datos...")

    # Obtener todos los registros existentes de la BD en memoria
    local_data = {
        obj.sn: obj for obj in ListOfSmartcards.objects.all()
    }

    offset = 0
    total_updated = 0

    while True:
        response = CallListSmartcards(session_id, offset, limit)
        remote_cards = response.get("smartcardEntries", [])
        if not remote_cards:
            break

        for remote in remote_cards:
            sn = remote.get("sn")
            if not sn or sn not in local_data:
                continue  # Solo trabajamos con registros ya existentes

            local_obj = local_data[sn]
            changed_fields = []

            for key, val in remote.items():
                if hasattr(local_obj, key):
                    local_val = getattr(local_obj, key)
                    if str(local_val) != str(val):
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

    logger.info(f"Actualización completa. Total de smartcards modificadas: {total_updated}")

def sync_smartcards(session_id, limit=100):
    """
    Sincroniza automáticamente las smartcards:
    - Si la base está vacía: descarga todos los registros.
    - Si ya existen registros: descarga nuevos y actualiza cambios.
    """
    logger.info("Sincronización iniciada en modo automático")

    try:

        if DataBaseEmpty():
            logger.info("Base de datos vacía: descargando todo")
            return fetch_all_smartcards(session_id, limit)
        else:
            last = LastSmartcard()
            highest_sn = last.sn if last else None
            logger.info(f"Base existente: buscando nuevos desde SN {highest_sn} y actualizando cambios")
            
            #*1. Buscar nuevos registros
            logger.info("Inicio de Descargando smartcards nuevas desde Panaccess...")
            new_result = download_smartcards_since_last(session_id, limit)
            logger.info(f"Fin descarga de smartcards nuevas completada.")
            
            #*2. Actualizar registros existentes
            logger.info("Inicio de Actualizan de smartcards existentes...")
            compare_and_update_all_existing(session_id, limit)
            logger.info("Fin de actualización de smartcards existentes.")
            
            return new_result

    except ConnectionError as ce:
        logger.error(f"Error de conexión: {str(ce)}")
        raise
    except ValueError as ve:
        logger.error(f"Error de valor: {str(ve)}")
        raise
    except Exception as e:
        logger.error(f"Error inesperado durante la sincronización: {str(e)}")
        raise

def CallListSmartcards(session_id, offset=0, limit=100, max_retries=3, retry_delay=5):
    """
    Llama a la función remota getListOfSmartcards del API Panaccess.
    Implementa reintentos automáticos con backoff exponencial en caso de errores de conexión.
    
    Args:
        session_id: ID de sesión de Panaccess
        offset: Offset para la paginación
        limit: Límite de registros por página
        max_retries: Número máximo de reintentos (default: 3)
        retry_delay: Tiempo inicial de espera entre reintentos en segundos (default: 5)
    
    Returns:
        Dict con la respuesta de la API
        
    Raises:
        Exception: Si todos los reintentos fallan
    """
    logger.info(f"Llamando a Panaccess API: offset={offset}, limit={limit}")
    client = CVClient()
    client.session_id = session_id

    last_exception = None
    
    for attempt in range(max_retries):
        try:
            # Timeout más largo para operaciones que pueden tardar
            timeout = 60 if attempt == 0 else 90  # Timeout más largo en reintentos
            response = client.call('getListOfSmartcards', {
                'offset': offset,
                'limit': limit,
                'orderDir': 'ASC',
                'orderBy': 'sn'
            }, timeout=timeout)

            if response.get('success'):
                return response.get('answer', {})
            else:
                # Intentar obtener el mensaje de error de diferentes campos posibles
                error_msg = (
                    response.get('errorMessage') or 
                    response.get('error') or 
                    response.get('message') or
                    f"Error desconocido. Respuesta completa: {response}"
                )
                
                # Si es un error de timeout o conexión, intentar reintentar
                error_str = str(error_msg).lower()
                if 'timeout' in error_str or 'connection' in error_str or 'connect' in error_str:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)  # Backoff exponencial
                        logger.warning(
                            f"Error de conexión/timeout en intento {attempt + 1}/{max_retries}. "
                            f"Reintentando en {wait_time} segundos... Error: {error_msg}"
                        )
                        time.sleep(wait_time)
                        continue
                
                # Si no es un error de conexión o ya agotamos los reintentos, lanzar excepción
                raise Exception(error_msg)

        except Exception as e:
            last_exception = e
            error_str = str(e).lower()
            
            # Verificar si es un error de conexión/timeout que podemos reintentar
            if ('timeout' in error_str or 'connection' in error_str or 'connect' in error_str) and attempt < max_retries - 1:
                wait_time = retry_delay * (2 ** attempt)  # Backoff exponencial
                logger.warning(
                    f"Error de conexión/timeout en intento {attempt + 1}/{max_retries} "
                    f"(offset={offset}). Reintentando en {wait_time} segundos... Error: {str(e)}"
                )
                time.sleep(wait_time)
                continue
            else:
                # Si no es un error de conexión o ya agotamos los reintentos, lanzar excepción
                logger.error(f"Fallo al obtener smartcards (offset={offset}): {str(e)}")
                raise
    
    # Si llegamos aquí, todos los reintentos fallaron
    logger.error(
        f"Fallo al obtener smartcards después de {max_retries} intentos "
        f"(offset={offset}): {str(last_exception)}"
    )
    raise Exception(f"Error después de {max_retries} intentos: {str(last_exception)}")


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