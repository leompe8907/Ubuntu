from ...models import ListOfSubscriber, ListOfSmartcards, SubscriberLoginInfo, SubscriberInfo
from django.db import transaction
from django.db.utils import OperationalError, DatabaseError
import logging
from ...utils.db_utils import is_connection_error, reconnect_database

logger = logging.getLogger(__name__)


def get_all_subscriber_codes():
    """
    Retorna todos los códigos únicos de suscriptores en las tablas base.
    Incluye ListOfSubscriber, ListOfSmartcards y SubscriberLoginInfo.
    """
    logger.info("[get_all_subscriber_codes] Obteniendo códigos únicos de suscriptores...")
    codes = set(
        ListOfSubscriber.objects.values_list('code', flat=True)
        .exclude(code__isnull=True).exclude(code='')
    )
    codes |= set(
        ListOfSmartcards.objects.values_list('subscriberCode', flat=True)
        .exclude(subscriberCode__isnull=True).exclude(subscriberCode='')
    )
    codes |= set(
        SubscriberLoginInfo.objects.values_list('subscriberCode', flat=True)
        .exclude(subscriberCode__isnull=True).exclude(subscriberCode='')
    )
    logger.info(f"[get_all_subscriber_codes] Total encontrados: {len(codes)}")
    return codes


def _smartcard_row_to_data(sc):
    """Convierte un registro ListOfSmartcards a dict para SubscriberInfo."""
    return {
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
        'model': sc.model,
    }


def get_smartcard_data(subscriber_code):
    """
    Busca todos los datos de smartcards para un suscriptor por código.
    """
    try:
        smartcards = ListOfSmartcards.objects.filter(subscriberCode=subscriber_code)
        if not smartcards.exists():
            logger.warning(f"[get_smartcard_data] No se encontraron smartcards para {subscriber_code}")
            return []

        logger.info(f"[get_smartcard_data] {smartcards.count()} smartcards encontradas para {subscriber_code}")

        result = []
        for sc in smartcards:
            if not sc.sn:
                logger.warning(f"[get_smartcard_data] Smartcard sin SN, se omite: {sc.id}")
                continue
            result.append(_smartcard_row_to_data(sc))

        return result

    except Exception as e:
        logger.error(f"[get_smartcard_data] Error inesperado para {subscriber_code}: {str(e)}", exc_info=True)
        return []


def get_login_data(subscriber_code):
    """
    Busca las credenciales de login para un suscriptor por código.
    """
    try:
        login = SubscriberLoginInfo.objects.get(subscriberCode=subscriber_code)
        logger.info(f"[get_login_data] Login encontrado para {subscriber_code}")
        return {
            'login1': login.login1,
            'login2': login.login2,
            'password': login.password,
        }
    except SubscriberLoginInfo.DoesNotExist:
        logger.warning(f"[get_login_data] No se encontró login para {subscriber_code}")
        return {}


def subscriber_info_empty():
    """Verifica si la tabla SubscriberInfo está vacía."""
    empty = not SubscriberInfo.objects.exists()
    logger.info(f"[subscriber_info_empty] ¿Base vacía? {empty}")
    return empty


def last_subscriber_info():
    """Retorna el último registro de SubscriberInfo basado en subscriber_code."""
    try:
        last = SubscriberInfo.objects.latest('subscriber_code')
        logger.info(f"[last_subscriber_info] Último código encontrado: {last.subscriber_code}")
        return last
    except SubscriberInfo.DoesNotExist:
        logger.warning("[last_subscriber_info] No hay registros en SubscriberInfo.")
        return None


def _upsert_subscriber_info_record(subscriber_code, smartcard_data, login_data=None):
    """
    Crea o actualiza un registro en SubscriberInfo para una SN.
    login_data es opcional: sin login igual se guarda la smartcard (búsqueda por SN).
    """
    sn = smartcard_data.get('sn')
    if not sn:
        return False

    login_data = login_data or {}

    obj, created = SubscriberInfo.objects.get_or_create(
        subscriber_code=subscriber_code,
        sn=sn,
    )

    obj.first_name = smartcard_data.get('first_name')
    obj.last_name = smartcard_data.get('last_name')
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

    if login_data:
        obj.login1 = login_data.get('login1')
        obj.login2 = login_data.get('login2')
        password_raw = login_data.get('password')
        if password_raw:
            obj.set_password(password_raw)

    obj.save()
    return created


def sync_smartcard_record(sc):
    """Consolida un registro ListOfSmartcards en SubscriberInfo."""
    if not sc or not sc.sn:
        return False
    if not sc.subscriberCode:
        logger.warning(f"[sync_smartcard_record] SN={sc.sn} sin subscriberCode, no se consolida")
        return False
    login_data = get_login_data(sc.subscriberCode)
    _upsert_subscriber_info_record(sc.subscriberCode, _smartcard_row_to_data(sc), login_data)
    return True


