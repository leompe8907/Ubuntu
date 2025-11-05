from django.core.management.base import BaseCommand
from udid.models import AppCredentials
from .keyGenerator import generate_rsa_key_pair  # ✅ Import corregido

class Command(BaseCommand):
    help = 'Genera claves RSA para tipos de aplicación'
    
    def add_arguments(self, parser):
        parser.add_argument(
            '--app-type',
            type=str,
            help='Tipo de aplicación (android_tv, android_mobile, 10foot, set_top_box, ios_mobile, ios_tv, web_player)'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Generar claves para todos los tipos de app'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Sobrescribir claves existentes'
        )
    
    def handle(self, *args, **options):
        self.stdout.write("🔧 Iniciando generación de claves RSA...")
        
        # Determinar qué tipos de app procesar
        if options['all']:
            app_types = ['android_tv', 'android_mobile', '10foot', 'set_top_box', 'ios_mobile', 'ios_tv', 'web_player']
            self.stdout.write(f"📱 Generando claves para TODOS los tipos de app: {', '.join(app_types)}")
        elif options['app_type']:
            app_types = [options['app_type']]
            self.stdout.write(f"📱 Generando claves para: {options['app_type']}")
        else:
            self.stdout.write(
                self.style.ERROR('❌ Debes especificar --app-type o --all')
            )
            return
        
        success_count = 0
        
        for app_type in app_types:
            try:
                self.stdout.write(f"\n🔑 Procesando {app_type}...")
                
                # Verificar si ya existe
                existing = AppCredentials.objects.filter(
                    app_type=app_type, 
                    is_active=True
                ).first()
                
                if existing and not options['force']:
                    self.stdout.write(
                        self.style.WARNING(f'⚠️  Claves para {app_type} ya existen. Usa --force para sobrescribir.')
                    )
                    continue
                
                # Generar nuevas claves
                self.stdout.write(f"🔧 Generando par de claves RSA para {app_type}...")
                private_key, public_key = generate_rsa_key_pair()
                
                # Desactivar claves existentes si hay
                if existing:
                    existing.is_active = False
                    existing.save()
                    self.stdout.write(f"🔄 Claves anteriores desactivadas para {app_type}")
                
                # Crear nuevo registro
                app_creds = AppCredentials.objects.create(
                    app_type=app_type,
                    private_key_pem=private_key,
                    public_key_pem=public_key,
                    is_active=True,
                    created_by='management_command'
                )
                
                success_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'✅ Claves generadas exitosamente para {app_type}')
                )
                
                # Mostrar información de la clave
                self.stdout.write(f'📊 ID: {app_creds.id}')
                self.stdout.write(f'📊 Fingerprint: {app_creds.key_fingerprint}')
                
                # Mostrar clave pública para embeber (solo primeras líneas)
                self.stdout.write(f'\n📋 Clave pública para {app_type} (para embeber en la app):')
                self.stdout.write('=' * 60)
                public_lines = public_key.split('\n')
                for line in public_lines[:3]:  # Solo mostrar primeras líneas
                    self.stdout.write(line)
                self.stdout.write('... (clave completa guardada en BD)')
                self.stdout.write('=' * 60)
                
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f'❌ Error generando claves para {app_type}: {str(e)}')
                )
        
        # Resumen final
        self.stdout.write(f'\n🎉 Proceso completado: {success_count} tipos de app procesados exitosamente')
        
        # Mostrar estado actual
        self.stdout.write('\n📊 Estado actual de credenciales:')
        for creds in AppCredentials.objects.filter(is_active=True):
            status = "✅ Activa" if creds.is_usable() else "❌ No usable"
            self.stdout.write(f'   {creds.app_type}: {status}')