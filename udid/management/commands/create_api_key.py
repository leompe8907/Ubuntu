"""
Comando de gestión para crear API keys.
Uso: python manage.py create_api_key --tenant <tenant_name> --plan <plan_name> [--name <key_name>]
"""
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from udid.models import Tenant, Plan, APIKey
from udid.utils.server.token_signing import generate_simple_api_key
import secrets


class Command(BaseCommand):
    help = 'Crea una nueva API key para un tenant y plan'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tenant',
            type=str,
            required=True,
            help='Nombre del tenant'
        )
        parser.add_argument(
            '--plan',
            type=str,
            required=True,
            help='Nombre del plan'
        )
        parser.add_argument(
            '--name',
            type=str,
            help='Nombre descriptivo para la API key (opcional)'
        )
        parser.add_argument(
            '--expires-days',
            type=int,
            help='Días hasta que expire la API key (opcional)'
        )

    def handle(self, *args, **options):
        tenant_name = options['tenant']
        plan_name = options['plan']
        key_name = options.get('name')
        expires_days = options.get('expires_days')

        # Obtener o crear tenant
        try:
            tenant = Tenant.objects.get(name=tenant_name)
        except Tenant.DoesNotExist:
            self.stdout.write(
                self.style.WARNING(f'Tenant "{tenant_name}" no existe. Creando...')
            )
            tenant = Tenant.objects.create(name=tenant_name)
            self.stdout.write(
                self.style.SUCCESS(f'Tenant "{tenant_name}" creado exitosamente.')
            )

        # Obtener plan
        try:
            plan = Plan.objects.get(name=plan_name)
        except Plan.DoesNotExist:
            raise CommandError(f'Plan "{plan_name}" no existe. Crea el plan primero.')

        # Generar API key
        api_key_value = generate_simple_api_key()

        # Calcular fecha de expiración si se proporciona
        expires_at = None
        if expires_days:
            expires_at = timezone.now() + timezone.timedelta(days=expires_days)

        # Crear API key
        api_key = APIKey.objects.create(
            key=api_key_value,
            name=key_name,
            tenant=tenant,
            plan=plan,
            expires_at=expires_at
        )

        self.stdout.write(self.style.SUCCESS('\n' + '='*70))
        self.stdout.write(self.style.SUCCESS('  API KEY CREADA EXITOSAMENTE'))
        self.stdout.write(self.style.SUCCESS('='*70))
        self.stdout.write(f'\nTenant: {tenant.name}')
        self.stdout.write(f'Plan: {plan.name}')
        self.stdout.write(f'Nombre: {key_name or "Sin nombre"}')
        self.stdout.write(f'Expira: {expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else "Nunca"}')
        self.stdout.write(f'\nAPI Key: {api_key_value}')
        self.stdout.write(self.style.WARNING(
            '\n⚠️  IMPORTANTE: Guarda esta API key ahora. No se mostrará de nuevo.'
        ))
        self.stdout.write(self.style.SUCCESS('='*70 + '\n'))

