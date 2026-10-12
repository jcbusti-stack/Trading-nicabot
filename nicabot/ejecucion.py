"""Ejecucion de ordenes REALES en Kraken (OPCIONAL, apagado por defecto).

Para que se envie una orden real deben cumplirse TODAS:
  - MODO=live
  - CONFIRMO_ORDENES_REALES=SI
  - KRAKEN_TRADING_API_KEY y KRAKEN_TRADING_API_SECRET definidos. Es una llave
    DISTINTA a la de solo lectura (KRAKEN_API_KEY) que usa main.py. Crearla
    en Kraken con permiso de "crear/modificar ordenes" y SIN permiso de retiro.
  - DRY_RUN=0 (si no, solo se registra la orden que SE HABRIA enviado)
Cada compra se limita a live.max_orden_usdt (en USD) del config.
"""
import logging
import os

log = logging.getLogger('nicabot.ejecucion')
VARS_LLAVES = ('KRAKEN_TRADING_API_KEY', 'KRAKEN_TRADING_API_SECRET')


class ModoNoPermitido(Exception):
    pass


def modo_actual():
    return (os.environ.get('MODO') or 'paper').strip().lower()


def es_dry_run(cfg):
    v = os.environ.get('DRY_RUN')
    if v is None:
        return bool(cfg['live'].get('dry_run_por_defecto', True))
    return v.strip().lower() not in ('0', 'no', 'false')


def verificar_live():
    """Lanza ModoNoPermitido si falta alguna salvaguarda. Devuelve las llaves."""
    if modo_actual() != 'live':
        raise ModoNoPermitido('MODO no es live')
    if (os.environ.get('CONFIRMO_ORDENES_REALES') or '').strip() != 'SI':
        raise ModoNoPermitido('Falta CONFIRMO_ORDENES_REALES=SI')
    llaves = {k: (os.environ.get(k) or '').strip() for k in VARS_LLAVES}
    faltan = [k for k, v in llaves.items() if not v]
    if faltan:
        raise ModoNoPermitido(f'Faltan secretos: {", ".join(faltan)}')
    return llaves


class EjecutorKraken:
    def __init__(self, cfg, exchange=None):
        self.cfg = cfg
        self.dry_run = es_dry_run(cfg)
        self.max_usd = float(cfg['live']['max_orden_usdt'])
        if exchange is not None:
            self.ex = exchange
        else:
            llaves = verificar_live()
            import ccxt
            self.ex = ccxt.kraken({'apiKey': llaves['KRAKEN_TRADING_API_KEY'],
                                   'secret': llaves['KRAKEN_TRADING_API_SECRET'], 'enableRateLimit': True})
            self.ex.load_markets()
        log.warning('*** MODO LIVE ACTIVO en Kraken (dry_run=%s, max por orden=%.2f USD) ***', self.dry_run, self.max_usd)

    def _precision(self, par, cantidad):
        try:
            if getattr(self.ex, 'markets', None):
                return float(self.ex.amount_to_precision(par, cantidad))
        except Exception as e:
            log.warning('No se pudo ajustar precision de %s: %s', par, e)
        return cantidad

    def comprar(self, par, cantidad, precio_ref):
        if cantidad * precio_ref > self.max_usd:
            log.warning('Orden recortada al tope: %.2f -> %.2f USD', cantidad * precio_ref, self.max_usd)
            cantidad = self.max_usd / precio_ref
        return self._enviar(par, 'buy', self._precision(par, cantidad), precio_ref)

    def vender(self, par, cantidad, precio_ref):
        if not self.dry_run:
            base = self.ex.market(par)['base'] if getattr(self.ex, 'markets', None) else par.split('/')[0]
            libre = float(self.ex.fetch_balance().get('free', {}).get(base, 0) or 0)
            cantidad = min(cantidad, libre)
        return self._enviar(par, 'sell', self._precision(par, cantidad), precio_ref)

    def _enviar(self, par, lado, cantidad, precio_ref):
        if cantidad <= 0:
            log.warning('Cantidad 0, no se envia orden %s %s', lado, par)
            return None
        log.warning('ORDEN REAL %s %s %.8g (~%.2f USD)%s', lado.upper(), par, cantidad,
                    cantidad * precio_ref, ' [DRY-RUN, NO ENVIADA]' if self.dry_run else '')
        if self.dry_run:
            return {'dry_run': True, 'side': lado, 'amount': cantidad}
        orden = self.ex.create_order(par, 'market', lado, cantidad)
        log.warning('Orden enviada. id=%s', orden.get('id'))
        return orden


def crear_ejecutor(cfg):
    """Devuelve EjecutorKraken solo si TODAS las salvaguardas se cumplen; si no, None (paper)."""
    if modo_actual() != 'live':
        log.info('Modo PAPER (sin dinero real).')
        return None
    try:
        verificar_live()
    except ModoNoPermitido as e:
        log.error('MODO=live pedido pero NO permitido: %s. Se sigue en PAPER.', e)
        return None
    return EjecutorKraken(cfg)
