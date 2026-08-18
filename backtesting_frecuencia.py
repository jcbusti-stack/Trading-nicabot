"""
Backtesting - Frecuencia de Señales - Trading-nicabot
Prueba si relajar las condiciones de entrada genera mas operaciones SIN
destruir el rendimiento.

La estrategia actual solo dio 91 operaciones en 5 años (~18 al año) porque
exige 4 condiciones simultaneas. Aqui se prueba relajar cada una por separado.

ADVERTENCIA: relajar condiciones casi siempre genera MAS operaciones PEORES.
Esta prueba sirve para medirlo, no para asumir que sera mejor.

Herramienta MANUAL. No cambia nada del bot en vivo.
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

NOMBRE_ARCHIVO = 'backtesting_frecuencia_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
PORCENTAJE_IN_SAMPLE = 0.70
STOP_LOSS_PCT = 5.0

EXCHANGES = ['binance', 'bybit', 'kraken', 'okx']

# Cada variante define: margen sobre EMA200, tope de RSI, si exige MACD,
# y si exige momentum (EMA9 > EMA21).
VARIANTES = {
    "ACTUAL (referencia)":        {"margen": 1.015, "rsi_max": 65, "macd": True,  "momentum": True},
    "Sin margen sobre EMA200":    {"margen": 1.000, "rsi_max": 65, "macd": True,  "momentum": True},
    "Margen reducido (0.5%)":     {"margen": 1.005, "rsi_max": 65, "macd": True,  "momentum": True},
    "Sin exigir MACD":            {"margen": 1.015, "rsi_max": 65, "macd": False, "momentum": True},
    "Sin exigir momentum":        {"margen": 1.015, "rsi_max": 65, "macd": True,  "momentum": False},
    "RSI hasta 75":               {"margen": 1.015, "rsi_max": 75, "macd": True,  "momentum": True},
    "Todo relajado":              {"margen": 1.000, "rsi_max": 75, "macd": False, "momentum": True},
}


def descargar_paginado(nombre_exchange, par, timeframe, objetivo):
    exchange = getattr(ccxt, nombre_exchange)({'enableRateLimit': True})
    ms_por_vela = exchange.parse_timeframe(timeframe) * 1000
    ahora = exchange.milliseconds()
    desde = ahora - (objetivo * ms_por_vela)
    velas = {}
    paginas = 0

    while desde < ahora and paginas < 200:
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
    mejor, nombre_mejor = [], None
    for nombre in EXCHANGES:
        try:
            print(f"Intentando {nombre}...")
            datos = descargar_paginado(nombre, par, timeframe, objetivo)
            print(f"  {nombre}: {len(datos)} velas.")
            if len(datos) > len(mejor):
                mejor, nombre_mejor = datos, nombre
            if len(mejor) >= objetivo * 0.9:
                break
        except Exception as e:
            print(f"  {nombre} no disponible: {e}")

    if not mejor:
        raise RuntimeError("Ningun exchange devolvio datos.")

    df = pd.DataFrame(mejor, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp'), nombre_mejor


def calcular_indicadores(df):
    df = df.copy()
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()
    delta = df['close'].diff()
    ganancia = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    perdida = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + ganancia / perdida))
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df.dropna()


def simular(df, cfg):
    en_posicion = False
    precio_entrada = None
    stop = None
    ops = []

    for i in range(len(df)):
        f = df.iloc[i]

        cond_tendencia = f['close'] > f['EMA_200'] * cfg['margen']
        cond_rsi = f['RSI'] < cfg['rsi_max']
        cond_macd = (f['MACD'] > f['MACD_signal']) if cfg['macd'] else True
        cond_mom = (f['EMA_9'] > f['EMA_21']) if cfg['momentum'] else True

        entrada = cond_tendencia and cond_rsi and cond_macd and cond_mom
        salida = (f['MACD'] < f['MACD_signal']) or (f['RSI'] > 75)

        if not en_posicion:
            if entrada:
                en_posicion = True
                precio_entrada = f['close']
                stop = precio_entrada * (1 - STOP_LOSS_PCT / 100)
        else:
            if f['low'] <= stop:
                ops.append((stop - precio_entrada) / precio_entrada * 100)
                en_posicion = False
            elif salida:
                ops.append((f['close'] - precio_entrada) / precio_entrada * 100)
                en_posicion = False

    return ops


def metricas(ops, años):
    if not ops:
        return {"total": 0, "por_año": 0, "acierto": 0, "retorno": 0, "peor": 0, "promedio": 0}

    ganadoras = [o for o in ops if o > 0]
    capital = 1000
    for o in ops:
        capital *= (1 + o / 100)

    return {
        "total": len(ops),
        "por_año": round(len(ops) / años, 1) if años > 0 else 0,
        "acierto": round(len(ganadoras) / len(ops) * 100, 1),
        "retorno": round((capital - 1000) / 1000 * 100, 1),
        "peor": round(min(ops), 1),
        "promedio": round(sum(ops) / len(ops), 2),
    }


def evaluar():
    df_bruto, fuente = obtener_historial(PAR, TIMEFRAME, VELAS_OBJETIVO)
    df = calcular_indicadores(df_bruto)

    dias_totales = (df.index[-1] - df.index[0]).days
    corte = int(len(df) * PORCENTAJE_IN_SAMPLE)
    df_in, df_out = df.iloc[:corte], df.iloc[corte:]

    años_in = (df_in.index[-1] - df_in.index[0]).days / 365.25
    años_out = (df_out.index[-1] - df_out.index[0]).days / 365.25

    resultados = {}
    for nombre, cfg in VARIANTES.items():
        print(f"Evaluando: {nombre}...")
        resultados[nombre] = {
            'in': metricas(simular(df_in, cfg), años_in),
            'out': metricas(simular(df_out, cfg), años_out),
        }

    meta = {
        "fuente": fuente,
        "velas": len(df),
        "desde": df.index[0].strftime("%Y-%m-%d"),
        "hasta": df.index[-1].strftime("%Y-%m-%d"),
        "años_out": round(años_out, 1),
    }
    return resultados, meta


def generar_reporte(resultados, meta):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Frecuencia de Señales (SOL) - {fecha}", ""]
    L.append(
        f"Datos: {meta['velas']} velas de 12h desde {meta['desde']} hasta {meta['hasta']}, "
        f"fuente: {meta['fuente']}. Periodo out-of-sample: {meta['años_out']} años."
    )
    L.append(
        "Objetivo: ver si relajar las condiciones de entrada genera mas operaciones "
        "sin destruir el rendimiento. Salida y stop-loss (5%) sin cambios."
    )
    L.append("")

    ref_out = resultados['ACTUAL (referencia)']['out']

    L.append("=== RESUMEN OUT-OF-SAMPLE (lo que mas importa) ===")
    L.append("")
    for nombre, d in resultados.items():
        o = d['out']
        if nombre == 'ACTUAL (referencia)':
            marca = ""
        else:
            dif_ret = o['retorno'] - ref_out['retorno']
            dif_ops = o['total'] - ref_out['total']
            marca = f" [{dif_ops:+d} operaciones, {dif_ret:+.1f} puntos de retorno]"

        L.append(f"{nombre}{marca}:")
        L.append(
            f"  {o['total']} operaciones ({o['por_año']}/año) | "
            f"{o['acierto']}% acierto | {o['retorno']}% retorno total | "
            f"{o['promedio']}% promedio por operacion | peor {o['peor']}%"
        )
        L.append("")

    L.append("=== IN-SAMPLE (para verificar consistencia) ===")
    L.append("")
    for nombre, d in resultados.items():
        i = d['in']
        L.append(
            f"{nombre}: {i['total']} operaciones ({i['por_año']}/año), "
            f"{i['acierto']}% acierto, {i['retorno']}% retorno, "
            f"{i['promedio']}% promedio por operacion"
        )

    L.append("")
    L.append("=== COMO LEER ESTO ===")
    L.append(
        "- 'promedio por operacion' es la metrica clave: si una variante genera el "
        "doble de operaciones pero su promedio baja a la mitad, no gano nada y "
        "ademas paga mas comisiones."
    )
    L.append(
        "- Una variante solo vale la pena si mejora en AMBOS periodos (in y out). "
        "Si mejora en uno y empeora en el otro, es azar, no ventaja."
    )
    L.append(
        "- Mas operaciones tambien significa mas costo real por comisiones de Kraken, "
        "que este backtesting NO descuenta. Una mejora menor a 2-3 puntos "
        "probablemente se la come la comision."
    )

    return "\n".join(L)


def subir_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado.')
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
        print('No se pudo subir el reporte:', e)


def main():
    resultados, meta = evaluar()
    reporte = generar_reporte(resultados, meta)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
