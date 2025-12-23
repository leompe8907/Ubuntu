from ...models import ListOfSubscriber, ListOfSmartcards, SubscriberLoginInfo, SubscriberInfo
from django.db import transaction
import logging

logger = logging.getLogger(__name__)

def get_all_subscriber_codes():
    """
    Retorna un set de todos los códigos únicos de suscriptores en la base de datos.
    """
    logger.info("[get_all_subscriber_codes] Obteniendo códigos únicos de suscriptores...")
    codes = set(ListOfSubscriber.objects.values_list('code', flat=True).exclude(code__isnull=True).exclude(code=''))
    logger.info(f"[get_all_subscriber_codes] Total encontrados: {len(codes)}")
    return codes

def get_smartcard_data(subscriber_code):
    """
    Busca todos los datos de smartcards para un suscriptor por código.

    Args:
        subscriber_code (str): Código del suscriptor

    Returns:
        list: Lista de diccionarios con datos de smartcard.
    """
    try:
        smartcards = ListOfSmartcards.objects.filter(subscriberCode=subscriber_code)
        if not smartcards.exists():
            logger.warning(f"[get_smartcard_data] No se encontraron smartcards para {subscriber_code}")
            return []

        logger.info(f"[get_smartcard_data] {smartcards.count()} smartcards encontradas para {subscriber_code}")

        result = []
        for sc in smartcards:
            # Validar que tenga SN (campo requerido)
            if not sc.sn:
                logger.warning(f"[get_smartcard_data] Smartcard sin SN, se omite: {sc.id}")
                continue
            
            result.append({
                'sn': sc.sn,
                'pin': sc.pin,
                'first_name': sc.firstName,
                'last_name': sc.lastName,
                'lastActivation': sc.lastActivation,
                'lastContact': sc.lastContact,
                'lastServiceListDownload': sc.lastServiceListDownload,
                'lastActivationIP': sc.lastActivationIP,
                'lastApiKeyId': sc.lastApiKeyId,
                'products': sc.products if sc.products else [],
                'packages': sc.packages if sc.packages else [],
                'packageNames': sc.packageNames if sc.packageNames else [],
                'model': sc.model
            })
        
        return result

    except Exception as e:
        logger.error(f"[get_smartcard_data] Error inesperado para {subscriber_code}: {str(e)}", exc_info=True)
        return []

def get_login_data(subscriber_code):
    """
    Busca las credenciales de login para un suscriptor por código.

    Args:
        subscriber_code (str): Código del suscriptor

    Returns:
        dict: Diccionario con login1, login2 y password o vacío si no se encuentra.
    """
    try:
        login = SubscriberLoginInfo.objects.get(subscriberCode=subscriber_code)
        logger.info(f"[get_login_data] Login encontrado para {subscriber_code}")
        return {
            'login1': login.login1,
            'login2': login.login2,
            'password': login.password
        }
    except SubscriberLoginInfo.DoesNotExist:
        logger.warning(f"[get_login_data] No se encontró login para {subscriber_code}")
        return {}

def subscriber_info_empty():
    """
    Verifica si la tabla SubscriberInfo está vacía.
    """
    empty = not SubscriberInfo.objects.exists()
    logger.info(f"[subscriber_info_empty] ¿Base vacía? {empty}")
    return empty

def last_subscriber_info():
    """
    Retorna el último registro de SubscriberInfo basado en subscriber_code.
    """
    try:
        last = SubscriberInfo.objects.latest('subscriber_code')
        logger.info(f"[last_subscriber_info] Último código encontrado: {last.subscriber_code}")
        return last
    except SubscriberInfo.DoesNotExist:
        logger.warning("[last_subscriber_info] No hay registros en SubscriberInfo.")
        return None

