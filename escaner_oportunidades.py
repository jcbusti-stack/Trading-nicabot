"""
Escaner de Oportunidades - Trading-nicabot
Aplica la misma logica tecnica validada para SOL (EMA 9/21/200 + RSI + MACD)
a las demas monedas del portafolio, como referencia informativa.

IMPORTANTE: Esta estrategia SOLO fue validada con backtesting para SOL.
Las senales de otras monedas son informativas, NO estan confirmadas con
backtesting propio. Sirven para detectar candidatos a investigar, no para
operar directamente con dinero real.

Requiere: ccxt, pandas (ya estan en requirements.txt)
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

NOMBRE_ARCHIVO = 'oportunidades.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

# Mismas 16 monedas del agente de sentimiento (menos SOL, que ya tiene su propio bot)
MONEDAS = ["BTC", "ETH", "XRP", "ONDO", "ADA", "BONK", "ENA", "KAS",
           "MNT", "PEPE", "SHIB", "WLFI", "DOGE", "SUI", "AVAX"]

exchange = ccxt.okx()


def calcular_ema(serie, periodo):
    return serie.ewm(span=periodo, adjust=False).mean()


def calcular_rsi(serie, periodo=14):
    delta = serie.diff()
    ganancia = delta.where(delta > 0, 0).rolling(periodo).mean()
    perdida = -delta.where(delta < 0, 0).rolling(periodo).mean()
    rs = ganancia / perdida
    return 100 - (100 / (1 + rs))


def calcular_macd(serie):
    ema12 = calcular_ema(serie, 12)
    ema26 = calcular_ema(serie, 26)
    macd = ema12 - ema26
    señal = calcular_ema(macd, 9)
    return macd, señal


def analizar_moneda(simbolo):
    par = f"{simbolo}/USDT"
    try:
        velas = exchange.fetch_ohlcv(par, timeframe='12h', limit=250)
    except Exception as e:
        return {"simbolo": simbolo, "error": str(e)}

    if len(velas) < 200:
        return {"simbolo": simbolo, "error": "Historial insuficiente (menos de 200 velas)"}

    df = pd.DataFrame(velas, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
    cierre = df['close']

    ema9 = calcular_ema(cierre, 9)
    ema21 = calcular_ema(cierre, 21)
    ema200 = calcular_ema(cierre, 200)
    rsi = calcular_rsi(cierre)
    macd, señal_macd = calcular_macd(cierre)

    precio_actual = cierre.iloc[-1]
    ema200_actual = ema200.iloc[-1]

    tendencia_ok = precio_actual > ema200_actual * 1.015
    momentum_ok = ema9.iloc[-1] > ema21.iloc[-1]
    rsi_ok = rsi.iloc[-1] < 65
    macd_ok = macd.iloc[-1] > señal_macd.iloc[-1]

    señal_completa = tendencia_ok and momentum_ok and rsi_ok and macd_ok

    return {
        "simbolo": simbolo,
        "precio": round(precio_actual, 6),
        "tendencia_ok": tendencia_ok,
        "momentum_ok": momentum_ok,
        "rsi": round(rsi.iloc[-1], 1),
        "rsi_ok": rsi_ok,
        "macd_ok": macd_ok,
        "señal_completa": señal_completa,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [f"Escaner de Oportunidades - {fecha}", ""]
    lineas.append(
        "AVISO: estrategia validada con backtesting SOLO para SOL. "
        "Estas senales son informativas, no confirmadas para estas monedas."
    )
    lineas.append("")

    candidatas = [r for r in resultados if r.get("señal_completa")]

    if candidatas:
        lineas.append("Monedas que cumplen los mismos criterios que SOL:")
        for r in candidatas:
            lineas.append(f"- {r['simbolo']}: precio {r['precio']}, RSI {r['rsi']}")
    else:
        lineas.append("Ninguna moneda cumple actualmente los criterios completos.")

    lineas.append("")
    lineas.append("Detalle completo:")
    for r in resultados:
        if "error" in r:
            lineas.append(f"- {r['simbolo']}: sin datos ({r['error']})")
            continue
        estado = "CUMPLE" if r["señal_completa"] else "no cumple"
        lineas.append(
            f"- {r['simbolo']}: {estado} | precio {r['precio']} | "
            f"tendencia {'OK' if r['tendencia_ok'] else 'NO'} | "
            f"momentum {'OK' if r['momentum_ok'] else 'NO'} | "
            f"RSI {r['rsi']} | MACD {'OK' if r['macd_ok'] else 'NO'}"
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
        print('No se pudo subir el archivo a Drive:', e)


def main():
    resultados = []
    for simbolo in MONEDAS:
        print(f"Analizando {simbolo}...")
        resultados.append(analizar_moneda(simbolo))

    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
