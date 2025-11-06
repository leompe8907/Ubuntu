#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script para desasociar todos los UDIDs asociados a subscriber_code - sn.

Este script busca todos los UDIDAuthRequest que tienen:
- status en ['validated', 'used', 'expired']
- subscriber_code y sn no nulos

Y los desasocia usando la misma lógica que DisassociateUDIDView.

Ejecutar: python desasociar_todos_udids.py
"""

import os
import sys
import django
from datetime import datetime

# Configurar Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ubuntu.settings')
django.setup()

from udid.models import UDIDAuthRequest, AuthAuditLog
from django.utils import timezone
from django.db import transaction

def desasociar_todos_udids(dry_run=False, reason='Bulk disassociation'):
    """
    Desasocia todos los UDIDs asociados a subscriber_code - sn.
    
    Args:
        dry_run: Si es True, solo muestra qué se desasociaría sin hacer cambios
        reason: Razón para la desasociación
    """
    print("\n" + "="*70)
    print("  DESASOCIACIÓN MASIVA DE UDIDs")
    print("="*70)
    print(f"\nModo: {'DRY RUN (solo simulación)' if dry_run else 'EJECUCIÓN REAL'}")
    print(f"Razón: {reason}")
    print(f"Fecha/Hora: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Buscar todos los UDIDs asociados
    udids_asociados = UDIDAuthRequest.objects.filter(
        status__in=['validated', 'used', 'expired'],
        subscriber_code__isnull=False,
        sn__isnull=False
    ).exclude(
        subscriber_code=''
    ).exclude(
        sn=''
    ).order_by('created_at')
    
    total = udids_asociados.count()
    
    print(f"UDIDs encontrados para desasociar: {total}\n")
    
    if total == 0:
        print("No hay UDIDs asociados para desasociar.")
        return
    
    # Mostrar resumen por estado
    print("Resumen por estado:")
    for status in ['validated', 'used', 'expired']:
        count = udids_asociados.filter(status=status).count()
        if count > 0:
            print(f"  - {status}: {count}")
    print()
    
    # Confirmar si no es dry_run
    if not dry_run:
        respuesta = input(f"¿Deseas desasociar {total} UDIDs? (s/n): ")
        if respuesta.lower() != 's':
            print("Operación cancelada.")
            return
    
    # Procesar desasociaciones
    exitosos = 0
    errores = 0
    detalles_errores = []
    
    print("\nProcesando desasociaciones...\n")
    
    for udid_request in udids_asociados:
        try:
            if dry_run:
                print(f"[DRY RUN] Desasociaría: UDID={udid_request.udid[:16]}..., "
                      f"subscriber_code={udid_request.subscriber_code}, "
                      f"sn={udid_request.sn}, status={udid_request.status}")
                exitosos += 1
            else:
                with transaction.atomic():
                    # Bloquear la fila para actualización
                    req = UDIDAuthRequest.objects.select_for_update().get(pk=udid_request.pk)
                    
                    # Verificar que aún está asociado
                    if not req.sn or req.status not in ['validated', 'used', 'expired']:
                        print(f"[SKIP] UDID {req.udid[:16]}... ya no está asociado")
                        continue
                    
                    old_sn = req.sn
                    old_status = req.status
                    subscriber_code = req.subscriber_code
                    
                    # Cambiar estado y limpiar SN
                    req.sn = None
                    req.status = 'revoked'
                    req.revoked_at = timezone.now()
                    req.revoked_reason = reason
                    req.save()
                    
                    # Log de auditoría
                    AuthAuditLog.objects.create(
                        action_type='udid_revoked',
                        udid=req.udid,
                        subscriber_code=subscriber_code,
                        operator_id='bulk_disassociation',
                        client_ip='127.0.0.1',
                        user_agent='BulkDisassociationScript/1.0',
                        details={
                            "old_sn": old_sn,
                            "old_status": old_status,
                            "revoked_at": timezone.now().isoformat(),
                            "reason": reason,
                            "bulk_operation": True
                        }
                    )
                    
                    exitosos += 1
                    if exitosos % 10 == 0:
                        print(f"  Progreso: {exitosos}/{total} desasociados...")
        
        except Exception as e:
            errores += 1
            error_msg = f"Error desasociando UDID {udid_request.udid[:16]}...: {str(e)}"
            detalles_errores.append(error_msg)
            print(f"[ERROR] {error_msg}")
    
    # Mostrar resultados
    print("\n" + "="*70)
    print("  RESULTADOS")
    print("="*70)
    print(f"\nTotal procesados: {total}")
    print(f"Desasociaciones exitosas: {exitosos}")
    print(f"Errores: {errores}")
    
    if errores > 0:
        print(f"\nErrores encontrados:")
        for error in detalles_errores[:10]:  # Mostrar solo los primeros 10
            print(f"  - {error}")
        if len(detalles_errores) > 10:
            print(f"  ... y {len(detalles_errores) - 10} errores más")
    
    if dry_run:
        print("\n[DRY RUN] No se realizaron cambios reales en la base de datos.")
    else:
        print(f"\n[OK] Desasociacion completada: {exitosos} UDIDs desasociados exitosamente.")
    
    print(f"\nFecha/Hora de finalización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70 + "\n")

def main():
    """Función principal"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Desasociar todos los UDIDs asociados a subscriber_code - sn'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Ejecutar en modo simulación sin hacer cambios reales'
    )
    parser.add_argument(
        '--reason',
        type=str,
        default='Bulk disassociation',
        help='Razón para la desasociación (default: "Bulk disassociation")'
    )
    
    args = parser.parse_args()
    
    desasociar_todos_udids(dry_run=args.dry_run, reason=args.reason)

if __name__ == '__main__':
    main()

