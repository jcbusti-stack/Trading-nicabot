"""Punto de entrada del bot multi-activo en Kraken (paper por defecto).
Toda la logica esta en el paquete nicabot/. Ver README.md."""
import sys

from nicabot.bot import ejecutar
from nicabot.registro import configurar_logging

if __name__ == '__main__':
    log = configurar_logging()
    try:
        ejecutar()
    except Exception:
        log.exception('El bot fallo en este paso')
        sys.exit(1)
