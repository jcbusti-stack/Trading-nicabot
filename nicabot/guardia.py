"""Guardia de entrada: evita comprar con una senal vieja o con un precio que ya
se movio mucho respecto al cierre de la vela de la senal."""
import pandas as pd

HORAS_VELA = {'1h': 1, '4h': 4, '12h': 12, '1d': 24}


def revisar_entrada(fecha_vela, close_senal, precio_vivo, ahora, cfg_entrada, timeframe='12h'):
    """Devuelve (ok, motivo). fecha_vela = apertura de la vela (indice)."""
    cierre = pd.Timestamp(fecha_vela) + pd.Timedelta(hours=HORAS_VELA.get(timeframe, 12))
    horas = (pd.Timestamp(ahora) - cierre).total_seconds() / 3600
    max_h = cfg_entrada.get('max_horas_desde_cierre')
    if max_h is not None and horas > max_h:
        return False, f'senal vieja ({horas:.1f}h desde el cierre > {max_h}h)'
    if not cfg_entrada.get('usar_precio_vivo', True):
        return True, 'ok'
    if not precio_vivo:
        return False, 'sin precio vivo de Kraken'
    mov = (precio_vivo - close_senal) / close_senal * 100
    if mov < -cfg_entrada.get('max_caida_pct', 2.0):
        return False, f'precio cayo {mov:.2f}% desde el cierre de la senal'
    if mov > cfg_entrada.get('max_subida_pct', 3.0):
        return False, f'precio subio {mov:+.2f}% desde el cierre de la senal'
    return True, 'ok'
