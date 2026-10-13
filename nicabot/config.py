"""Carga de configuracion desde config.json (con valores por defecto)."""
import copy
import json
import os

RUTA_CONFIG_DEFECTO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')

DEFECTO = {
    'exchange': 'kraken', 'dir_cache_velas': None, 'pares': ['SOL/USD'], 'ajustes_por_par': {},
    'portafolio': {'max_posiciones_abiertas': 5, 'max_exposicion_total_pct': 100.0, 'max_por_activo_pct': 25.0},
    'timeframe': '12h', 'velas': 300, 'capital_inicial': 1000.0,
    'ruta_estado': 'estado.json',
    'estrategia': {'ema_rapida': 9, 'ema_lenta': 21, 'ema_tendencia': 200, 'margen_tendencia_pct': 1.5,
                   'rsi_periodo': 14, 'rsi_max_entrada': 65, 'rsi_salida': 75, 'atr_periodo': 14,
                   'min_velas_ema': 250, 'semilla_1d': True, 'modo_entrada': 'cruce', 'salida_macd': True,
                   'max_extension_atr': None,
                   'filtros': {'volumen': {'enabled': False, 'periodo': 20, 'factor': 1.0},
                               'volatilidad': {'enabled': False, 'atr_pct_max': None},
                               'multi_marco': {'enabled': False, 'ema_diaria': 50, 'usar_diario': True,
                                               'usar_macd_4h': True}}},
    'entrada': {'usar_precio_vivo': True, 'max_horas_desde_cierre': 3.0,
                'max_caida_pct': 2.0, 'max_subida_pct': 3.0},
    'riesgo': {'riesgo_por_operacion_pct': 1.0, 'max_exposicion_pct': 100.0, 'stop_modo': 'pct',
               'stop_pct': 5.0, 'stop_atr_mult': 2.0, 'take_profit_r': 3.0, 'trailing_atr_mult': None,
               'tp_parcial_r': None, 'tp_parcial_frac': 0.5, 'breakeven_tras_parcial': True,
               'breakeven_offset_pct': 0.6, 'max_velas_posicion': None, 'trailing_solo_tras_parcial': False,
               'kill_switch_pct': 25.0, 'kill_switch_cierra_posicion': False},
    'costos': {'comision_pct': 0.26, 'slippage_pct': 0.05},
    'live': {'max_orden_usdt': 50.0, 'dry_run_por_defecto': True},
    'filtro_btc': {'enabled': True, 'par': 'BTC/USD', 'precio_sobre_ema200': True, 'ema9_sobre_ema21': True},
    'revision_mensual': {'enabled': True, 'ultimas_n': 12, 'min_operaciones': 8, 'enfriamiento_dias': 60},
    'drive': {'folder_id': '', 'nombre_senal': 'senal_sol.txt', 'nombre_resumen': 'senal_multi.txt'},
}


def _mezclar(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _mezclar(base[k], v)
        else:
            base[k] = v
    return base


def cargar_config(ruta=None):
    cfg = copy.deepcopy(DEFECTO)
    ruta = ruta or os.environ.get('NICABOT_CONFIG') or RUTA_CONFIG_DEFECTO
    if os.path.exists(ruta):
        with open(ruta, encoding='utf-8') as f:
            _mezclar(cfg, json.load(f))
    # La variable de entorno tiene prioridad sobre el folder del config
    folder_env = (os.environ.get('DRIVE_FOLDER_ID') or '').strip()
    if folder_env:
        cfg['drive']['folder_id'] = folder_env
    if 'par' in cfg and cfg['par'] not in cfg['pares']:  # compatibilidad con config de un solo par
        cfg['pares'] = [cfg['par']] + cfg['pares']
    for par in cfg['pares']:
        if config_par(cfg, par)['riesgo']['stop_modo'] not in ('pct', 'atr'):
            raise ValueError(f"riesgo.stop_modo invalido para {par}")
    return cfg


def config_par(cfg, par):
    """Config efectiva de un par: la global + ajustes_por_par[par]."""
    efectiva = copy.deepcopy(cfg)
    _mezclar(efectiva, copy.deepcopy(cfg.get('ajustes_por_par', {}).get(par, {})))
    return efectiva
