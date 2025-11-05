from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.db.models.signals import post_save

import secrets
import uuid

from .utils.encryption import encrypt_value, decrypt_value

class ListOfSubscriber(models.Model):
    id = models.CharField(primary_key=True, unique=True, max_length=100)
    code = models.CharField(max_length=100, blank=True, null=True, unique=True)
    lastName = models.CharField(max_length=100, null=True, blank=True)
    firstName = models.CharField(max_length=100, null=True, blank=True)
    smartcards = models.JSONField(null=True, blank=True)
    hcId = models.CharField(max_length=100, null=True, blank=True)
    hcName = models.CharField(max_length=100, null=True, blank=True)
    country = models.CharField(max_length=100, null=True, blank=True)
    city = models.CharField(max_length=100, null=True, blank=True)
    zip = models.CharField(max_length=20, null=True, blank=True)
    address = models.CharField(max_length=255, null=True, blank=True)
    created = models.DateField(null=True, blank=True)
    modified = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.data

class ListOfSmartcards(models.Model):
    sn = models.CharField(max_length=100, unique=True, null=True, blank=True)
    subscriberCode = models.CharField(max_length=100, null=True, blank=True)
    lastName = models.CharField(max_length=100, blank=True, null=True)
    firstName = models.CharField(max_length=100, blank=True, null=True)
    pin = models.CharField(max_length=100, null=True, blank=True)
    pairedBox = models.CharField(max_length=100, null=True, blank=True)
    products = models.JSONField(null=True, blank=True)
    casIds = models.CharField(max_length=100, null=True, blank=True)
    packages = models.JSONField(null=True, blank=True)
    packageNames = models.JSONField(null=True, blank=True)
    configId = models.CharField(max_length=100, null=True, blank=True)
    configProtected = models.BooleanField(default=False, null=True, blank=True)
    alias = models.CharField(max_length=100, null=True, blank=True)
    regionId = models.IntegerField(null=True, blank=True)
    regionName = models.CharField(max_length=100, null=True, blank=True)
    masterSn = models.CharField(max_length=100, null=True, blank=True)
    hcId = models.CharField(max_length=100, null=True, blank=True)
    lastActivation = models.DateTimeField(null=True, blank=True)
    lastContact = models.DateTimeField(null=True, blank=True)
    lastServiceListDownload = models.DateTimeField(null=True, blank=True)
    lastActivationIP = models.CharField(max_length=100, null=True, blank=True)
    firmwareVersion = models.CharField(max_length=100, null=True, blank=True)
    camlibVersion = models.CharField(max_length=100, null=True, blank=True)
    lastApiKeyId = models.CharField(max_length=100, null=True, blank=True)
    blacklisted = models.BooleanField(default=False, null=True, blank=True)
    disabled = models.BooleanField(default=False, null=True, blank=True)
    defect = models.BooleanField(default=False, null=True, blank=True)
    stbModel = models.CharField(max_length=100, null=True, blank=True)
    stbVendor = models.CharField(max_length=100, null=True, blank=True)
    stbChipset = models.CharField(max_length=100, null=True, blank=True)
    mac = models.CharField(max_length=100, null=True, blank=True)
    manufacturer = models.CharField(max_length=100, null=True, blank=True)
    model = models.CharField(max_length=100, null=True, blank=True)
    fingerprint = models.CharField(max_length=100, null=True, blank=True)
    hardware = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return self.data

class SubscriberLoginInfo(models.Model):
    subscriberCode = models.CharField(max_length=100, null=True, blank=True)
    login1 = models.IntegerField(null=True, blank=True)
    login2 = models.CharField(max_length=100, null=True, blank=True)
    additionalLogins = models.JSONField(null=True, blank=True)
    password = models.CharField(max_length=100, null=True, blank=True)
    licenses = models.JSONField(null=True, blank=True)

    def __str__(self):
        return self.data

