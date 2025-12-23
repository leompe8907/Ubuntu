import logging
from django.db import transaction
from typing import Optional
from .singleton import get_panaccess
from .exceptions import PanaccessException, PanaccessAPIError
from ...models import ListOfSubscriber
from ...serializers import ListOfSubscriberSerializer

logger = logging.getLogger(__name__)


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


def fetch_all_subscribers(session_id=None, limit=100):
    """
    Descarga todos los suscriptores desde Panaccess y los almacena en la base de datos.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
    """
    logger.info("Iniciando descarga completa de suscriptores desde Panaccess...")
    offset = 0
    all_data = []
    while True:
        result = CallListSubscribers(session_id, offset, limit)
        rows = result.get("rows", [])
        if not rows:
            break
        for row in rows:
            # Validar que row tenga la estructura esperada
            if not isinstance(row.get("cell"), list) or len(row.get("cell", [])) < 12:
                logger.warning(f"Fila con estructura inválida, se omite: {row.get('id', 'unknown')}")
                continue
            
            cell = row["cell"]
            all_data.append({
                "id": str(row.get("id")),  # Convertir a string para el primary key
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
            })
        offset += limit
        logger.info(f"Procesados {len(all_data)} suscriptores hasta ahora...")
    
    logger.info(f"Total de suscriptores descargados: {len(all_data)}")
    return store_all_subscribers_in_chunks(all_data)

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


def download_subscribers_since_last(session_id=None, limit=100):
    """
    Descarga suscriptores nuevos desde el último registrado (modo incremental).
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
    """
    logger.info("Iniciando descarga incremental de suscriptores desde Panaccess...")
    last = LastSubscriber()
    if not last:
        logger.warning("No hay suscriptores registrados. Se recomienda usar descarga total.")
        return []
    highest_code = last.code
    logger.info(f"Buscando suscriptores posteriores al código: {highest_code}")
    offset = 0
    new_data = []
    found = False
    while True:
        result = CallListSubscribers(session_id, offset, limit)
        rows = result.get("rows", [])
        if not rows:
            break
        for row in rows:
            if not isinstance(row.get("cell"), list) or len(row.get("cell", [])) < 12:
                logger.warning(f"Fila con estructura inválida, se omite: {row.get('id', 'unknown')}")
                continue
            
            cell = row["cell"]
            code = cell[0] if len(cell) > 0 and cell[0] else None
            
            if code == highest_code:
                found = True
                logger.info(f"Código {highest_code} encontrado. Fin de descarga incremental.")
                break
            
            new_data.append({
                "id": str(row.get("id")),
                "code": code,
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
            })
        if found:
            break
        offset += limit
        logger.info(f"Procesados {len(new_data)} suscriptores nuevos hasta ahora...")
    
    logger.info(f"Total de suscriptores nuevos descargados: {len(new_data)}")
    return store_all_subscribers_in_chunks(new_data)


def compare_and_update_all_subscribers(session_id=None, limit=100):
    """
    Compara todos los suscriptores de Panaccess con los de la base local y actualiza si hay diferencias.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        limit: Cantidad máxima de registros por página
    """
    logger.info("Comparando suscriptores de Panaccess con la base de datos...")
    local_data = {
        obj.code: obj for obj in ListOfSubscriber.objects.all() if obj.code
    }
    offset = 0
    total_updated = 0
    while True:
        response = CallListSubscribers(session_id, offset, limit)
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


def CallListSubscribers(session_id=None, offset=0, limit=100):
    """
    Llama a la API de Panaccess para obtener la lista de suscriptores.
    
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
            'orderBy': 'code'
        }
        
        # Hacer la llamada usando el singleton SIN timeout (None)
        # Esto permite que la llamada espere indefinidamente hasta que Panaccess responda
        response = panaccess.call('getListOfSubscribers', parameters, timeout=None)

        if response.get('success'):
            return response.get('answer', {})
        else:
            error_message = response.get('errorMessage', 'Error desconocido al obtener suscriptores')
            logger.error(f"Error en respuesta de PanAccess: {error_message}")
            raise PanaccessAPIError(error_message)

    except PanaccessException:
        raise
    except Exception as e:
        logger.error(f"Fallo en la llamada a getListOfSubscribers: {str(e)}", exc_info=True)
        raise