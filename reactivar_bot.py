"""
Reactivar el bot despues de un kill switch - Trading-nicabot

Se corre MANUALMENTE cuando Juan Carlos ya revisó la situación y decide que
el bot puede volver a abrir posiciones nuevas.

Uso: correr este script desde GitHub Actions (workflow manual) o localmente.
NO borra el historial de operaciones, solo desactiva el kill switch y
reinicia el pico de capital al valor actual, para que futuras caidas se
midan desde aqui en adelante.
"""

import json
import os

RUTA_ESTADO = 'estado.json'


def main():
    if not os.path.exists(RUTA_ESTADO):
        print('No existe estado.json todavia. Nada que reactivar.')
        return

    with open(RUTA_ESTADO) as f:
        estado = json.load(f)

    if not estado.get('kill_switch_activo'):
        print('El kill switch no esta activo. No hay nada que hacer.')
        return

    print(f"Kill switch estaba activo desde: {estado.get('kill_switch_fecha')}")
    print(f"Capital actual: ${estado['cash']:.2f}")

    estado['kill_switch_activo'] = False
    estado['kill_switch_fecha'] = None
    estado['pico_capital'] = estado['cash']  # reinicia el pico desde hoy

    with open(RUTA_ESTADO, 'w') as f:
        json.dump(estado, f, indent=2)

    print('Kill switch DESACTIVADO. El bot puede volver a abrir posiciones.')
    print('El pico de capital se reinicio al valor actual.')


if __name__ == '__main__':
    main()