class SubscriberInfo(models.Model):
    # Subscriber fields
    subscriber_code = models.CharField(max_length=100)

    # Smartcard fields
    sn = models.CharField(max_length=100, null=True, blank=True)
    pin_hash = models.CharField(max_length=255, null=True, blank=True)  # PIN hasheado
    first_name = models.CharField(max_length=100, null=True, blank=True)
    last_name = models.CharField(max_length=100, null=True, blank=True)
    lastActivation = models.DateTimeField(null=True, blank=True)
    lastContact = models.DateTimeField(null=True, blank=True)
    lastServiceListDownload = models.DateTimeField(null=True, blank=True)
    lastActivationIP = models.CharField(max_length=100, null=True, blank=True)
    lastApiKeyId = models.CharField(max_length=100, null=True, blank=True)
    products = models.JSONField(null=True, blank=True)
    packages = models.JSONField(null=True, blank=True)
    packageNames = models.JSONField(null=True, blank=True)
    model = models.CharField(max_length=100, null=True, blank=True)

    # Login fields - passwords hasheadas
    login1 = models.IntegerField(null=True, blank=True)
    login2 = models.CharField(max_length=100, null=True, blank=True)
    password_hash = models.CharField(max_length=255, null=True, blank=True)

    # Control de activación
    activated = models.BooleanField(default=False)
    activation_date = models.DateTimeField(null=True, blank=True)
    
    # Security fields
    failed_login_attempts = models.IntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_login = models.DateTimeField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['subscriber_code']),
            models.Index(fields=['sn']),
            models.Index(fields=['activated']),
        ]

    def set_password(self, raw_password):
        self.password_hash = encrypt_value(raw_password)
    
    def get_password(self):
        return decrypt_value(self.password_hash) if self.password_hash else None
    
    def check_password(self, raw_password):
        return self.get_password() == raw_password
    
    def set_pin(self, raw_pin):
        self.pin_hash = encrypt_value(raw_pin)
    
    def get_pin(self):
        return decrypt_value(self.pin_hash) if self.pin_hash else None
    
    def check_pin(self, raw_pin):
        return self.get_pin() == raw_pin

    
    def is_locked(self):
        """Verificar si la cuenta está bloqueada"""
        if not self.locked_until:
            return False
        return timezone.now() < self.locked_until
    
    def lock_account(self, minutes=30):
        """Bloquear cuenta por X minutos"""
        self.locked_until = timezone.now() + timedelta(minutes=minutes)
        self.save()
    
    def unlock_account(self):
        """Desbloquear cuenta"""
        self.locked_until = None
        self.failed_login_attempts = 0
        self.save()
    
    def activate(self):
        """Activar subscriber"""
        self.activated = True
        self.activation_date = timezone.now()
        self.save()

    def __str__(self):
        return self.data

class AuthAuditLog(models.Model):
    ACTION_TYPES = [
        ('udid_generated', 'UDID Generated'),
        ('udid_validated', 'UDID Validated'),
        ('udid_used', 'UDID Used'),
        ('login_attempt', 'Login Attempt'),
        ('login_success', 'Login Success'),
        ('login_failed', 'Login Failed'),
        ('account_locked', 'Account Locked'),
        ('account_unlocked', 'Account Unlocked'),
    ]
    
    action_type = models.CharField(max_length=20, choices=ACTION_TYPES)
    subscriber_code = models.CharField(max_length=100, null=True, blank=True)
    udid = models.CharField(max_length=100, null=True, blank=True)
    operator_id = models.CharField(max_length=100, null=True, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    details = models.JSONField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['subscriber_code']),
            models.Index(fields=['action_type']),
        ]
    
    def __str__(self):
        return f"{self.action_type} - {self.subscriber_code} - {self.timestamp}"

