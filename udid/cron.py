from django_cron import CronJobBase, Schedule
from .utils.panaccess import (
    CVClient,
    sync_smartcards,
    update_smartcards_from_subscribers,
    sync_subscribers,
    sync_subscriber_logins,
    sync_merge_all_subscribers,
)
import logging

logger = logging.getLogger(__name__)

def execute_sync_tasks():
    """
    Ejecuta todas las tareas de sincronización y validación completa desde Panaccess.
    
    QUÉ HACE:
    Esta función realiza una sincronización completa y validación de todos los datos:
    - Smartcards (tarjetas inteligentes con productos, paquetes, estado)
    - Suscriptores (información de clientes)
    - Credenciales de login de suscriptores
    - Tabla consolidada SubscriberInfo (merge de toda la información)
    
    CÓMO LO HACE:
    Comportamiento inteligente según el estado de la base de datos:
    
    1. PRIMERA EJECUCIÓN (Base de datos vacía):
       - Detecta automáticamente que no hay registros
       - Descarga TODOS los datos desde Panaccess (descarga completa inicial)
       - Crea todos los registros en las tablas correspondientes
       - Genera la tabla consolidada SubscriberInfo
    
    2. EJECUCIONES SIGUIENTES (Base de datos con datos):
       - Descarga NUEVOS registros que no existen en la BD
       - VALIDA y COMPARA cada registro existente con Panaccess
       - ACTUALIZA automáticamente los registros que han cambiado
       - Mantiene sincronizada la tabla consolidada SubscriberInfo
    
    PROCESO PASO A PASO:
    1. Autenticación: Hace login a Panaccess para obtener session_id
    2. Sincronización de Smartcards: Descarga/valida tarjetas inteligentes
    3. Sincronización de Suscriptores: Descarga/valida información de clientes
    4. Sincronización de Credenciales: Descarga/valida logins de suscriptores
    5. Merge en SubscriberInfo: Consolida toda la información en una tabla única
    
    Esta función puede ser llamada tanto por el cron como por el endpoint manual.
    
    Returns:
        dict: Resultado de la sincronización con información detallada de cada tarea
    """
    # Estructura de resultado para rastrear el éxito/fallo de cada tarea
    result = {
        'success': False,
        'message': '',
        'tasks': {
            'smartcards': {'success': False, 'message': ''},
            'subscribers': {'success': False, 'message': ''},
            'subscriber_logins': {'success': False, 'message': ''},
            'merge_subscribers': {'success': False, 'message': ''}
        },
        'session_id': None
    }
    
    try:
        logger.info("[SYNC] Iniciando validación y sincronización completa de información")
        
        # ========================================================================
        # PASO 1: AUTENTICACIÓN CON PANACCESS
        # ========================================================================
        # Crea un cliente para conectarse a la API de Panaccess
        # y obtiene un session_id necesario para todas las operaciones
        client = CVClient()
        success, error_message = client.login()
        
        if not success:
            # Si falla el login, no podemos continuar con ninguna sincronización
            error_msg = f"Error al hacer login: {error_message}"
            logger.error(f"[SYNC] {error_msg}")
            result['message'] = error_msg
            return result
        
        session_id = client.session_id
        result['session_id'] = session_id
        logger.info(f"[SYNC] Session ID: {session_id}")
        
        # ========================================================================
        # PASO 2: SINCRONIZACIÓN DE SMARTCARDS
        # ========================================================================
        # QUÉ HACE: Sincroniza las tarjetas inteligentes (smartcards)
        # CÓMO LO HACE:
        #   - Si BD vacía: Descarga TODAS las smartcards desde Panaccess
        #   - Si BD con datos: Descarga nuevas Y valida/actualiza las existentes
        #   - Compara: productos, paquetes, estado, número de serie (SN), etc.
        #   - Actualiza: Solo los campos que han cambiado en Panaccess
        try:
            logger.info("[SYNC] Validando y sincronizando smartcards desde Panaccess")
            sync_smartcards(session_id)
            result['tasks']['smartcards'] = {'success': True, 'message': 'Validación y sincronización completada'}
            logger.info("[SYNC] Smartcards validadas y sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error validando smartcards: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['smartcards'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # PASO 3: SINCRONIZACIÓN DE SUSCRIPTORES
        # ========================================================================
        # QUÉ HACE: Sincroniza la información de los suscriptores (clientes)
        # CÓMO LO HACE:
        #   - Si BD vacía: Descarga TODOS los suscriptores desde Panaccess
        #   - Si BD con datos: Descarga nuevos Y valida/actualiza los existentes
        #   - Compara: código, nombre, apellido, dirección, smartcards asociadas, etc.
        #   - Actualiza: Solo los campos que han cambiado en Panaccess
        try:
            logger.info("[SYNC] Validando y sincronizando suscriptores desde Panaccess")
            sync_subscribers(session_id)
            result['tasks']['subscribers'] = {'success': True, 'message': 'Validación y sincronización completada'}
            logger.info("[SYNC] Suscriptores validados y sincronizados correctamente")
        except Exception as e:
            error_msg = f"Error validando suscriptores: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['subscribers'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # PASO 4: SINCRONIZACIÓN DE CREDENCIALES DE LOGIN
        # ========================================================================
        # QUÉ HACE: Sincroniza las credenciales de acceso de los suscriptores
        # CÓMO LO HACE:
        #   - Si BD vacía: Descarga TODAS las credenciales desde Panaccess
        #   - Si BD con datos: Descarga nuevas Y valida/actualiza las existentes
        #   - Compara: username, password, subscriberCode, etc.
        #   - Actualiza: Solo las credenciales que han cambiado en Panaccess
        try:
            logger.info("[SYNC] Validando y sincronizando credenciales de suscriptores desde Panaccess")
            sync_subscriber_logins(session_id)
            result['tasks']['subscriber_logins'] = {'success': True, 'message': 'Validación y sincronización completada'}
            logger.info("[SYNC] Credenciales validadas y sincronizadas correctamente")
        except Exception as e:
            error_msg = f"Error validando credenciales: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['subscriber_logins'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # PASO 5: MERGE Y ACTUALIZACIÓN DE SUBSCRIBERINFO (TABLA CONSOLIDADA)
        # ========================================================================
        # QUÉ HACE: Consolida toda la información en una sola tabla (SubscriberInfo)
        # CÓMO LO HACE:
        #   - Toma datos de: ListOfSubscriber, ListOfSmartcards, SubscriberLoginInfo
        #   - Combina: Toda la información relacionada a cada suscriptor
        #   - Crea/Actualiza: Registros en SubscriberInfo (tabla usada para validar UDIDs)
        #   - Ventaja: Una sola consulta para obtener toda la info de un suscriptor
        try:
            logger.info("[SYNC] Validando y actualizando merge de suscriptores en SubscriberInfo")
            sync_merge_all_subscribers()
            result['tasks']['merge_subscribers'] = {'success': True, 'message': 'Validación y actualización completada'}
            logger.info("[SYNC] SubscriberInfo validado y actualizado correctamente")
        except Exception as e:
            error_msg = f"Error validando merge de suscriptores: {str(e)}"
            logger.error(f"[SYNC] {error_msg}")
            result['tasks']['merge_subscribers'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # VERIFICACIÓN FINAL: ÉXITO GENERAL
        # ========================================================================
        # Verifica si todas las tareas se completaron exitosamente
        # Si alguna falló, el resultado general será False pero el proceso continuó
        all_success = all(task['success'] for task in result['tasks'].values())
        result['success'] = all_success
        result['message'] = 'Sincronización completada' if all_success else 'Sincronización completada con algunos errores'
        
        logger.info(f"[SYNC] Validación y sincronización completa finalizada. Éxito: {all_success}")
        
    except Exception as e:
        # Captura cualquier error inesperado que no fue manejado en las tareas individuales
        error_msg = f"Error inesperado durante la sincronización: {str(e)}"
        logger.error(f"[SYNC] {error_msg}", exc_info=True)
        result['message'] = error_msg
    
    return result

def execute_update_subscribers():
    """
    Ejecuta la sincronización rápida de suscriptores y datos relacionados.
    
    QUÉ HACE:
    Esta función realiza una actualización rápida y frecuente de:
    - Suscriptores (información de clientes)
    - Credenciales de login de suscriptores
    - Smartcards asociadas a suscriptores (solo asociación, no productos/paquetes completos)
    - Tabla consolidada SubscriberInfo (merge de toda la información)
    
    CÓMO LO HACE:
    Proceso optimizado para ejecutarse frecuentemente (cada 5 minutos):
    
    1. Sincronización de Suscriptores:
       - Si BD vacía: Descarga TODOS los suscriptores
       - Si BD con datos: Descarga nuevos Y actualiza existentes
       - Compara y actualiza: código, nombre, dirección, smartcards asociadas, etc.
    
    2. Sincronización de Credenciales:
       - Si BD vacía: Descarga TODAS las credenciales
       - Si BD con datos: Descarga nuevas Y actualiza existentes
       - Compara y actualiza: username, password, subscriberCode, etc.
    
    3. Actualización de Smartcards desde Suscriptores:
       - Lee el campo 'smartcards' (JSON) de cada suscriptor
       - Extrae los números de serie (SN) de las smartcards asociadas
       - Crea/actualiza registros en ListOfSmartcards con la asociación al suscriptor
       - IMPORTANTE: NO descarga productos/paquetes completos desde Panaccess
       - Ventaja: Es MUY rápido (segundos) vs. 8-9 horas de sincronización completa
    
    4. Merge en SubscriberInfo:
       - Consolida toda la información en la tabla SubscriberInfo
       - Esta tabla se usa para validar UDIDs en tiempo real
    
    DIFERENCIA CON execute_sync_tasks():
    - Esta función NO sincroniza smartcards completas desde Panaccess (productos, paquetes, etc.)
    - Es más rápida (segundos/minutos) vs. horas de sincronización completa
    - Se ejecuta cada 5 minutos para mantener datos actualizados
    - Suficiente para asociar UDIDs rápidamente cuando un usuario se registra
    
    NOTA IMPORTANTE:
    No sincroniza smartcards completas desde Panaccess aquí porque con 10,000 smartcards
    puede tomar 8-9 horas. La actualización desde suscriptores es más rápida y suficiente
    para asociar UDIDs. Para sincronizar productos/paquetes completos, usar MergeSyncCronJob
    (diaria) o ejecución manual.
    
    Esta función puede ser llamada tanto por el cron como por el endpoint manual.
    
    Returns:
        dict: Resultado de la sincronización con información detallada de cada tarea
    """
    # Estructura de resultado para rastrear el éxito/fallo de cada tarea
    result = {
        'success': False,
        'message': '',
        'session_id': None,
        'tasks': {
            'subscribers': {'success': False, 'message': ''},
            'credentials': {'success': False, 'message': ''},
            'smartcards_from_subscribers': {'success': False, 'message': ''},
            'merge_subscribers': {'success': False, 'message': ''}
        }
    }
    
    try:
        logger.info("[UPDATE_SUBSCRIBERS] Iniciando sincronización de suscriptores y credenciales")
        
        # ========================================================================
        # PASO 1: AUTENTICACIÓN CON PANACCESS
        # ========================================================================
        # Obtiene un session_id necesario para consultar la API de Panaccess
        client = CVClient()
        success, error_message = client.login()
        
        if not success:
            # Si falla el login, no podemos continuar
            error_msg = f"Error al hacer login: {error_message}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}")
            result['message'] = error_msg
            return result
        
        session_id = client.session_id
        result['session_id'] = session_id
        logger.info(f"[UPDATE_SUBSCRIBERS] Session ID: {session_id}")
        
        # ========================================================================
        # PASO 2: SINCRONIZACIÓN DE SUSCRIPTORES
        # ========================================================================
        # QUÉ HACE: Actualiza la información de los suscriptores (clientes)
        # CÓMO LO HACE:
        #   - Si BD vacía: Descarga TODOS los suscriptores desde Panaccess
        #   - Si BD con datos: Descarga nuevos Y valida/actualiza los existentes
        #   - Compara: código, nombre, apellido, dirección, smartcards asociadas, etc.
        #   - Actualiza: Solo los campos que han cambiado en Panaccess
        #   - Límite: 100 registros por página para optimizar memoria
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando sincronización completa de suscriptores")
            sync_subscribers(session_id, limit=100)
            result['tasks']['subscribers'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[UPDATE_SUBSCRIBERS] Sincronización de suscriptores completada exitosamente")
        except Exception as e:
            error_msg = f"Error en sincronización de suscriptores: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['subscribers'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # PASO 3: SINCRONIZACIÓN DE CREDENCIALES DE LOGIN
        # ========================================================================
        # QUÉ HACE: Actualiza las credenciales de acceso de los suscriptores
        # CÓMO LO HACE:
        #   - Si BD vacía: Descarga TODAS las credenciales desde Panaccess
        #   - Si BD con datos: Descarga nuevas Y valida/actualiza las existentes
        #   - Compara: username, password, subscriberCode, etc.
        #   - Actualiza: Solo las credenciales que han cambiado en Panaccess
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando sincronización de credenciales de suscriptores")
            sync_subscriber_logins(session_id)
            result['tasks']['credentials'] = {'success': True, 'message': 'Sincronización completada'}
            logger.info("[UPDATE_SUBSCRIBERS] Sincronización de credenciales completada exitosamente")
        except Exception as e:
            error_msg = f"Error en sincronización de credenciales: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['credentials'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # PASO 4: ACTUALIZACIÓN DE SMARTCARDS DESDE INFORMACIÓN DE SUSCRIPTORES
        # ========================================================================
        # QUÉ HACE: Actualiza la asociación de smartcards con suscriptores
        # CÓMO LO HACE:
        #   - Lee el campo 'smartcards' (JSON) de cada suscriptor en ListOfSubscriber
        #   - Extrae los números de serie (SN) de las smartcards asociadas
        #   - Busca cada SN en ListOfSmartcards
        #   - Si no existe: Crea un nuevo registro con el SN y subscriberCode
        #   - Si existe: Actualiza el subscriberCode asociado
        #   - IMPORTANTE: NO descarga productos/paquetes completos desde Panaccess
        #   - Ventaja: Es MUY rápido (segundos) vs. 8-9 horas de sincronización completa
        #   - Suficiente: Para asociar UDIDs cuando un usuario se registra
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando actualización de smartcards desde suscriptores")
            update_result = update_smartcards_from_subscribers()
            result['tasks']['smartcards_from_subscribers'] = {
                'success': True,
                'message': f"Actualización completada: {update_result.get('total_smartcards_created', 0)} creadas, {update_result.get('total_smartcards_updated', 0)} actualizadas",
                'details': update_result
            }
            logger.info("[UPDATE_SUBSCRIBERS] Actualización de smartcards desde suscriptores completada exitosamente")
        except Exception as e:
            error_msg = f"Error en actualización de smartcards desde suscriptores: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['smartcards_from_subscribers'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # PASO 5: MERGE Y ACTUALIZACIÓN DE SUBSCRIBERINFO (TABLA CONSOLIDADA)
        # ========================================================================
        # QUÉ HACE: Consolida toda la información en una sola tabla (SubscriberInfo)
        # CÓMO LO HACE:
        #   - Toma datos de: ListOfSubscriber, ListOfSmartcards, SubscriberLoginInfo
        #   - Combina: Toda la información relacionada a cada suscriptor
        #   - Crea/Actualiza: Registros en SubscriberInfo
        #   - Ventaja: Una sola consulta para obtener toda la info de un suscriptor
        #   - Uso: Esta tabla se usa para validar UDIDs en tiempo real
        try:
            logger.info("[UPDATE_SUBSCRIBERS] Iniciando merge y actualización de SubscriberInfo")
            sync_merge_all_subscribers()
            result['tasks']['merge_subscribers'] = {'success': True, 'message': 'Merge y actualización completada'}
            logger.info("[UPDATE_SUBSCRIBERS] Merge y actualización de SubscriberInfo completada exitosamente")
        except Exception as e:
            error_msg = f"Error en merge de suscriptores: {str(e)}"
            logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
            result['tasks']['merge_subscribers'] = {'success': False, 'message': error_msg}
        
        # ========================================================================
        # VERIFICACIÓN FINAL: ÉXITO GENERAL
        # ========================================================================
        # Verifica si todas las tareas se completaron exitosamente
        all_success = all(task['success'] for task in result['tasks'].values())
        result['success'] = all_success
        result['message'] = 'Sincronización completada' if all_success else 'Sincronización completada con algunos errores'
        
        logger.info(f"[UPDATE_SUBSCRIBERS] Sincronización finalizada. Éxito: {all_success}")
        
    except Exception as e:
        # Captura cualquier error inesperado que no fue manejado en las tareas individuales
        error_msg = f"Error inesperado durante la sincronización: {str(e)}"
        logger.error(f"[UPDATE_SUBSCRIBERS] {error_msg}", exc_info=True)
        result['message'] = error_msg
    
    return result


class MergeSyncCronJob(CronJobBase):
    """
    CronJob para sincronización completa y validación de toda la información.
    
    QUÉ HACE:
    Esta tarea realiza una sincronización COMPLETA de todos los datos desde Panaccess:
    - Smartcards completas (productos, paquetes, estado, etc.)
    - Suscriptores completos
    - Credenciales de login completas
    - Tabla consolidada SubscriberInfo
    - Asociación de smartcards con suscriptores
    
    CÓMO LO HACE:
    Comportamiento inteligente según el estado de la base de datos:
    
    PRIMERA EJECUCIÓN (Base de datos vacía):
    - Detecta automáticamente que no hay registros
    - Descarga TODOS los datos desde Panaccess (descarga completa inicial)
    - Crea todos los registros en las tablas correspondientes
    - Genera la tabla consolidada SubscriberInfo
    
    EJECUCIONES SIGUIENTES (Base de datos con datos):
    - Descarga NUEVOS registros que no existen en la BD
    - VALIDA y COMPARA cada registro existente con Panaccess
    - ACTUALIZA automáticamente los registros que han cambiado
    - Sincroniza smartcards completas (productos, paquetes, estado) desde Panaccess
    - Valida y actualiza la asociación de smartcards con suscriptores
    - Mantiene sincronizada la tabla consolidada SubscriberInfo
    
    PROCESO PASO A PASO:
    1. Ejecuta execute_sync_tasks() que:
       - Autentica con Panaccess
       - Sincroniza smartcards completas (puede tomar horas con muchos registros)
       - Sincroniza suscriptores
       - Sincroniza credenciales de login
       - Hace merge en SubscriberInfo
    
    2. Actualiza smartcards desde suscriptores:
       - Asegura que la asociación subscriberCode esté correcta
       - Crea/actualiza smartcards basándose en la información de suscriptores
    
    FRECUENCIA:
    - Se ejecuta una vez al día a las 00:00 (medianoche)
    - Razón: Puede tomar varias horas con grandes volúmenes de datos (8-9 horas con 10,000 smartcards)
    - Es una tarea de validación y corrección completa
    - Se ejecuta en horario de bajo tráfico para no afectar el rendimiento del sistema
    
    DIFERENCIA CON UpdateSubscribersCronJob:
    - Esta tarea sincroniza smartcards COMPLETAS desde Panaccess (productos, paquetes, etc.)
    - UpdateSubscribersCronJob solo actualiza la asociación (más rápido, cada 5 minutos)
    - Esta tarea asegura que toda la información esté 100% correcta y sincronizada
    
    IMPORTANTE:
    Esta tarea asegura que toda la información esté correcta y sincronizada con Panaccess.
    Es la tarea de validación y corrección completa del sistema.
    """
    # Programar para ejecutarse a las 00:00 (medianoche) todos los días
    schedule = Schedule(run_at_times=['00:00'])
    code = 'udid.sync_smartcards_cron'

    def do(self):
        """
        Método principal que se ejecuta cuando el cron job se activa.
        
        QUÉ HACE:
        - Ejecuta la sincronización completa de todos los datos
        - Valida y corrige toda la información comparándola con Panaccess
        - Asegura que la asociación de smartcards con suscriptores esté correcta
        
        CÓMO LO HACE:
        1. Llama a execute_sync_tasks() para sincronización completa
        2. Actualiza smartcards desde suscriptores para validar asociaciones
        3. Registra el resultado en los logs
        """
        logger.info("[MERGE_SYNC] Iniciando validación y corrección completa de información")
        
        # Ejecuta la sincronización completa de todos los datos
        # Esta función maneja automáticamente si es primera vez o ejecución siguiente
        result = execute_sync_tasks()
        
        # Además, actualizar smartcards desde suscriptores para asegurar asociación correcta
        # Esto valida que la relación entre smartcards y suscriptores esté correcta
        try:
            logger.info("[MERGE_SYNC] Validando asociación de smartcards con suscriptores")
            update_result = update_smartcards_from_subscribers()
            logger.info(f"[MERGE_SYNC] Asociación validada: {update_result.get('total_smartcards_created', 0)} creadas, {update_result.get('total_smartcards_updated', 0)} actualizadas")
        except Exception as e:
            logger.error(f"[MERGE_SYNC] Error validando asociación smartcards-suscriptores: {str(e)}", exc_info=True)
        
        logger.info("[MERGE_SYNC] Validación y corrección completa finalizada")
        return result


class UpdateSubscribersCronJob(CronJobBase):
    """
    CronJob para actualización rápida y frecuente de suscriptores y datos relacionados.
    
    QUÉ HACE:
    Esta tarea realiza una actualización RÁPIDA de:
    - Suscriptores (información de clientes)
    - Credenciales de login de suscriptores
    - Smartcards asociadas a suscriptores (solo asociación, NO productos/paquetes completos)
    - Tabla consolidada SubscriberInfo (merge de toda la información)
    
    CÓMO LO HACE:
    Proceso optimizado para ejecutarse frecuentemente:
    
    1. Sincronización de Suscriptores:
       - Si BD vacía: Descarga TODOS los suscriptores
       - Si BD con datos: Descarga nuevos Y actualiza existentes
       - Compara y actualiza: código, nombre, dirección, smartcards asociadas, etc.
    
    2. Sincronización de Credenciales:
       - Si BD vacía: Descarga TODAS las credenciales
       - Si BD con datos: Descarga nuevas Y actualiza existentes
       - Compara y actualiza: username, password, subscriberCode, etc.
    
    3. Actualización de Smartcards desde Suscriptores:
       - Lee el campo 'smartcards' (JSON) de cada suscriptor
       - Extrae los números de serie (SN) de las smartcards asociadas
       - Crea/actualiza registros en ListOfSmartcards con la asociación al suscriptor
       - IMPORTANTE: NO descarga productos/paquetes completos desde Panaccess
       - Ventaja: Es MUY rápido (segundos) vs. 8-9 horas de sincronización completa
    
    4. Merge en SubscriberInfo:
       - Consolida toda la información en la tabla SubscriberInfo
       - Esta tabla se usa para validar UDIDs en tiempo real
    
    FRECUENCIA:
    - Se ejecuta cada 5 minutos
    - Razón: Mantener los datos actualizados para que los usuarios puedan asociar UDIDs rápidamente
    - Es una tarea rápida (segundos/minutos) vs. horas de sincronización completa
    
    DIFERENCIA CON MergeSyncCronJob:
    - Esta tarea NO sincroniza smartcards completas desde Panaccess (productos, paquetes, etc.)
    - Solo actualiza la asociación de smartcards con suscriptores (más rápido)
    - MergeSyncCronJob sí sincroniza smartcards completas (más lento, una vez al día)
    - Esta tarea es suficiente para asociar UDIDs cuando un usuario se registra
    
    NOTA IMPORTANTE:
    No sincroniza smartcards completas desde Panaccess aquí porque con 10,000 smartcards
    puede tomar 8-9 horas. La actualización desde suscriptores es más rápida y suficiente
    para asociar UDIDs. Para sincronizar productos/paquetes completos, usar MergeSyncCronJob
    (diaria) o ejecución manual.
    
    VENTAJA:
    Permite que cuando un usuario se registre y quiera asociar un UDID, la información
    esté actualizada (máximo 5 minutos de retraso) sin tener que esperar la sincronización
    completa diaria.
    """
    RUN_EVERY_MINS = 5  # Cada 5 minutos
    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'udid.update_subscribers_cron'

    def do(self):
        """
        Método principal que se ejecuta cuando el cron job se activa.
        
        QUÉ HACE:
        - Ejecuta la actualización rápida de suscriptores y datos relacionados
        - Mantiene la información actualizada para validación de UDIDs
        
        CÓMO LO HACE:
        - Llama a execute_update_subscribers() que maneja todo el proceso
        - Esta función es rápida y optimizada para ejecutarse frecuentemente
        """
        # Usar la función reutilizable que maneja toda la lógica de actualización
        execute_update_subscribers()

