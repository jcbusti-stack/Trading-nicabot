"""
Backtesting - Stop-Loss Dinamico (ATR) vs Fijo - Trading-nicabot
Compara el stop-loss fijo del 5% (el que ya usa el bot en vivo) contra un
stop-loss dinamico basado en ATR (Average True Range), que se ajusta segun
la volatilidad real del momento en vez de ser siempre el mismo porcentaje.

Herramienta MANUAL - se corre cuando quieras evaluar el cambio, no corre
automatico. Usa la MISMA logica de entrada validada de SOL, solo cambia
como se calcula el stop-loss.

Requiere: GCP_SA_KEY (para subir el resultado a Drive)
"""

import json
import os
import re
from datetime import datetime

import ccxt
import pandas as pd

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_ARCHIVO = 'backtesting_atr_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
PORCENTAJE_IN_SAMPLE = 0.70
MULTIPLICADORES_ATR = [1.5, 2.0, 2.5, 3.0]  # cuantas veces el ATR se usa como distancia de stop

exchange = ccxt.okx()


def descargar_historico(par, timeframe='12h', velas=1000):
    ohlcv = exchange.fetch_ohlcv(par, timeframe=timeframe, limit=velas)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp')


def calcular_indicadores(df, periodo_atr=14):
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

    # ATR: promedio del rango real (True Range) de los ultimos N periodos
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = true_range.rolling(periodo_atr).mean()

    return df.dropna()


def simular_operaciones(df, modo_stop, multiplicador_atr=None):
    """
    modo_stop: 'fijo' (5% siempre) o 'atr' (dinamico segun volatilidad)
    """
    en_posicion = False
    precio_entrada = None
    stop_actual = None
    operaciones = []

    for i in range(len(df)):
        fila = df.iloc[i]
        trend_up = fila['close'] > fila['EMA_200'] * 1.015
        macd_bullish = fila['MACD'] > fila['MACD_signal']
        rsi_ok = fila['RSI'] < 65
        momentum_ok = fila['EMA_9'] > fila['EMA_21']
        senal_entrada = trend_up and macd_bullish and rsi_ok and momentum_ok
        senal_salida = (fila['MACD'] < fila['MACD_signal']) or (fila['RSI'] > 75)

        if not en_posicion:
            if senal_entrada:
                en_posicion = True
                precio_entrada = fila['close']
                if modo_stop == 'fijo':
                    stop_actual = precio_entrada * 0.95
                else:
                    stop_actual = precio_entrada - (fila['ATR'] * multiplicador_atr)
        else:
            if senal_salida or fila['close'] <= stop_actual:
                pnl_pct = (fila['close'] - precio_entrada) / precio_entrada * 100
                operaciones.append(pnl_pct)
                en_posicion = False
                precio_entrada = None
                stop_actual = None

    return operaciones


def calcular_metricas(operaciones):
    if not operaciones:
        return {"total_operaciones": 0, "tasa_acierto": 0, "retorno_total_pct": 0, "peor_operacion_pct": 0}

    ganadoras = [op for op in operaciones if op > 0]
    tasa_acierto = len(ganadoras) / len(operaciones) * 100

    capital = 1000
    for op in operaciones:
        capital *= (1 + op / 100)
    retorno_total_pct = (capital - 1000) / 1000 * 100

    return {
        "total_operaciones": len(operaciones),
        "tasa_acierto": round(tasa_acierto, 1),
        "retorno_total_pct": round(retorno_total_pct, 1),
        "peor_operacion_pct": round(min(operaciones), 1),
    }


def evaluar_todo():
    print(f"Descargando historial de {PAR}...")
    df = calcular_indicadores(descargar_historico(PAR))

    punto_corte = int(len(df) * PORCENTAJE_IN_SAMPLE)
    df_in = df.iloc[:punto_corte]
    df_out = df.iloc[punto_corte:]

    resultados = {}

    print("Evaluando stop-loss FIJO (5%, el que ya usa el bot en vivo)...")
    ops_in_fijo = simular_operaciones(df_in, 'fijo')
    ops_out_fijo = simular_operaciones(df_out, 'fijo')
    resultados['Fijo 5%'] = {
        'in_sample': calcular_metricas(ops_in_fijo),
        'out_sample': calcular_metricas(ops_out_fijo),
    }

    for mult in MULTIPLICADORES_ATR:
        print(f"Evaluando stop-loss ATR x{mult}...")
        ops_in = simular_operaciones(df_in, 'atr', mult)
        ops_out = simular_operaciones(df_out, 'atr', mult)
        resultados[f'ATR x{mult}'] = {
            'in_sample': calcular_metricas(ops_in),
            'out_sample': calcular_metricas(ops_out),
        }

    return resultados


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [f"Backtesting - Stop-Loss ATR vs Fijo (SOL) - {fecha}", ""]
    lineas.append(
        "Metodologia: misma logica de entrada validada de SOL (EMA 9/21/200 + RSI + MACD), "
        "velas de 12h, 70% in-sample / 30% out-of-sample. Solo cambia como se calcula el stop-loss."
    )
    lineas.append("")

    referencia_out = resultados['Fijo 5%']['out_sample']['retorno_total_pct']

    for nombre, datos in resultados.items():
        out = datos['out_sample']
        marca = ""
        if nombre != 'Fijo 5%':
            diferencia = out['retorno_total_pct'] - referencia_out
            if diferencia > 0:
                marca = f" (MEJOR que el fijo por {diferencia:+.1f} puntos)"
            else:
                marca = f" (peor que el fijo por {diferencia:+.1f} puntos)"

        lineas.append(f"{nombre}{marca}:")
        lineas.append(
            f"  In-sample: {datos['in_sample']['total_operaciones']} operaciones, "
            f"{datos['in_sample']['tasa_acierto']}% acierto, "
            f"{datos['in_sample']['retorno_total_pct']}% retorno, "
            f"peor operacion {datos['in_sample']['peor_operacion_pct']}%"
        )
        lineas.append(
            f"  Out-of-sample: {out['total_operaciones']} operaciones, "
            f"{out['tasa_acierto']}% acierto, "
            f"{out['retorno_total_pct']}% retorno, "
            f"peor operacion {out['peor_operacion_pct']}%"
        )
        lineas.append("")

    lineas.append(
        "IMPORTANTE: 'peor operacion' te dice el peor resultado individual de una "
        "operacion - util para ver si el ATR te protege mejor en momentos de alta "
        "volatilidad que el 5% fijo, o si te saca demasiado rapido en momentos normales."
    )
    lineas.append("")
    lineas.append(
        "Si algun ATR muestra mejor retorno Y mejor (o similar) tasa de acierto en "
        "out-of-sample que el fijo, es candidato serio para reemplazar el 5% fijo "
        "en bot_paper.py. Si no, mejor quedarse con el fijo, que ya esta probado."
    )

    return "\n".join(lineas)


def subir_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado, no se sube a Drive.')
        return
    try:
        match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
        info = json.loads(match.group(0))
        creds = Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
            f.write(texto)

        query = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
        files = results.get('files', [])

        media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/plain')
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')
        else:
            metadata = {'name': NOMBRE_ARCHIVO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
            service.files().create(body=metadata, media_body=media, fields='id').execute()
            print(f'{NOMBRE_ARCHIVO} creado en Drive.')
    except Exception as e:
        print('No se pudo subir el reporte a Drive:', e)


def main():
    resultados = evaluar_todo()
    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
