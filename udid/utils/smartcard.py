import logging
from django.db import transaction
from .auth import CVClient
from ..models import ListOfSmartcards
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

def CallListSmartcards(session_id, offset=0, limit=100):
    """
    Llama a la función remota getListOfSmartcards del API Panaccess.
    """
    logger.info(f"Llamando a Panaccess API: offset={offset}, limit={limit}")
    client = CVClient()
    client.session_id = session_id

    try:
        response = client.call('getListOfSmartcards', {
            'offset': offset,
            'limit': limit,
            'orderDir': 'ASC',
            'orderBy': 'sn'
        })

        if response.get('success'):
            return response.get('answer', {})
        else:
            raise Exception(response.get('errorMessage', 'Error desconocido'))

    except Exception as e:
        logger.error(f"Fallo al obtener smartcards: {str(e)}")
        raise