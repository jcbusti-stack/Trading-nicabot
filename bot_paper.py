import ccxt
import pandas as pd
import json
import os

RUTA_ESTADO = 'estado.json'
CAPITAL_INICIAL = 1000

exchange = ccxt.okx()


def descargar_reciente(par, timeframe='12h', velas=300):
    ohlcv = exchange.fetch_ohlcv(par, timeframe=timeframe, limit=velas)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp')


def indicadores(df):
    df = df.copy()
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()
    delta = df['close'].diff()
    ganancia = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    perdida = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = ganancia / perdida
    df['RSI'] = 100 - (100 / (1 + rs))
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df.dropna()


def main():
    df = indicadores(descargar_reciente('SOL/USDT'))
    ultima = df.iloc[-1]

    trend_up = ultima['close'] > ultima['EMA_200'] * 1.015
    macd_bullish = ultima['MACD'] > ultima['MACD_signal']
    rsi_ok = ultima['RSI'] < 65
    momentum_ok = ultima['EMA_9'] > ultima['EMA_21']
    senal_entrada = trend_up and macd_bullish and rsi_ok and momentum_ok
    senal_salida = (ultima['MACD'] < ultima['MACD_signal']) or (ultima['RSI'] > 75)

    print('--- DIAGNOSTICO INDICADORES (12h) ---')
    print(f'Precio actual: {ultima["close"]:.2f}')
    print(f'EMA_200: {ultima["EMA_200"]:.2f} | necesita precio > {ultima["EMA_200"] * 1.015:.2f} -> {"OK" if trend_up else "NO cumple"}')
    print(f'EMA_9: {ultima["EMA_9"]:.2f} vs EMA_21: {ultima["EMA_21"]:.2f} -> {"OK" if momentum_ok else "NO cumple"}')
    print(f'RSI: {ultima["RSI"]:.2f} (necesita <65) -> {"OK" if rsi_ok else "NO cumple"}')
    print(f'MACD: {ultima["MACD"]:.4f} vs señal: {ultima["MACD_signal"]:.4f} -> {"OK" if macd_bullish else "NO cumple"}')
    print(f'SEÑAL DE ENTRADA COMPLETA: {"SI" if senal_entrada else "NO"}')
    print('--------------------------------------')

    if os.path.exists(RUTA_ESTADO):
        with open(RUTA_ESTADO) as f:
            estado = json.load(f)
    else:
        estado = {'en_posicion': False, 'cash': CAPITAL_INICIAL, 'precio_entrada': None,
                   'fecha_entrada': None, 'historial': []}

    precio_actual = float(ultima['close'])
    fecha_actual = str(ultima.name)
    mensaje = ''

    if not estado['en_posicion']:
        if senal_entrada:
            estado['en_posicion'] = True
            estado['precio_entrada'] = precio_actual
            estado['fecha_entrada'] = fecha_actual
            mensaje = f'COMPRA (papel) SOL a ${precio_actual:.2f} el {fecha_actual}'
        else:
            mensaje = 'SIN POSICION - esperando senal de entrada'
    else:
        stop_loss = estado['precio_entrada'] * 0.95
        if senal_salida or precio_actual <= stop_loss:
            pnl_pct = (precio_actual - estado['precio_entrada']) / estado['precio_entrada'] * 100
            estado['cash'] = estado['cash'] * (1 + pnl_pct / 100)
            estado['historial'].append({
                'entrada': estado['precio_entrada'], 'salida': precio_actual,
                'fecha_entrada': estado['fecha_entrada'], 'fecha_salida': fecha_actual,
                'resultado_pct': round(pnl_pct, 2)
            })
            motivo = 'stop-loss' if precio_actual <= stop_loss else 'senal'
            mensaje = f'VENTA (papel) SOL a ${precio_actual:.2f} ({motivo}). Resultado: {pnl_pct:.2f}%'
            estado['en_posicion'] = False
            estado['precio_entrada'] = None
            estado['fecha_entrada'] = None
        else:
            pnl_pct = (precio_actual - estado['precio_entrada']) / estado['precio_entrada'] * 100
            mensaje = f'EN POSICION desde {estado["fecha_entrada"]} a ${estado["precio_entrada"]:.2f}. Ganancia actual: {pnl_pct:.2f}%'

    with open(RUTA_ESTADO, 'w') as f:
        json.dump(estado, f, indent=2)

    print('=== ESTADO PAPER TRADING SOL (SIN DINERO REAL) ===')
    print(mensaje)
    print('Capital simulado actual:', round(estado['cash'], 2))
    print('Operaciones cerradas hasta hoy:', len(estado['historial']))


if __name__ == '__main__':
    main()
