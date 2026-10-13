"""Revisa stops / take-profit / trailing con el precio vivo de Kraken (PAPER).
Lo corre .github/workflows/vigilancia_stops.yml cada ~20 minutos."""
import sys

from nicabot.registro import configurar_logging
from nicabot.vigilancia import ejecutar

if __name__ == '__main__':
    log = configurar_logging()
    try:
        ejecutar()
    except Exception:
        log.exception('La vigilancia de stops fallo')
        sys.exit(1)
