from django.utils import timezone
from rest_framework import serializers
from django.contrib.auth.hashers import make_password
from .models import (ListOfSubscriber, ListOfSmartcards, SubscriberLoginInfo, SubscriberInfo, UDIDAuthRequest, AuthAuditLog)

class ListOfSubscriberSerializer(serializers.ModelSerializer):
    """Serializer para datos raw de suscriptores desde Panaccess"""
    
    class Meta:
        model = ListOfSubscriber
        fields = '__all__'
        
    def validate_code(self, value):
        """Validar que el código del suscriptor no esté vacío"""
        if not value or not value.strip():
            raise serializers.ValidationError("El código del suscriptor es requerido")
        return value.strip()

class ListOfSmartcardsSerializer(serializers.ModelSerializer):
    """Serializer para datos raw de smartcards desde Panaccess"""
    
    class Meta:
        model = ListOfSmartcards
        fields = '__all__'
        
    def validate_sn(self, value):
        """Validar número de serie de smartcard"""
        if not value or not value.strip():
            raise serializers.ValidationError("El número de serie es requerido")
        return value.strip()

class SubscriberLoginInfoSerializer(serializers.ModelSerializer):
    """Serializer para información de login raw desde Panaccess"""
    
    class Meta:
        model = SubscriberLoginInfo
        fields = '__all__'

class SubscriberInfoSerializer(serializers.ModelSerializer):
    """
    Serializer principal para el sistema UDID.
    Maneja datos consolidados y seguros.
    """
    
    # Campos de solo escritura para passwords
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    pin = serializers.CharField(write_only=True, required=False, allow_blank=True)
    
    # Campos de solo lectura para datos sensibles
    password_hash = serializers.CharField(read_only=True)
    pin_hash = serializers.CharField(read_only=True)
    failed_login_attempts = serializers.IntegerField(read_only=True)
    locked_until = serializers.DateTimeField(read_only=True)
    
    class Meta:
        model = SubscriberInfo
        fields = [
            'id', 'subscriber_code', 'sn', 'first_name', 'last_name',
            'lastActivation', 'lastContact', 'lastServiceListDownload',
            'lastActivationIP', 'lastApiKeyId', 'products', 'packages',
            'packageNames', 'model', 'login1', 'login2', 'activated',
            'activation_date', 'last_login', 'created_at', 'updated_at',
            # Write-only fields
            'password', 'pin',
            # Read-only fields  
            'password_hash', 'pin_hash', 'failed_login_attempts', 'locked_until'
        ]
        
    def create(self, validated_data):
        """Crear nuevo SubscriberInfo con passwords hasheadas"""
        password = validated_data.pop('password', None)
        pin = validated_data.pop('pin', None)
        
        instance = SubscriberInfo.objects.create(**validated_data)
        
        if password:
            instance.set_password(password)
        if pin:
            instance.set_pin(pin)
            
        instance.save()
        return instance
        
    def update(self, instance, validated_data):
        """Actualizar SubscriberInfo con passwords hasheadas"""
        password = validated_data.pop('password', None)
        pin = validated_data.pop('pin', None)
        
        # Actualizar campos normales
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
            
        # Actualizar passwords si se proporcionan
        if password:
            instance.set_password(password)
        if pin:
            instance.set_pin(pin)
            
        instance.save()
        return instance

class UDIDAuthRequestSerializer(serializers.ModelSerializer):
    """Serializer para requests de autenticación UDID"""
    
    # Campos sensibles de solo lectura
    temp_token = serializers.CharField(read_only=True)
    attempts_count = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = UDIDAuthRequest
        fields = [
            'id', 'udid', 'subscriber_code', 'status', 'created_at',
            'expires_at', 'validated_at', 'used_at', 'validated_by_operator',
            'client_ip', 'user_agent', 'device_fingerprint',
            # Read-only fields
            'temp_token', 'attempts_count'
        ]
        read_only_fields = ['udid', 'expires_at', 'created_at']
        
    def validate_subscriber_code(self, value):
        """Validar que el subscriber_code existe"""
        if not SubscriberInfo.objects.filter(subscriber_code=value).exists():
            raise serializers.ValidationError("Código de suscriptor no válido")
        return value

class AuthAuditLogSerializer(serializers.ModelSerializer):
    """Serializer para logs de auditoría"""
    
    class Meta:
        model = AuthAuditLog
        fields = '__all__'
        read_only_fields = ['timestamp']

# Serializers para APIs públicas (sin datos sensibles)
class PublicSubscriberInfoSerializer(serializers.ModelSerializer):
    """Serializer público sin datos sensibles"""
    
    class Meta:
        model = SubscriberInfo
        fields = [
            'subscriber_code','first_name', 'last_name', 'sn', 'activated',
            'products', 'packages', 'packageNames', 'model','lastActivation',
            'lastActivationIP', 'lastServiceListDownload', 'lastActivation'
        ]

