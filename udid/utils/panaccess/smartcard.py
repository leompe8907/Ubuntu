import logging
import json
from django.db import transaction
from typing import Optional
from .singleton import get_panaccess
from .exceptions import PanaccessException, PanaccessAPIError
from ...models import ListOfSmartcards, ListOfSubscriber
from ...serializers import ListOfSmartcardsSerializer

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

def fetch_all_smartcards(session_id=None, limit=100, timeout=None):
    """
    Descarga todos los smartcards desde Panaccess y los almacena en la base de datos.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
        timeout: Timeout en segundos (ignorado, siempre usa timeout=None para sin límite)
    """
    logger.info("Iniciando descarga completa de smartcards desde Panaccess (sin timeout)...")
    offset = 0
    all_data = []
    
    while True:
        result = CallListSmartcards(session_id, offset, limit)
        smartcard_entries = result.get("smartcardEntries", [])
        if not smartcard_entries:
            break
        
        for entry in smartcard_entries:
            # Validar que entry tenga la estructura esperada
            if not isinstance(entry, dict) or 'sn' not in entry:
                logger.warning(f"Entrada con estructura inválida, se omite: {entry.get('sn', 'unknown')}")
                continue
            
            all_data.append(entry)
        
        offset += limit
        logger.info(f"Procesados {len(all_data)} smartcards hasta ahora...")
    
    logger.info(f"Total de smartcards descargados: {len(all_data)}")
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

def download_smartcards_since_last(session_id=None, limit=100):
    """
    Descarga smartcards nuevos desde el último registrado (modo incremental).
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
    """
    logger.info("Iniciando descarga incremental de smartcards desde Panaccess...")
    last = LastSmartcard()
    if not last:
        logger.warning("No hay smartcards registradas. Se recomienda usar descarga total.")
        return []
    
    highest_sn = last.sn
    logger.info(f"Buscando smartcards posteriores al SN: {highest_sn}")
    offset = 0
    new_data = []
    found = False
    
    while True:
        result = CallListSmartcards(session_id, offset, limit)
        smartcard_entries = result.get("smartcardEntries", [])
        if not smartcard_entries:
            break
        
        for entry in smartcard_entries:
            if not isinstance(entry, dict) or 'sn' not in entry:
                logger.warning(f"Entrada con estructura inválida, se omite: {entry.get('sn', 'unknown')}")
                continue
            
            sn = entry.get('sn')
            
            if sn == highest_sn:
                found = True
                logger.info(f"SN {highest_sn} encontrado. Fin de descarga incremental.")
                break
            
            new_data.append(entry)
        
        if found:
            break
        offset += limit
        logger.info(f"Procesados {len(new_data)} smartcards nuevos hasta ahora...")
    
    logger.info(f"Total de smartcards nuevos descargados: {len(new_data)}")
    return store_all_smartcards_in_chunks(new_data)

def compare_and_update_all_smartcards(session_id=None, limit=100):
    """
    Compara todos los smartcards de Panaccess con los de la base local y actualiza si hay diferencias.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
    """
    logger.info("Comparando smartcards de Panaccess con la base de datos...")
    local_data = {
        obj.sn: obj for obj in ListOfSmartcards.objects.all() if obj.sn
    }
    offset = 0
    total_updated = 0
    
    while True:
        response = CallListSmartcards(session_id, offset, limit)
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

def CallListSmartcards(session_id=None, offset=0, limit=100):
    """
    Llama a la API de Panaccess para obtener la lista de smartcards.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        offset: Índice de inicio para paginación
        limit: Cantidad máxima de registros a obtener
    
    Returns:
        Diccionario con la respuesta de PanAccess
    """
    logger.info(f"Llamando API Panaccess: offset={offset}, limit={limit} (sin timeout)")
    
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
        
        # Hacer la llamada usando el singleton SIN timeout (None)
        # Esto permite que la llamada espere indefinidamente hasta que Panaccess responda
        response = panaccess.call('getListOfSmartcards', parameters, timeout=None)

        if response.get('success'):
            return response.get('answer', {})
        else:
            error_message = response.get('errorMessage', 'Error desconocido al obtener smartcards')
            logger.error(f"Error en respuesta de PanAccess: {error_message}")
            raise PanaccessAPIError(error_message)

    except PanaccessException:
        raise
    except Exception as e:
        logger.error(f"Fallo en la llamada a getListOfSmartcards: {str(e)}", exc_info=True)
        raise


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