class AppCredentials(models.Model):
    APP_TYPES = [
        ('android_tv', 'Android TV'),
        ('android_mobile', 'Android Mobile'),
        ('10foot', '10 Foot UI'),
        ('set_top_box', 'Set Top Box'),
        ('ios_mobile', 'iOS Mobile'),
        ('ios_tv', 'Apple TV'),
        ('web_player', 'Web Player'),
    ]

    app_type = models.CharField(max_length=50, choices=APP_TYPES)
    app_version = models.CharField(max_length=20, default='1.0', db_index=True)

    # ✅ Claves RSA almacenadas correctamente
    private_key_pem = models.TextField(help_text="Clave privada - NUNCA enviar al cliente")
    public_key_pem = models.TextField(help_text="Clave pública - se embebe en aplicaciones")

    # ✅ Control de seguridad mejorado
    is_active = models.BooleanField(default=True)
    is_compromised = models.BooleanField(default=False)  # Para marcar claves comprometidas
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # Para rotación automática
    
    # ✅ Auditoría de uso
    last_used = models.DateTimeField(null=True, blank=True)
    usage_count = models.IntegerField(default=0)
    
    # ✅ Metadatos de seguridad
    key_fingerprint = models.CharField(max_length=64, null=True, blank=True)  # SHA256 del public key
    created_by = models.CharField(max_length=100, null=True, blank=True)
    revoked_by = models.CharField(max_length=100, null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = [['app_type', 'app_version']]
        indexes = [
            models.Index(fields=['app_type', 'app_version', 'is_active']),
            models.Index(fields=['is_active', 'expires_at']),
            models.Index(fields=['key_fingerprint']),
        ]
    
    def save(self, *args, **kwargs):
        # Generar fingerprint de la clave pública
        if self.public_key_pem and not self.key_fingerprint:
            import hashlib
            self.key_fingerprint = hashlib.sha256(
                self.public_key_pem.encode()
            ).hexdigest()[:16]
        super().save(*args, **kwargs)
    
    def is_expired(self):
        if not self.expires_at:
            return False
        return timezone.now() > self.expires_at
    
    def is_usable(self):
        return (
            self.is_active and 
            not self.is_compromised and 
            not self.is_expired()
        )
    
    def revoke(self, reason="Manual revocation", revoked_by=None):
        """Revocar credenciales de forma segura"""
        self.is_active = False
        self.is_compromised = True
        self.revoked_at = timezone.now()
        self.revoked_by = revoked_by
        self.revocation_reason = reason
        self.save()
    
    def __str__(self):
        status = "✅" if self.is_usable() else "❌"
        return f"{status} {self.app_type} v{self.app_version}"

class EncryptedCredentialsLog(models.Model):
    """
    ✅ Log de credenciales encriptadas enviadas
    Permite auditoría sin exponer datos sensibles
    """
    udid = models.CharField(max_length=100, db_index=True)
    subscriber_code = models.CharField(max_length=100, db_index=True)
    sn = models.CharField(max_length=100, null=True, blank=True)
    
    # Información de la aplicación
    app_type = models.CharField(max_length=50)
    app_version = models.CharField(max_length=20)
    app_credentials_id = models.ForeignKey(AppCredentials, on_delete=models.CASCADE)
    
    # Metadatos de encriptación (NO los datos encriptados)
    encryption_algorithm = models.CharField(max_length=50, default="AES-256-CBC + RSA-OAEP")
    encrypted_data_hash = models.CharField(max_length=64)  # SHA256 del payload encriptado
    
    # Auditoría
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    
    # Estado de entrega
    delivered_successfully = models.BooleanField(default=False)
    delivery_attempts = models.IntegerField(default=0)
    last_delivery_error = models.TextField(null=True, blank=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['subscriber_code', 'app_type']),
            models.Index(fields=['udid']),
        ]
    
    def __str__(self):
        return f"Encrypted delivery: {self.subscriber_code} - {self.app_type} - {self.timestamp}"

