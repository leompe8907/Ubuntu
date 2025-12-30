import logging
from django.db import transaction, connection
from django.db.utils import OperationalError, DatabaseError
from typing import Optional
from .singleton import get_panaccess
from .exceptions import PanaccessException, PanaccessAPIError
from ...models import ListOfSubscriber, SubscriberLoginInfo
from ...serializers import SubscriberLoginInfoSerializer
from ...utils.db_utils import is_connection_error, reconnect_database

logger = logging.getLogger(__name__)

def DataBaseEmpty():
    """
    Verifica si la tabla SubscriberLoginInfo está vacía.
    """
    logger.info("Verificando si la base de datos de logins de suscriptores está vacía...")
    return not SubscriberLoginInfo.objects.exists()

def LastSubscriberLoginInfo():
    """
    Retorna el último registro de login de suscriptor registrado en la base de datos según el campo 'subscriberCode'.
    """
    logger.info("Buscando el último login de suscriptor registrado en la base de datos...")
    try:
        # CORRECCIÓN: Usar 'subscriberCode' en lugar de 'subscriber_code'
        return SubscriberLoginInfo.objects.latest('subscriberCode')
    except SubscriberLoginInfo.DoesNotExist:
        logger.warning("No se encontró ningún login de suscriptor en la base de datos.")
        return None

def get_all_subscriber_codes():
    """
    Retorna un conjunto (set) con todos los códigos válidos de suscriptores 
    (no nulos ni vacíos) existentes en la tabla ListOfSubscriber.
    """
    logger.info("Obteniendo códigos válidos de suscriptores desde la base de datos...")

    raw_codes = ListOfSubscriber.objects.values_list('code', flat=True)
    codes = {code for code in raw_codes if code}

    logger.info(f"Total de códigos válidos obtenidos: {len(codes)}")
    return codes

def fetch_all_logins_from_panaccess(session_id=None):
    """
    Recorre todos los códigos de suscriptores y llama a CallSubscriberLoginInfo por cada uno.

    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)

    Returns:
        list: Lista de diccionarios con la información de login para cada suscriptor.
    """
    logger.info("Iniciando recorrido de códigos de suscriptores para obtener logins desde Panaccess...")
    subscriber_codes = get_all_subscriber_codes()
    results = []

    for code in subscriber_codes:
        login_info = CallSubscriberLoginInfo(session_id, code)
        if login_info:
            results.append(login_info)

    logger.info(f"Total de logins obtenidos correctamente: {len(results)}")
    return store_logins_to_db(results)

def store_logins_to_db(login_data_list):
    """
    Almacena la información de login de suscriptores en la base de datos.

    Args:
        login_data_list (list): Lista de diccionarios con datos de login y su 'subscriber_code'.
    """
    logger.info("Iniciando almacenamiento de logins en la base de datos...")
    saved_count = 0
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        try:
            with transaction.atomic():
                for login_data in login_data_list:
                    subscriber_code = login_data.get('subscriberCode')
                    if not subscriber_code:
                        logger.warning("Registro omitido: falta 'subscriberCode'.")
                        continue

                    try:
                        # CORRECCIÓN: Usar get_or_create para evitar duplicados
                        obj, created = SubscriberLoginInfo.objects.get_or_create(
                            subscriberCode=subscriber_code,
                            defaults={k: v for k, v in login_data.items() if k != 'subscriberCode'}
                        )
                        if created:
                            saved_count += 1
                            logger.debug(f"Nuevo registro creado para {subscriber_code}")
                        else:
                            logger.debug(f"Registro ya existe para {subscriber_code}")
                    except Exception as e:
                        logger.error(f"Error al guardar login de {subscriber_code}: {str(e)}")
            
            logger.info(f"Total de registros guardados correctamente: {saved_count}")
            return saved_count
            
        except (OperationalError, DatabaseError) as e:
            if is_connection_error(e):
                retry_count += 1
                logger.warning(f"Conexión perdida (intento {retry_count}/{max_retries}). Reconectando...")
                reconnect_database()
                if retry_count < max_retries:
                    import time
                    time.sleep(2 * retry_count)
                    continue
                else:
                    raise DatabaseError(f"No se pudo reconectar después de {max_retries} intentos")
            else:
                raise

