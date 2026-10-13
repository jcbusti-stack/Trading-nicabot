"""Revision mensual por moneda: desactiva pares con expectativa negativa (ver nicabot/revision.py).
Lo corre .github/workflows/revision_mensual.yml el dia 1 de cada mes."""
import sys

from nicabot.registro import configurar_logging
from nicabot.revision import ejecutar

if __name__ == '__main__':
    log = configurar_logging()
    try:
        ejecutar()
    except Exception:
        log.exception('La revision mensual fallo')
        sys.exit(1)
