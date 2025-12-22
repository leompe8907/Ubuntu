"""
Handlers y filtros personalizados de logging para manejar encoding en Windows.
"""
import logging
import sys
import re


class UnicodeSafeFilter(logging.Filter):
    """
    Filtro que reemplaza caracteres Unicode problemáticos antes de escribir.
    
    En Windows, reemplaza emojis y caracteres especiales por texto ASCII.
    """
    
    # Mapeo de emojis comunes a texto
    EMOJI_REPLACEMENTS = {
        '🚀': '[INIT]',
        '✅': '[OK]',
        '❌': '[ERROR]',
        '🔄': '[RETRY]',
        '🔑': '[AUTH]',
        '🔍': '[CHECK]',
        '⚠️': '[WARN]',
        '🛑': '[STOP]',
        '🚨': '[ALERT]',
    }
    
    def filter(self, record):
        """
        Filtra el mensaje reemplazando emojis problemáticos.
        """
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            # Reemplazar emojis conocidos
            for emoji, replacement in self.EMOJI_REPLACEMENTS.items():
                record.msg = record.msg.replace(emoji, replacement)
            
            # Reemplazar cualquier otro emoji o carácter no ASCII problemático
            # Solo si estamos en Windows
            if sys.platform == 'win32':
                # Reemplazar cualquier emoji restante con [EMOJI]
                record.msg = re.sub(
                    r'[\U0001F300-\U0001F9FF]|[\u2600-\u27FF]|[\u2700-\u27BF]',
                    '[?]',
                    record.msg
                )
        
        return True


class SafeConsoleHandler(logging.StreamHandler):
    """
    Handler de consola que maneja errores de encoding de forma segura.
    
    En Windows, cuando la consola no soporta UTF-8, reemplaza caracteres
    no codificables en lugar de lanzar excepciones.
    """
    
    def __init__(self, stream=None):
        if stream is None:
            stream = sys.stdout
        super().__init__(stream)
        
        # Agregar el filtro Unicode
        self.addFilter(UnicodeSafeFilter())
        
        # Configurar encoding UTF-8 si es posible
        if hasattr(stream, 'reconfigure'):
            try:
                stream.reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, ValueError):
                # Si no se puede configurar, usar errors='replace' en emit
                pass
    
    def emit(self, record):
        """
        Emite un registro de log, manejando errores de encoding.
        """
        try:
            msg = self.format(record)
            stream = self.stream
            
            # Intentar escribir con encoding seguro
            try:
                if hasattr(stream, 'buffer'):
                    # Para stdout/stderr, usar buffer con UTF-8 y errors='replace'
                    safe_msg = msg.encode('utf-8', errors='replace').decode('utf-8', errors='replace')
                    stream.buffer.write(safe_msg.encode('utf-8', errors='replace'))
                    stream.buffer.write(self.terminator.encode('utf-8', errors='replace'))
                    stream.buffer.flush()
                else:
                    # Fallback: reemplazar caracteres problemáticos
                    safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
                    stream.write(safe_msg + self.terminator)
                    stream.flush()
            except (UnicodeEncodeError, AttributeError, UnicodeDecodeError) as e:
                # Si falla, reemplazar todos los caracteres no ASCII
                safe_msg = msg.encode('ascii', errors='replace').decode('ascii')
                try:
                    stream.write(safe_msg + self.terminator)
                    stream.flush()
                except Exception:
                    # Último recurso: usar handleError
                    self.handleError(record)
                
        except Exception:
            # Si todo falla, usar el handler de errores
            self.handleError(record)