def fetch_new_logins_from_panaccess(session_id=None):
    """
    Obtiene logins solo para nuevos suscriptores que no están aún en la base de datos.

    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)

    Returns:
        int: Número de registros guardados correctamente.
    """
    logger.info("Obteniendo logins de nuevos suscriptores desde Panaccess...")

    # Último registro guardado
    last_record = LastSubscriberLoginInfo()
    last_code = last_record.subscriberCode if last_record else None

    # Todos los códigos disponibles
    all_codes = sorted(get_all_subscriber_codes())

    # Filtrar códigos nuevos si hay un último código registrado
    if last_code:
        new_codes = [code for code in all_codes if code > last_code]
    else:
        new_codes = all_codes  # si no hay ningún registro previo, traer todos

    logger.info(f"Nuevos códigos de suscriptores detectados: {len(new_codes)}")

    # Filtrar códigos que ya existen en la BD
    existing_codes = set(
        SubscriberLoginInfo.objects.values_list('subscriberCode', flat=True)
    )
    new_codes = [code for code in new_codes if code not in existing_codes]
    logger.info(f"Códigos nuevos después de filtrar existentes: {len(new_codes)}")

    results = []
    for code in new_codes:
        login_info = CallSubscriberLoginInfo(session_id, code)
        if login_info:
            # Agregar el código manualmente si no viene en la respuesta
            login_info['subscriberCode'] = code
            results.append(login_info)

    logger.info(f"Total de nuevos logins obtenidos correctamente: {len(results)}")
    return store_logins_to_db(results)

def compare_and_update_all_existing(session_id=None):
    """
    Compara todos los registros de login de Panaccess con la BD y actualiza solo los campos
    que hayan cambiado. No crea nuevos registros.

    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
    """
    logger.info("Comparando logins de suscriptores de Panaccess con la base de datos...")

    # Obtener todos los registros existentes de la BD en memoria
    local_data = {
        obj.subscriberCode: obj for obj in SubscriberLoginInfo.objects.all()
        if obj.subscriberCode  # Solo los que tienen código válido
    }
    
    logger.info(f"Registros locales encontrados: {len(local_data)}")

    # Obtener todos los códigos de suscriptores válidos
    subscriber_codes = get_all_subscriber_codes()
    total_updated = 0
    total_processed = 0

    for subscriber_code in subscriber_codes:
        # Solo procesar si el código ya existe en la BD
        if subscriber_code not in local_data:
            continue
            
        try:
            # Obtener datos remotos de Panaccess
            remote_login = CallSubscriberLoginInfo(session_id, subscriber_code)
            if not remote_login:
                logger.warning(f"No se pudo obtener datos remotos para {subscriber_code}")
                continue

            total_processed += 1
            local_obj = local_data[subscriber_code]
            changed_fields = []

            # Comparar campo por campo
            for key, remote_value in remote_login.items():
                model_field = key
                
                if hasattr(local_obj, model_field):
                    local_value = getattr(local_obj, model_field)
                    
                    # Comparar valores, manejando None y listas
                    if isinstance(local_value, list) and isinstance(remote_value, list):
                        if local_value != remote_value:
                            setattr(local_obj, model_field, remote_value)
                            changed_fields.append(model_field)
                    elif str(local_value) != str(remote_value):
                        setattr(local_obj, model_field, remote_value)
                        changed_fields.append(model_field)

            # Guardar solo si hay cambios
            if changed_fields:
                try:
                    max_retries = 3
                    retry_count = 0
                    
                    while retry_count < max_retries:
                        try:
                            local_obj.save(update_fields=changed_fields)
                            total_updated += 1
                            logger.debug(f"Subscriber {subscriber_code} actualizado. Campos: {changed_fields}")
                            break  # Éxito
                        except (OperationalError, DatabaseError) as e:
                            if is_connection_error(e):
                                retry_count += 1
                                logger.warning(f"Conexión perdida al actualizar {subscriber_code} (intento {retry_count}/{max_retries})")
                                reconnect_database()
                                if retry_count < max_retries:
                                    import time
                                    time.sleep(2 * retry_count)
                                    continue
                                else:
                                    logger.error(f"No se pudo reconectar después de {max_retries} intentos para {subscriber_code}")
                                    break
                            else:
                                raise
                except Exception as e:
                    logger.error(f"Error actualizando subscriber {subscriber_code}: {str(e)}")
            else:
                logger.debug(f"Sin cambios para subscriber {subscriber_code}")

        except Exception as e:
            logger.error(f"Error procesando subscriber {subscriber_code}: {str(e)}")

    logger.info(f"Actualización completa. Total procesados: {total_processed}, Total actualizados: {total_updated}")
    return total_updated