class UDIDAuthRequest(models.Model):
    STATUSES = [
        ('pending', 'Pending'),
        ('validated', 'Validated'),
        ('expired', 'Expired'),
        ('revoked', 'Revoked'),
        ('used', 'Used'),
    ]
    
    METHODS = [
        ('automatic', 'Automatic'),
        ('manual', 'Manual'),
    ]
    
    udid = models.CharField(max_length=100, unique=True, db_index=True)
    subscriber_code = models.CharField(max_length=100, db_index=True)
    sn = models.CharField(max_length=100, null=True, blank=True)
    temp_token = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUSES, default='pending')
    method = models.CharField(max_length=20, choices=METHODS, default='automatic')
    lastActivation = models.DateTimeField(null=True, blank=True)
    lastServiceListDownload = models.DateTimeField(null=True, blank=True)
    lastActivationIP = models.CharField(max_length=100, null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    validated_at = models.DateTimeField(null=True, blank=True)
    used_at = models.DateTimeField(null=True, blank=True)
    
    # Security fields
    validated_by_operator = models.CharField(max_length=100, null=True, blank=True)
    client_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    attempts_count = models.IntegerField(default=0)
    
    # Device fingerprinting
    device_fingerprint = models.CharField(max_length=255, null=True, blank=True)
    
    # Campos para trackear app y versión
    app_type = models.CharField(max_length=50, null=True, blank=True)
    app_version = models.CharField(max_length=20, null=True, blank=True)
    encrypted_response_sent = models.BooleanField(default=False)
    
    # Nuevos campos de seguridad
    app_credentials_used = models.ForeignKey(AppCredentials, on_delete=models.SET_NULL, null=True, blank=True)
    encryption_successful = models.BooleanField(default=False)
    credentials_delivered = models.BooleanField(default=False)
    
    # Rate limiting mejorado
    requests_from_ip_count = models.IntegerField(default=0)
    suspicious_activity = models.BooleanField(default=False)
    
    class Meta:
        indexes = [
            models.Index(fields=['udid', 'status']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['subscriber_code']),
            models.Index(fields=['sn']),
            models.Index(fields=['subscriber_code', 'sn']),
            models.Index(fields=['status', 'expires_at']),
        ]
    
    def save(self, *args, **kwargs):
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=15)
        if not self.udid:
            self.udid = str(uuid.uuid4())
        if not self.temp_token:
            self.temp_token = secrets.token_urlsafe(32)
        
        # ✅ LÓGICA PRINCIPAL: Detener expiración automáticamente
        old_status = None
        if self.pk:  # Solo si el objeto ya existe en BD
            try:
                old_instance = UDIDAuthRequest.objects.get(pk=self.pk)
                old_status = old_instance.status
            except UDIDAuthRequest.DoesNotExist:
                pass
        
        # ✅ Si el status cambió a 'validated' o 'used', detener expiración
        if old_status and old_status != self.status:
            if self.status in ['validated', 'used']:
                self.stop_expiration()
        
        super().save(*args, **kwargs)
    
    def is_expired(self):
        """✅ Mejorado: Si está validated o used, nunca expira"""
        if self.status in ['validated', 'used']:
            return False
        return timezone.now() > self.expires_at
    
    def is_valid(self):
        """✅ Mejorado: Considera el estado para determinar validez"""
        return (
            self.status == 'pending' and
            not self.is_expired() and
            self.attempts_count < 5
        )
    
    def stop_expiration(self):
        """✅ NUEVA: Detener la expiración del UDID (versión simple)"""
        # Establecer fecha muy lejana en el futuro
        self.expires_at = timezone.now() + timedelta(days=3650)
    
    def validate_udid(self, operator=None):
        """✅ NUEVA: Método helper para validar UDID"""
        if self.status == 'pending' and not self.is_expired():
            self.status = 'validated'
            self.validated_at = timezone.now()
            if operator:
                self.validated_by_operator = operator
            # La expiración se detendrá automáticamente en save()
            self.save()
            return True
        return False
    
    def mark_as_used(self):
        """✅ MEJORADO: Marcar como usado y detener expiración automáticamente"""
        self.status = 'used'
        self.used_at = timezone.now()
        # La expiración se detendrá automáticamente en save()
        self.save()

    def validate_app_credentials(self):
        """Validar que existan credenciales para el tipo de app"""
        if self.app_type:
            return AppCredentials.objects.filter(
                app_type=self.app_type,
                is_active=True
            ).exists()
        return True
    
    def mark_credentials_delivered(self, app_credentials):
        """Marcar que las credenciales fueron entregadas exitosamente"""
        self.credentials_delivered = True
        self.encryption_successful = True
        self.app_credentials_used = app_credentials
        self.save()
        
        # Actualizar estadísticas de uso de las credenciales
        app_credentials.last_used = timezone.now()
        app_credentials.usage_count += 1
        app_credentials.save()
    
    def get_expiration_info(self):
        """✅ NUEVA: Información sobre el estado de expiración"""
        if self.status in ['validated', 'used']:
            return {
                'expires': False,
                'status': self.status,
                'message': f'UDID {self.status} - expiration stopped'
            }
        else:
            time_left = self.expires_at - timezone.now()
            return {
                'expires': True,
                'expires_at': self.expires_at,
                'is_expired': self.is_expired(),
                'time_remaining': time_left if time_left.total_seconds() > 0 else None
            }
    
    def __str__(self):
        expiry_info = "∞" if self.status in ['validated', 'used'] else "⏰"
        return f"UDID Auth: {self.udid} - {self.status} {expiry_info}"

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    operator_code = models.CharField(max_length=50)
    document_number = models.CharField(max_length=20, null=True, blank=True, unique=True)

    def __str__(self):
        return f"{self.user.username} - {self.operator_code}"

# Crear automáticamente el perfil cuando se crea un usuario
@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance)