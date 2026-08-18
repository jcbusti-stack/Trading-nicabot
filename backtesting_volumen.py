"""
Backtesting - Filtro de Volumen - Trading-nicabot
Prueba si exigir volumen alto al momento de entrar mejora los resultados
de la estrategia validada de SOL.

La idea: solo entrar cuando el volumen de la vela supera su promedio de las
ultimas N velas por cierto margen. Si el volumen es bajo, el movimiento puede
ser debil o poco confiable.

Compara: SIN filtro (lo que hace el bot hoy) contra varios umbrales de volumen.

Herramienta MANUAL. Todo lo demas de la estrategia queda igual.
"""

import json
import os
import re
import time
from datetime import datetime

import ccxt
import pandas as pd

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_ARCHIVO = 'backtesting_volumen_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
PORCENTAJE_IN_SAMPLE = 0.70
STOP_LOSS_PCT = 5.0
PERIODO_VOLUMEN = 20  # cuantas velas para el promedio de volumen

# Umbrales a probar: 1.0 = volumen igual al promedio, 1.5 = 50% arriba, etc.
UMBRALES_VOLUMEN = [1.0, 1.2, 1.5, 2.0]

EXCHANGES = ['binance', 'bybit', 'kraken', 'okx']


def descargar_paginado(nombre_exchange, par, timeframe, objetivo):
    exchange = getattr(ccxt, nombre_exchange)({'enableRateLimit': True})
    ms_por_vela = exchange.parse_timeframe(timeframe) * 1000
    ahora = exchange.milliseconds()

    desde = ahora - (objetivo * ms_por_vela)
    velas = {}
    paginas = 0
    MAX_PAGINAS = 200

    while desde < ahora and paginas < MAX_PAGINAS:
        paginas += 1
        lote = exchange.fetch_ohlcv(par, timeframe=timeframe, since=desde, limit=1000)

        if not lote:
            desde += ms_por_vela * 500
            continue

        for vela in lote:
            velas[vela[0]] = vela

        siguiente = lote[-1][0] + ms_por_vela
        if siguiente <= desde:
            break
        desde = siguiente
        time.sleep(exchange.rateLimit / 1000)

    return [velas[k] for k in sorted(velas)]


def obtener_historial(par, timeframe, objetivo):
    mejor_datos = []
    mejor_nombre = None

    for nombre in EXCHANGES:
        try:
            print(f"Intentando descargar de {nombre}...")
            datos = descargar_paginado(nombre, par, timeframe, objetivo)
            print(f"  {nombre}: {len(datos)} velas obtenidas.")
            if len(datos) > len(mejor_datos):
                mejor_datos = datos
                mejor_nombre = nombre
            if len(mejor_datos) >= objetivo * 0.9:
                break
        except Exception as e:
            print(f"  {nombre} no disponible: {e}")

    if not mejor_datos:
        raise RuntimeError("Ningun exchange devolvio datos.")

    df = pd.DataFrame(mejor_datos, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp'), mejor_nombre


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

    # Promedio de volumen de las ultimas N velas (sin incluir la actual)
    df['VOL_PROM'] = df['volume'].rolling(PERIODO_VOLUMEN).mean().shift(1)

    return df.dropna()


def simular_operaciones(df, umbral_volumen=None):
    """umbral_volumen=None significa SIN filtro (como el bot actual)."""
    en_posicion = False
    precio_entrada = None
    stop_actual = None
    operaciones = []
    entradas_bloqueadas = 0

    for i in range(len(df)):
        fila = df.iloc[i]
        trend_up = fila['close'] > fila['EMA_200'] * 1.015
        macd_bullish = fila['MACD'] > fila['MACD_signal']
        rsi_ok = fila['RSI'] < 65
        momentum_ok = fila['EMA_9'] > fila['EMA_21']
        senal_base = trend_up and macd_bullish and rsi_ok and momentum_ok
        senal_salida = (fila['MACD'] < fila['MACD_signal']) or (fila['RSI'] > 75)

        if umbral_volumen is None:
            volumen_ok = True
        else:
            volumen_ok = fila['volume'] >= fila['VOL_PROM'] * umbral_volumen

        if not en_posicion:
            if senal_base and volumen_ok:
                en_posicion = True
                precio_entrada = fila['close']
                stop_actual = precio_entrada * (1 - STOP_LOSS_PCT / 100)
            elif senal_base and not volumen_ok:
                entradas_bloqueadas += 1
        else:
            if fila['low'] <= stop_actual:
                operaciones.append((stop_actual - precio_entrada) / precio_entrada * 100)
                en_posicion = False
                precio_entrada = None
                stop_actual = None
            elif senal_salida:
                operaciones.append((fila['close'] - precio_entrada) / precio_entrada * 100)
                en_posicion = False
                precio_entrada = None
                stop_actual = None

    return operaciones, entradas_bloqueadas


def calcular_metricas(operaciones, bloqueadas):
    if not operaciones:
        return {"total": 0, "acierto": 0, "retorno": 0, "peor": 0, "bloqueadas": bloqueadas}

    ganadoras = [op for op in operaciones if op > 0]
    capital = 1000
    for op in operaciones:
        capital *= (1 + op / 100)

    return {
        "total": len(operaciones),
        "acierto": round(len(ganadoras) / len(operaciones) * 100, 1),
        "retorno": round((capital - 1000) / 1000 * 100, 1),
        "peor": round(min(operaciones), 1),
        "bloqueadas": bloqueadas,
    }


def evaluar_todo():
    df_bruto, fuente = obtener_historial(PAR, TIMEFRAME, VELAS_OBJETIVO)
    df = calcular_indicadores(df_bruto)

    meta = {
        "fuente": fuente,
        "velas": len(df),
        "desde": df.index[0].strftime("%Y-%m-%d"),
        "hasta": df.index[-1].strftime("%Y-%m-%d"),
    }

    corte = int(len(df) * PORCENTAJE_IN_SAMPLE)
    df_in, df_out = df.iloc[:corte], df.iloc[corte:]

    resultados = {}

    ops_in, bl_in = simular_operaciones(df_in, None)
    ops_out, bl_out = simular_operaciones(df_out, None)
    resultados['Sin filtro (actual)'] = {
        'in': calcular_metricas(ops_in, bl_in),
        'out': calcular_metricas(ops_out, bl_out),
    }

    for umbral in UMBRALES_VOLUMEN:
        ops_in, bl_in = simular_operaciones(df_in, umbral)
        ops_out, bl_out = simular_operaciones(df_out, umbral)
        etiqueta = f'Volumen >= {umbral}x promedio'
        resultados[etiqueta] = {
            'in': calcular_metricas(ops_in, bl_in),
            'out': calcular_metricas(ops_out, bl_out),
        }

    return resultados, meta


def generar_reporte(resultados, meta):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Filtro de Volumen (SOL) - {fecha}", ""]
    L.append(
        f"Datos: {meta['velas']} velas de 12h desde {meta['desde']} hasta {meta['hasta']}, "
        f"fuente: {meta['fuente']}."
    )
    L.append(
        f"Metodologia: estrategia validada de SOL (EMA 9/21/200 + RSI + MACD + stop-loss 5%), "
        f"70% in-sample / 30% out-of-sample. Lo unico que cambia es exigir volumen alto "
        f"para entrar. Promedio de volumen calculado sobre {PERIODO_VOLUMEN} velas."
    )
    L.append("")

    avisos = []
    if meta['velas'] < 1000:
        avisos.append(f"POCOS DATOS: solo {meta['velas']} velas.")
    if datetime.now().year - int(meta['hasta'][:4]) >= 1:
        avisos.append(f"DATOS VIEJOS: el historial termina en {meta['hasta']}.")
    if avisos:
        L.append("*** ADVERTENCIAS SOBRE LA CALIDAD DE ESTA PRUEBA ***")
        for a in avisos:
            L.append(f"- {a}")
        L.append("")

    ref = resultados['Sin filtro (actual)']['out']['retorno']

    for nombre, d in resultados.items():
        marca = ""
        if nombre != 'Sin filtro (actual)':
            dif = d['out']['retorno'] - ref
            marca = f" ({'MEJOR' if dif > 0 else 'peor'} por {dif:+.1f} puntos)"

        L.append(f"{nombre}{marca}:")
        L.append(
            f"  In-sample: {d['in']['total']} operaciones, {d['in']['acierto']}% acierto, "
            f"{d['in']['retorno']}% retorno, peor operacion {d['in']['peor']}%, "
            f"{d['in']['bloqueadas']} entradas bloqueadas por volumen bajo"
        )
        L.append(
            f"  Out-of-sample: {d['out']['total']} operaciones, {d['out']['acierto']}% acierto, "
            f"{d['out']['retorno']}% retorno, peor operacion {d['out']['peor']}%, "
            f"{d['out']['bloqueadas']} entradas bloqueadas por volumen bajo"
        )
        L.append("")

    L.append(
        "COMO LEER ESTO: 'entradas bloqueadas' te dice cuantas señales validas se "
        "descartaron por volumen bajo. Si es 0, el filtro no hizo nada y la prueba "
        "no sirve. Si bloqueo muchas y el retorno bajo, el filtro esta descartando "
        "buenas operaciones."
    )
    L.append("")
    L.append(
        "Solo si un umbral mejora el retorno Y la tasa de acierto en out-of-sample, "
        "con una diferencia clara (no de 1 o 2 puntos, que es ruido), vale la pena "
        "agregarlo a bot_paper.py."
    )
    return "\n".join(L)


def subir_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado, no se sube a Drive.')
        return
    try:
        match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
        creds = Credentials.from_service_account_info(
            json.loads(match.group(0)), scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
            f.write(texto)

        query = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
        files = service.files().list(q=query, fields='files(id)', pageSize=1).execute().get('files', [])
        media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/plain')

        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')
        else:
            meta = {'name': NOMBRE_ARCHIVO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
            service.files().create(body=meta, media_body=media, fields='id').execute()
            print(f'{NOMBRE_ARCHIVO} creado en Drive.')
    except Exception as e:
        print('No se pudo subir el reporte a Drive:', e)


def main():
    resultados, meta = evaluar_todo()
    reporte = generar_reporte(resultados, meta)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