def merge_subscriber_data(subscriber_code):
    """
    Fusiona datos de smartcard y login en la tabla SubscriberInfo.
    Se crea un registro por cada SN encontrado con el mismo subscriber_code.
    """
    logger.info(f"[merge_subscriber_data] Iniciando consolidación para {subscriber_code}")

    try:
        smartcard_data_list = get_smartcard_data(subscriber_code)
        login_data = get_login_data(subscriber_code)

        if not smartcard_data_list:
            logger.warning(f"[merge_subscriber_data] No hay datos de smartcard para {subscriber_code}")
            return

        if not login_data:
            logger.warning(f"[merge_subscriber_data] No hay datos de login para {subscriber_code}")
            return

        # Crear un registro por cada SN
        with transaction.atomic():
            logger.info(f"[merge_subscriber_data] Procesando {len(smartcard_data_list)} smartcards para {subscriber_code}")
            for smartcard_data in smartcard_data_list:
                sn = smartcard_data.get('sn')
                if not sn:
                    continue

                obj, created = SubscriberInfo.objects.get_or_create(
                    subscriber_code=subscriber_code,
                    sn=sn,
                )

                # Datos de Smartcard
                obj.first_name = smartcard_data.get('firstName')
                obj.last_name = smartcard_data.get('lastName')
                obj.lastActivation = smartcard_data.get('lastActivation')
                obj.lastContact = smartcard_data.get('lastContact')
                obj.lastServiceListDownload = smartcard_data.get('lastServiceListDownload')
                obj.lastActivationIP = smartcard_data.get('lastActivationIP')
                obj.lastApiKeyId = smartcard_data.get('lastApiKeyId')
                obj.products = smartcard_data.get('products')
                obj.packages = smartcard_data.get('packages')
                obj.packageNames = smartcard_data.get('packageNames')
                obj.model = smartcard_data.get('model')

                pin_raw = smartcard_data.get('pin')
                if pin_raw:
                    obj.set_pin(pin_raw)

                # Datos de login
                if login_data:
                    obj.login1 = login_data.get('login1')
                    obj.login2 = login_data.get('login2')
                    password_raw = login_data.get('password')
                    if password_raw:
                        obj.set_password(password_raw)

                obj.save()

                logger.info(f"[merge_subscriber_data] Registro {'creado' if created else 'actualizado'} para SN={sn}")

    except Exception as e:
        logger.error(f"[merge_subscriber_data] Error inesperado en {subscriber_code}: {str(e)}")

