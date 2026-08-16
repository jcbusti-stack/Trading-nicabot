"""
Backtesting - Trading-nicabot
Aplica la MISMA estrategia validada de SOL (EMA 9/21/200 + RSI + MACD +
stop-loss 5%) a otras monedas, con division in-sample / out-of-sample,
igual que se hizo originalmente para validar SOL.

Esto es una herramienta MANUAL - se corre cuando quieras probar una
moneda nueva, no corre automatico todos los dias.

Uso: cambia la lista MONEDAS_A_PROBAR abajo con las monedas que quieras
validar, y corre el workflow manual desde GitHub Actions.
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

NOMBRE_ARCHIVO = 'backtesting_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

# Cambia esta lista para probar otras monedas
MONEDAS_A_PROBAR = ["ONDO", "AVAX", "KAS"]

# Misma regla que la estrategia validada de SOL
STOP_LOSS_PCT = 5.0
PORCENTAJE_IN_SAMPLE = 0.70  # 70% de los datos para probar, 30% para validar

exchange = ccxt.okx()


def descargar_historico(par, timeframe='12h', velas=1000):
    ohlcv = exchange.fetch_ohlcv(par, timeframe=timeframe, limit=velas)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp')


def calcular_indicadores(df):
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


def simular_operaciones(df):
    """Simula la estrategia vela a vela, igual que bot_paper.py pero
    recorriendo todo el historial de una sola vez."""
    en_posicion = False
    precio_entrada = None
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
        else:
            stop_loss = precio_entrada * (1 - STOP_LOSS_PCT / 100)
            if senal_salida or fila['close'] <= stop_loss:
                pnl_pct = (fila['close'] - precio_entrada) / precio_entrada * 100
                operaciones.append(pnl_pct)
                en_posicion = False
                precio_entrada = None

    return operaciones


def calcular_metricas(operaciones):
    if not operaciones:
        return {"total_operaciones": 0, "tasa_acierto": 0, "retorno_total_pct": 0}

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
    }


def evaluar_moneda(simbolo):
    par = f"{simbolo}/USDT"
    try:
        df = calcular_indicadores(descargar_historico(par))
    except Exception as e:
        return {"simbolo": simbolo, "error": str(e)}

    if len(df) < 300:
        return {"simbolo": simbolo, "error": f"Historial insuficiente ({len(df)} velas, se necesitan al menos 300)"}

    punto_corte = int(len(df) * PORCENTAJE_IN_SAMPLE)
    df_in_sample = df.iloc[:punto_corte]
    df_out_sample = df.iloc[punto_corte:]

    ops_in = simular_operaciones(df_in_sample)
    ops_out = simular_operaciones(df_out_sample)

    metricas_in = calcular_metricas(ops_in)
    metricas_out = calcular_metricas(ops_out)

    # Criterio de validacion: tiene que tener operaciones en ambos periodos,
    # tasa de acierto razonable, y retorno positivo en el periodo out-of-sample
    # (el que de verdad importa, porque simula datos "nunca vistos")
    validada = (
        metricas_out["total_operaciones"] >= 3
        and metricas_out["tasa_acierto"] >= 45
        and metricas_out["retorno_total_pct"] > 0
    )

    return {
        "simbolo": simbolo,
        "in_sample": metricas_in,
        "out_sample": metricas_out,
        "validada": validada,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [f"Backtesting - Resultados - {fecha}", ""]
    lineas.append(
        "Metodologia: misma estrategia validada de SOL (EMA 9/21/200 + RSI + "
        "MACD + stop-loss 5%), velas de 12h, dividido en 70% in-sample "
        "(entrenamiento) y 30% out-of-sample (validacion con datos no vistos)."
    )
    lineas.append("")

    for r in resultados:
        if "error" in r:
            lineas.append(f"{r['simbolo']}: no se pudo evaluar ({r['error']})")
            lineas.append("")
            continue

        estado = "VALIDADA - se puede usar como señal real" if r["validada"] else "NO validada - solo informativa"
        lineas.append(f"{r['simbolo']}: {estado}")
        lineas.append(
            f"  In-sample: {r['in_sample']['total_operaciones']} operaciones, "
            f"{r['in_sample']['tasa_acierto']}% acierto, "
            f"{r['in_sample']['retorno_total_pct']}% retorno"
        )
        lineas.append(
            f"  Out-of-sample: {r['out_sample']['total_operaciones']} operaciones, "
            f"{r['out_sample']['tasa_acierto']}% acierto, "
            f"{r['out_sample']['retorno_total_pct']}% retorno"
        )
        lineas.append("")

    lineas.append(
        "IMPORTANTE: solo las monedas marcadas como VALIDADA deberian "
        "considerarse para el escaner de oportunidades como señal real. "
        "Las demas siguen siendo solo informativas."
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
    resultados = []
    for simbolo in MONEDAS_A_PROBAR:
        print(f"Evaluando {simbolo}...")
        resultados.append(evaluar_moneda(simbolo))

    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