def sync_subscriber_logins(session_id=None):
    """
    Sincroniza los logins de suscriptores desde Panaccess hacia la base de datos.

    - Si no hay registros en la base ⇒ trae todos.
    - Si ya hay registros           ⇒ trae solo los nuevos y actualiza existentes.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
    """
    logger.info("Iniciando sincronización de logins de suscriptores...")

    try:
        if DataBaseEmpty():
            logger.info("La base de datos está vacía. Obteniendo todos los logins...")
            return fetch_all_logins_from_panaccess(session_id)
        else:
            last = LastSubscriberLoginInfo()
            last_code = last.subscriberCode if last else None
            logger.info(f"Último código de suscriptor en la base de datos: {last_code}")

            # 1. Buscar nuevos logins desde Panaccess
            logger.info("Inicio de descarga de nuevos registros...")
            new_result = fetch_new_logins_from_panaccess(session_id)
            logger.info("Descarga de nuevos registros finalizada.")

            # 2. Comparar y actualizar los existentes
            logger.info("Comparando y actualizando registros existentes...")
            compare_and_update_all_existing(session_id)
            logger.info("Comparación y actualización de registros existentes finalizada.")
            
            return new_result

    except PanaccessException as e:
        logger.error(f"Error de PanAccess durante sincronización: {str(e)}")
        raise
    except ConnectionError as ce:
        logger.error(f"Error de conexión: {str(ce)}")
        raise
    except ValueError as ve:
        logger.error(f"Error de valor: {str(ve)}")
        raise
    except Exception as e:
        logger.error(f"Error inesperado durante la sincronización: {str(e)}", exc_info=True)
        raise

def CallSubscriberLoginInfo(session_id=None, subscriber_code=None):
    """
    Llama a la API de Panaccess para obtener las credenciales de los suscriptores.
    
    Args:
        session_id: ID de sesión (opcional, se usa el singleton si no se proporciona)
        subscriber_code: Código del suscriptor
        
    Returns:
        dict: La respuesta con la información de login
    """
    
    logger.info(f"Llamando API Panaccess para obtener credenciales de {subscriber_code} (sin timeout)")
    
    try:
        # Usar el singleton de PanAccess
        panaccess = get_panaccess()
        
        # Preparar parámetros
        parameters = {
            'subscriberCode': subscriber_code
        }
        
        # Hacer la llamada usando el singleton SIN timeout (None)
        # Esto permite que la llamada espere indefinidamente hasta que Panaccess responda
        response = panaccess.call('getSubscriberLoginInfo', parameters, timeout=None)

        if response.get('success'):
            result = response.get('answer', {})
            result['subscriberCode'] = subscriber_code
            return result
        else:
            error_msg = response.get('errorMessage', 'Error desconocido al obtener credenciales')
            logger.error(f"Error en API para {subscriber_code}: {error_msg}")
            return None
    except PanaccessException as e:
        logger.error(f"Error de PanAccess al obtener credenciales de {subscriber_code}: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado al obtener credenciales de {subscriber_code}: {str(e)}", exc_info=True)
        return None