"""
Backtesting - Stop-Loss Dinamico (ATR) vs Fijo - Trading-nicabot
VERSION 2: descarga historial EXTENDIDO con paginacion y multiples exchanges
de respaldo, para tener suficientes operaciones y que la comparacion sea valida.

La version anterior solo consiguio 4 operaciones - muy pocas para concluir nada.
Esta version baja miles de velas retrocediendo en el tiempo por paginas.

Herramienta MANUAL. Misma logica de entrada validada de SOL, solo cambia
como se calcula el stop-loss.
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

NOMBRE_ARCHIVO = 'backtesting_atr_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000          # ~5.5 años en velas de 12h
PORCENTAJE_IN_SAMPLE = 0.70
MULTIPLICADORES_ATR = [1.5, 2.0, 2.5, 3.0]

# Se prueban en orden hasta que uno entregue suficientes datos.
EXCHANGES = ['binance', 'bybit', 'kraken', 'okx']


def descargar_paginado(nombre_exchange, par, timeframe, objetivo):
    """
    Descarga velas avanzando en el tiempo, pagina por pagina, hasta llegar
    al presente o hasta juntar el objetivo.

    NOTA: cada exchange entrega un maximo distinto de velas por peticion
    (OKX ~100-300, Binance ~1000). Antes el script se detenia cuando un lote
    traia menos de 1000 velas, y por eso solo bajaba ~287 velas de 2021.
    Ahora sigue pidiendo hasta alcanzar el presente.
    """
    exchange = getattr(ccxt, nombre_exchange)({'enableRateLimit': True})
    ms_por_vela = exchange.parse_timeframe(timeframe) * 1000
    ahora = exchange.milliseconds()

    desde = ahora - (objetivo * ms_por_vela)
    velas = {}
    paginas = 0
    MAX_PAGINAS = 200  # freno de seguridad

    while desde < ahora and paginas < MAX_PAGINAS:
        paginas += 1
        lote = exchange.fetch_ohlcv(par, timeframe=timeframe, since=desde, limit=1000)

        if not lote:
            # Sin datos en este tramo: saltar hacia adelante para no atorarse
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
    """Prueba varios exchanges hasta conseguir el historial mas largo posible."""
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

    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    df['ATR'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1).rolling(periodo_atr).mean()

    return df.dropna()


def simular_operaciones(df, modo_stop, multiplicador_atr=None):
    en_posicion = False
    precio_entrada = None
    stop_actual = None
    operaciones = []
    stops_activados = 0

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
            toco_stop = fila['low'] <= stop_actual
            if toco_stop:
                pnl_pct = (stop_actual - precio_entrada) / precio_entrada * 100
                operaciones.append(pnl_pct)
                stops_activados += 1
                en_posicion = False
                precio_entrada = None
                stop_actual = None
            elif senal_salida:
                pnl_pct = (fila['close'] - precio_entrada) / precio_entrada * 100
                operaciones.append(pnl_pct)
                en_posicion = False
                precio_entrada = None
                stop_actual = None

    return operaciones, stops_activados


def calcular_metricas(operaciones, stops_activados):
    if not operaciones:
        return {"total": 0, "acierto": 0, "retorno": 0, "peor": 0, "stops": 0}

    ganadoras = [op for op in operaciones if op > 0]
    capital = 1000
    for op in operaciones:
        capital *= (1 + op / 100)

    return {
        "total": len(operaciones),
        "acierto": round(len(ganadoras) / len(operaciones) * 100, 1),
        "retorno": round((capital - 1000) / 1000 * 100, 1),
        "peor": round(min(operaciones), 1),
        "stops": stops_activados,
    }


def evaluar_todo():
    df_bruto, fuente = obtener_historial(PAR, TIMEFRAME, VELAS_OBJETIVO)
    df = calcular_indicadores(df_bruto)

    fecha_inicio = df.index[0].strftime("%Y-%m-%d")
    fecha_fin = df.index[-1].strftime("%Y-%m-%d")

    corte = int(len(df) * PORCENTAJE_IN_SAMPLE)
    df_in, df_out = df.iloc[:corte], df.iloc[corte:]

    resultados = {}

    ops_in, st_in = simular_operaciones(df_in, 'fijo')
    ops_out, st_out = simular_operaciones(df_out, 'fijo')
    resultados['Fijo 5%'] = {
        'in': calcular_metricas(ops_in, st_in),
        'out': calcular_metricas(ops_out, st_out),
    }

    for mult in MULTIPLICADORES_ATR:
        ops_in, st_in = simular_operaciones(df_in, 'atr', mult)
        ops_out, st_out = simular_operaciones(df_out, 'atr', mult)
        resultados[f'ATR x{mult}'] = {
            'in': calcular_metricas(ops_in, st_in),
            'out': calcular_metricas(ops_out, st_out),
        }

    meta = {
        "fuente": fuente,
        "velas": len(df),
        "desde": fecha_inicio,
        "hasta": fecha_fin,
    }
    return resultados, meta


def generar_reporte(resultados, meta):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Stop-Loss ATR vs Fijo (SOL) - {fecha}", ""]
    L.append(
        f"Datos: {meta['velas']} velas de 12h desde {meta['desde']} hasta {meta['hasta']}, "
        f"fuente: {meta['fuente']}."
    )
    L.append(
        "Metodologia: misma logica de entrada validada de SOL (EMA 9/21/200 + RSI + MACD), "
        "70% in-sample / 30% out-of-sample. Solo cambia como se calcula el stop-loss."
    )
    L.append("")

    # Avisos automaticos de calidad de datos
    avisos = []
    if meta['velas'] < 1000:
        avisos.append(
            f"POCOS DATOS: solo {meta['velas']} velas. Se necesitan varios miles "
            "para que la comparacion sea confiable."
        )
    anio_final = int(meta['hasta'][:4])
    anio_actual = datetime.now().year
    if anio_actual - anio_final >= 1:
        avisos.append(
            f"DATOS VIEJOS: el historial termina en {meta['hasta']}, no llega al presente. "
            "Los resultados NO reflejan el mercado actual."
        )
    if avisos:
        L.append("*** ADVERTENCIAS SOBRE LA CALIDAD DE ESTA PRUEBA ***")
        for aviso in avisos:
            L.append(f"- {aviso}")
        L.append("")

    ref = resultados['Fijo 5%']['out']['retorno']

    for nombre, d in resultados.items():
        marca = ""
        if nombre != 'Fijo 5%':
            dif = d['out']['retorno'] - ref
            marca = f" ({'MEJOR' if dif > 0 else 'peor'} que el fijo por {dif:+.1f} puntos)"

        L.append(f"{nombre}{marca}:")
        L.append(
            f"  In-sample: {d['in']['total']} operaciones, {d['in']['acierto']}% acierto, "
            f"{d['in']['retorno']}% retorno, peor operacion {d['in']['peor']}%, "
            f"stop-loss activado {d['in']['stops']} veces"
        )
        L.append(
            f"  Out-of-sample: {d['out']['total']} operaciones, {d['out']['acierto']}% acierto, "
            f"{d['out']['retorno']}% retorno, peor operacion {d['out']['peor']}%, "
            f"stop-loss activado {d['out']['stops']} veces"
        )
        L.append("")

    L.append(
        "COMO LEER ESTO: fijate en 'stop-loss activado' - si es 0 en todas las "
        "versiones, la prueba no sirve para comparar, porque el stop-loss nunca "
        "entro en accion y por eso todos dan el mismo resultado."
    )
    L.append("")
    L.append(
        "Solo si un ATR muestra mejor retorno Y acierto similar o mejor en "
        "out-of-sample, con el stop-loss activandose varias veces, es candidato "
        "real para reemplazar el 5% fijo en bot_paper.py."
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