class UDIDValidationSerializer(serializers.Serializer):
    """Serializer para validación de UDID"""
    
    udid = serializers.CharField(max_length=100)
    subscriber_code = serializers.CharField(max_length=100)
    pin = serializers.CharField(max_length=20, write_only=True)
    operator_id = serializers.CharField(max_length=100, required=False)
    
    def validate(self, attrs):
        """Validación completa del UDID"""
        udid = attrs.get('udid')
        subscriber_code = attrs.get('subscriber_code')
        pin = attrs.get('pin')
        
        # Verificar que el UDID request existe y es válido
        try:
            udid_request = UDIDAuthRequest.objects.get(udid=udid, subscriber_code=subscriber_code)
        except UDIDAuthRequest.DoesNotExist:
            raise serializers.ValidationError("UDID no válido o expirado")
            
        if not udid_request.is_valid():
            raise serializers.ValidationError("UDID no válido, expirado o con demasiados intentos")
            
        # Verificar PIN del suscriptor
        try:
            subscriber = SubscriberInfo.objects.get(subscriber_code=subscriber_code)
        except SubscriberInfo.DoesNotExist:
            raise serializers.ValidationError("Suscriptor no encontrado")
            
        if subscriber.is_locked():
            raise serializers.ValidationError("Cuenta bloqueada")
            
        if not subscriber.check_pin(pin):
            # Incrementar intentos fallidos
            subscriber.failed_login_attempts += 1
            if subscriber.failed_login_attempts >= 5:
                subscriber.lock_account()
            subscriber.save()
            raise serializers.ValidationError("PIN incorrecto")
            
        attrs['udid_request'] = udid_request
        attrs['subscriber'] = subscriber
        return attrs

class LoginSerializer(serializers.Serializer):
    """Serializer para login de suscriptores"""
    
    subscriber_code = serializers.CharField(max_length=100)
    password = serializers.CharField(max_length=100, write_only=True)
    
    def validate(self, attrs):
        """Validar credenciales del suscriptor"""
        subscriber_code = attrs.get('subscriber_code')
        password = attrs.get('password')
        
        try:
            subscriber = SubscriberInfo.objects.get(subscriber_code=subscriber_code)
        except SubscriberInfo.DoesNotExist:
            raise serializers.ValidationError("Credenciales inválidas")
            
        if subscriber.is_locked():
            raise serializers.ValidationError("Cuenta bloqueada")
            
        if not subscriber.check_password(password):
            subscriber.failed_login_attempts += 1
            if subscriber.failed_login_attempts >= 5:
                subscriber.lock_account()
            subscriber.save()
            raise serializers.ValidationError("Credenciales inválidas")
            
        # Reset intentos fallidos en login exitoso
        subscriber.failed_login_attempts = 0
        subscriber.last_login = serializers.DateTimeField().to_representation(
            serializers.DateTimeField().to_internal_value(None)
        )
        subscriber.save()
        
        attrs['subscriber'] = subscriber
        return attrs
    
class UDIDAssociationSerializer(serializers.Serializer):
    udid = serializers.CharField(max_length=100)
    subscriber_code = serializers.CharField(max_length=100)
    sn = serializers.CharField(max_length=100)
    operator_id = serializers.CharField(max_length=100)
    method = serializers.ChoiceField(choices=[('automatic', 'Automatic'), ('manual', 'Manual')], default='automatic')

    def validate(self, attrs):
        udid = attrs['udid']
        subscriber_code = attrs['subscriber_code']
        sn = attrs['sn']

        # Validar existencia de la solicitud de UDID
        try:
            udid_request = UDIDAuthRequest.objects.get(udid=udid)
        except UDIDAuthRequest.DoesNotExist:
            raise serializers.ValidationError("UDID no encontrado")

        if not udid_request.is_valid():
            raise serializers.ValidationError("UDID inválido, expirado o con demasiados intentos")

        # ✅ Validar existencia del SN
        try:
            subscriber = SubscriberInfo.objects.get(sn=sn)
        except SubscriberInfo.DoesNotExist:
            raise serializers.ValidationError("Smartcard SN no encontrada")

        # ✅ Validar que el SN pertenezca al subscriber_code indicado
        if subscriber.subscriber_code != subscriber_code:
            raise serializers.ValidationError("Este SN no pertenece al subscriber_code indicado")

        # ✅ Validar que el subscriber esté activado
        if not subscriber.activated:
            subscriber.activated = True
            subscriber.activation_date = timezone.now()
            subscriber.save()

        # ✅ Validar si la cuenta está bloqueada
        if subscriber.is_locked():
            raise serializers.ValidationError("La cuenta del suscriptor está bloqueada")

        # ✅ Verificar que no esté asociado a otro UDID activo
        conflict_qs = UDIDAuthRequest.objects.filter(
            sn=sn,
            subscriber_code=subscriber_code,
            status__in=['validated', 'used']
        ).exclude(udid=udid)

        if conflict_qs.exists():
            raise serializers.ValidationError("Este SN ya está asociado a otro UDID activo")

        # Pasar los objetos validados para usarlos en la vista
        attrs['subscriber'] = subscriber
        attrs['udid_request'] = udid_request
        return attrs