def sync_smartcard_by_sn(sn):
    """
    Consolida una SN concreta desde ListOfSmartcards hacia SubscriberInfo.
    Útil cuando la smartcard existe pero aún no pasó por el merge masivo.
    """
    sn = str(sn).strip()
    if not sn:
        return False
    if SubscriberInfo.objects.filter(sn=sn).exists():
        return True
    sc = ListOfSmartcards.objects.filter(sn=sn).first()
    if not sc:
        logger.warning(f"[sync_smartcard_by_sn] SN={sn} no encontrada en ListOfSmartcards")
        return False
    return sync_smartcard_record(sc)


def ensure_sn_searchable(sn):
    """Garantiza que una SN esté en SubscriberInfo si existe en ListOfSmartcards."""
    return sync_smartcard_by_sn(sn)


def sync_all_smartcards_bulk(transaction_size=100):
    """
    Consolida TODAS las smartcards hacia SubscriberInfo recorriendo ListOfSmartcards.
    Más fiable que iterar solo por códigos de suscriptor.
    """
    logger.info("[sync_all_smartcards_bulk] Iniciando consolidación masiva desde smartcards...")
    qs = (
        ListOfSmartcards.objects
        .exclude(sn__isnull=True).exclude(sn='')
        .exclude(subscriberCode__isnull=True).exclude(subscriberCode='')
    )
    total = 0
    batch = []

    for sc in qs.iterator(chunk_size=500):
        batch.append(sc)
        if len(batch) < transaction_size:
            continue
        with transaction.atomic():
            for item in batch:
                sync_smartcard_record(item)
        total += len(batch)
        batch = []
        if total % 5000 == 0:
            logger.info(f"[sync_all_smartcards_bulk] Procesadas {total} smartcards...")

    if batch:
        with transaction.atomic():
            for item in batch:
                sync_smartcard_record(item)
        total += len(batch)

    logger.info(f"[sync_all_smartcards_bulk] Finalizado. Total procesadas: {total}")
    return total


def sync_subscriber_code(subscriber_code):
    """
    Sincroniza todas las SN de un suscriptor hacia SubscriberInfo (crear + actualizar).
    """
    smartcard_data_list = get_smartcard_data(subscriber_code)
    if not smartcard_data_list:
        return 0

    login_data = get_login_data(subscriber_code)
    if not login_data:
        logger.warning(
            f"[sync_subscriber_code] Sin login para {subscriber_code}; "
            "se consolidan smartcards igualmente para búsqueda."
        )

    max_retries = 3
    retry_count = 0
    created_count = 0

    while retry_count < max_retries:
        try:
            with transaction.atomic():
                for smartcard_data in smartcard_data_list:
                    if _upsert_subscriber_info_record(subscriber_code, smartcard_data, login_data):
                        created_count += 1
            return created_count

        except (OperationalError, DatabaseError) as e:
            if is_connection_error(e):
                retry_count += 1
                logger.warning(
                    f"[sync_subscriber_code] Conexión perdida "
                    f"(intento {retry_count}/{max_retries}). Reconectando..."
                )
                reconnect_database()
                if retry_count < max_retries:
                    import time
                    time.sleep(2 * retry_count)
                    continue
                raise DatabaseError(f"No se pudo reconectar después de {max_retries} intentos")
            raise

    return created_count


def merge_subscriber_data(subscriber_code):
    """Fusiona datos de smartcard y login en SubscriberInfo para un código."""
    logger.info(f"[merge_subscriber_data] Iniciando consolidación para {subscriber_code}")
    try:
        created = sync_subscriber_code(subscriber_code)
        logger.info(f"[merge_subscriber_data] {subscriber_code}: {created} registro(s) nuevo(s)")
    except Exception as e:
        logger.error(f"[merge_subscriber_data] Error inesperado en {subscriber_code}: {str(e)}")


def compare_and_update_subscriber_data(subscriber_code):
    """Mantiene compatibilidad con tareas existentes: upsert completo por código."""
    logger.info(f"[compare_and_update_subscriber_data] Sincronizando {subscriber_code}")
    try:
        return sync_subscriber_code(subscriber_code)
    except Exception as e:
        logger.error(
            f"[compare_and_update_subscriber_data] Error inesperado en {subscriber_code}: {str(e)}"
        )
        return 0


def sync_merge_all_subscribers():
    """
    Sincroniza smartcards hacia SubscriberInfo.
    Usa recorrido directo de ListOfSmartcards para no omitir ninguna SN.
    """
    logger.info("[sync_merge_all_subscribers] Iniciando sincronización de suscriptores...")
    try:
        total = sync_all_smartcards_bulk()
        logger.info(f"[sync_merge_all_subscribers] Finalizado. Smartcards procesadas: {total}")
    except Exception as e:
        logger.error(f"[sync_merge_all_subscribers] Error inesperado: {str(e)}")
        raise