def compare_and_update_subscriber_data(subscriber_code):
    """
    Compara y actualiza datos existentes en SubscriberInfo para un subscriber_code específico.
    Solo actualiza campos que hayan cambiado.
    """
    logger.info(f"[compare_and_update_subscriber_data] Comparando datos para {subscriber_code}")

    try:
        # Obtener datos actuales de la BD
        existing_records = SubscriberInfo.objects.filter(subscriber_code=subscriber_code)
        if not existing_records.exists():
            logger.info(f"[compare_and_update_subscriber_data] No hay registros existentes para {subscriber_code}")
            return

        # Obtener datos frescos
        smartcard_data_list = get_smartcard_data(subscriber_code)
        login_data = get_login_data(subscriber_code)

        if not smartcard_data_list:
            logger.warning(f"[compare_and_update_subscriber_data] No hay datos de smartcard frescos para {subscriber_code}")
            return

        # Crear diccionario de datos existentes por SN
        existing_by_sn = {record.sn: record for record in existing_records}

        total_updated = 0
        
        with transaction.atomic():
            for smartcard_data in smartcard_data_list:
                sn = smartcard_data.get('sn')
                if not sn or sn not in existing_by_sn:
                    continue

                obj = existing_by_sn[sn]
                changed_fields = []

                # Comparar campos de smartcard
                smartcard_fields = {
                    'first_name': smartcard_data.get('firstName'),
                    'last_name': smartcard_data.get('lastName'),
                    'lastActivation': smartcard_data.get('lastActivation'),
                    'lastContact': smartcard_data.get('lastContact'),
                    'lastServiceListDownload': smartcard_data.get('lastServiceListDownload'),
                    'lastActivationIP': smartcard_data.get('lastActivationIP'),
                    'lastApiKeyId': smartcard_data.get('lastApiKeyId'),
                    'products': smartcard_data.get('products'),
                    'packages': smartcard_data.get('packages'),
                    'packageNames': smartcard_data.get('packageNames'),
                    'model': smartcard_data.get('model'),
                }

                for field_name, new_value in smartcard_fields.items():
                    current_value = getattr(obj, field_name)
                    # Comparar valores, manejando None y listas
                    if isinstance(current_value, list) and isinstance(new_value, list):
                        if current_value != new_value:
                            setattr(obj, field_name, new_value)
                            changed_fields.append(field_name)
                    elif isinstance(current_value, dict) and isinstance(new_value, dict):
                        # También manejar diccionarios si es necesario
                        if current_value != new_value:
                            setattr(obj, field_name, new_value)
                            changed_fields.append(field_name)
                    elif str(current_value) != str(new_value):
                        setattr(obj, field_name, new_value)
                        changed_fields.append(field_name)

                # Comparar PIN
                pin_raw = smartcard_data.get('pin')
                if pin_raw:
                    current_pin = obj.get_pin()
                    if current_pin != pin_raw:
                        obj.set_pin(pin_raw)
                        changed_fields.append('pin_hash')

                # Comparar campos de login
                if login_data:
                    login_fields = {
                        'login1': login_data.get('login1'),
                        'login2': login_data.get('login2'),
                    }

                    for field_name, new_value in login_fields.items():
                        current_value = getattr(obj, field_name)
                        if str(current_value) != str(new_value):
                            setattr(obj, field_name, new_value)
                            changed_fields.append(field_name)

                    # Comparar password
                    password_raw = login_data.get('password')
                    if password_raw:
                        current_password = obj.get_password()
                        if current_password != password_raw:
                            obj.set_password(password_raw)
                            changed_fields.append('password_hash')

                # Guardar solo si hay cambios
                if changed_fields:
                    obj.save(update_fields=changed_fields)
                    total_updated += 1
                    logger.info(f"[compare_and_update_subscriber_data] SN={sn} actualizado. Campos: {changed_fields}")
                else:
                    logger.debug(f"[compare_and_update_subscriber_data] Sin cambios para SN={sn}")

        logger.info(f"[compare_and_update_subscriber_data] Total actualizados para {subscriber_code}: {total_updated}")
        return total_updated

    except Exception as e:
        logger.error(f"[compare_and_update_subscriber_data] Error inesperado en {subscriber_code}: {str(e)}")
        return 0


def sync_merge_all_subscribers():
    """
    Ejecuta el proceso de sincronización para todos los suscriptores activos.

    • Si la tabla está vacía, hace merge de todos.
    • Si ya hay registros, solo hace merge de los nuevos (mayores al último) y actualiza existentes.
    """
    logger.info("[sync_merge_all_subscribers] Iniciando sincronización de suscriptores...")

    try:
        codes = sorted(get_all_subscriber_codes())
        logger.info(f"[sync_merge_all_subscribers] Total de códigos encontrados: {len(codes)}")

        if subscriber_info_empty():
            logger.info("[sync_merge_all_subscribers] Base vacía. Procesando todos los códigos.")
            total_processed = 0
            for code in codes:
                merge_subscriber_data(code)
                total_processed += 1
            logger.info(f"[sync_merge_all_subscribers] Procesados {total_processed} códigos nuevos.")
        else:
            last = last_subscriber_info()
            if not last:
                logger.warning("[sync_merge_all_subscribers] No se pudo determinar el último registro.")
                return

            last_code = last.subscriber_code
            nuevos = [c for c in codes if c > last_code]
            existentes = [c for c in codes if c <= last_code]

            logger.info(f"[sync_merge_all_subscribers] Procesando {len(nuevos)} códigos nuevos...")
            for code in nuevos:
                merge_subscriber_data(code)

            logger.info(f"[sync_merge_all_subscribers] Comparando y actualizando {len(existentes)} códigos existentes...")
            total_updated = 0
            for code in existentes:
                updated = compare_and_update_subscriber_data(code)
                if updated:
                    total_updated += updated

            logger.info(f"[sync_merge_all_subscribers] Nuevos: {len(nuevos)}, Actualizados: {total_updated}")

        logger.info("[sync_merge_all_subscribers] Sincronización finalizada.")

    except Exception as e:
        logger.error(f"[sync_merge_all_subscribers] Error inesperado durante la sincronización: {str(e)}")
        raise