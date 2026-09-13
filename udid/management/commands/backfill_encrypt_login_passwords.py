"""
Comando de gestion para cifrar con Fernet las contraseñas de SubscriberLoginInfo.password
que quedaron guardadas en texto plano antes de la migracion
0004_encrypt_subscriberlogininfo_password (ver udid/utils/panaccess/login.py).

Es seguro correrlo mas de una vez y en cualquier momento: cada registro se prueba con
decrypt_value() antes de tocarlo. Si ya es un token Fernet valido para la clave actual,
se deja intacto; si no, se asume texto plano y se cifra con set_login_password().

Uso:
    python manage.py backfill_encrypt_login_passwords --dry-run
    python manage.py backfill_encrypt_login_passwords
    python manage.py backfill_encrypt_login_passwords --batch-size 1000
"""
import logging

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.utils import OperationalError, DatabaseError

from udid.models import SubscriberLoginInfo
from udid.utils.encryption import decrypt_value
from udid.utils.db_utils import is_connection_error, reconnect_database

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Cifra con Fernet las contraseñas de SubscriberLoginInfo.password que quedaron '
        'en texto plano antes de la migracion 0004. Idempotente: los valores que ya '
        'estan cifrados se detectan y se dejan sin cambios.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='No escribe en la base de datos, solo reporta cuantos registros se cifrarian.'
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=500,
            help='Cantidad de registros a guardar por transaccion (default: 500).'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        batch_size = options['batch_size']
        max_retries = 3

        qs = SubscriberLoginInfo.objects.exclude(password__isnull=True).exclude(password='')
        total = qs.count()

        self.stdout.write(f'Registros con password no vacio: {total}')
        if total == 0:
            self.stdout.write(self.style.SUCCESS('Nada que hacer.'))
            return

        stats = {'processed': 0, 'already_encrypted': 0, 'encrypted': 0, 'errors': 0}
        batch = []

        def flush_batch():
            if not batch:
                return
            if dry_run:
                stats['encrypted'] += len(batch)
                batch.clear()
                return

            retry_count = 0
            while retry_count < max_retries:
                try:
                    with transaction.atomic():
                        for obj in batch:
                            obj.save(update_fields=['password'])
                    stats['encrypted'] += len(batch)
                    break
                except (OperationalError, DatabaseError) as e:
                    if is_connection_error(e):
                        retry_count += 1
                        self.stdout.write(self.style.WARNING(
                            f'Conexion perdida guardando lote (intento {retry_count}/{max_retries}). '
                            f'Reconectando...'
                        ))
                        reconnect_database()
                        continue
                    raise
            else:
                stats['errors'] += len(batch)
                logger.error(f'No se pudo guardar un lote de {len(batch)} registros tras {max_retries} intentos')
            batch.clear()

        for obj in qs.iterator(chunk_size=batch_size):
            stats['processed'] += 1
            raw_value = obj.password

            # Heuristica de deteccion: si decrypt_value() no falla, el valor ya es un
            # token Fernet valido para la clave actual -> ya esta cifrado, no tocar.
            try:
                decrypt_value(raw_value)
                stats['already_encrypted'] += 1
                continue
            except Exception:
                pass

            try:
                obj.set_login_password(raw_value)
            except Exception as e:
                stats['errors'] += 1
                logger.error(
                    f'Error cifrando password de subscriberCode={obj.subscriberCode!r}: {e}'
                )
                continue

            batch.append(obj)
            if len(batch) >= batch_size:
                flush_batch()
                self.stdout.write(f'Progreso: {stats["processed"]}/{total} revisados...')

        flush_batch()

        action_label = 'Se cifrarian (dry-run)' if dry_run else 'Cifrados ahora'
        self.stdout.write(self.style.SUCCESS('\n' + '=' * 70))
        self.stdout.write(self.style.SUCCESS('  BACKFILL DE CIFRADO DE PASSWORDS COMPLETADO'))
        self.stdout.write(self.style.SUCCESS('=' * 70))
        self.stdout.write(f'Total revisados: {stats["processed"]}')
        self.stdout.write(f'Ya estaban cifrados (sin cambios): {stats["already_encrypted"]}')
        self.stdout.write(f'{action_label}: {stats["encrypted"]}')
        if stats['errors']:
            self.stdout.write(self.style.WARNING(f'Errores (revisar logs): {stats["errors"]}'))
        self.stdout.write(self.style.SUCCESS('=' * 70 + '\n'))
