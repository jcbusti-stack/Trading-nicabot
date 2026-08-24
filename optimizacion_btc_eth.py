"""
Optimizacion de Parametros Propios - BTC y ETH - Trading-nicabot

A diferencia de pruebas anteriores (que aplicaban la formula EXACTA de SOL
a otras monedas), esto busca los NUMEROS PROPIOS de BTC y ETH: prueba
muchas combinaciones de margen sobre EMA200, tope de RSI, y stop-loss,
para encontrar la version que mejor les quede a cada uno.

Metodologia (walk-forward optimization completo):
  1. Con el 70% de los datos (in-sample), probar muchas combinaciones de
     parametros y quedarse con la mejor.
  2. Validar esa combinacion en el 30% restante (out-of-sample, datos
     nunca vistos durante la busqueda).
  3. Prueba de SUAVIDAD: revisar si las combinaciones VECINAS a la mejor
     tambien funcionan razonablemente bien. Si solo un numero exacto
     funciona y sus vecinos son malos, es sobreajuste (numero de la
     suerte), no una ventaja real.
  4. Ventanas rodantes con los parametros encontrados.

Con comisiones reales incluidas siempre.

Herramienta MANUAL. No cambia nada del bot en vivo (que sigue siendo
solo SOL, con sus propios parametros ya validados).
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

NOMBRE_ARCHIVO = 'optimizacion_btc_eth_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
EXCHANGES = ['binance', 'bybit', 'okx']
VENTANAS = 6
COSTO_POR_OPERACION_PCT = (0.26 + 0.05) * 2

ACTIVOS = ["BTC", "ETH"]

# Espacio de busqueda: combinaciones a probar
MARGENES_EMA = [1.000, 1.005, 1.010, 1.015, 1.020, 1.025]
TOPES_RSI = [55, 60, 65, 70, 75]
STOPS_LOSS = [3.0, 5.0, 7.0, 10.0]

MIN_OPERACIONES_IN_SAMPLE = 8  # combinaciones con menos de esto se descartan


def descargar_paginado(ex_nombre, par, timeframe, objetivo):
    ex = getattr(ccxt, ex_nombre)({'enableRateLimit': True})
    ms = ex.parse_timeframe(timeframe) * 1000
    ahora = ex.milliseconds()
    desde = ahora - (objetivo * ms)
    velas = {}
    p = 0
    while desde < ahora and p < 200:
        p += 1
        lote = ex.fetch_ohlcv(par, timeframe=timeframe, since=desde, limit=1000)
        if not lote:
            desde += ms * 500
            continue
        for v in lote:
            velas[v[0]] = v
        sig = lote[-1][0] + ms
        if sig <= desde:
            break
        desde = sig
        time.sleep(ex.rateLimit / 1000)
    return [velas[k] for k in sorted(velas)]


def obtener_historial(simbolo):
    par = f"{simbolo}/USDT"
    mejor, fuente = [], None
    for nombre in EXCHANGES:
        try:
            d = descargar_paginado(nombre, par, TIMEFRAME, VELAS_OBJETIVO)
            if len(d) > len(mejor):
                mejor, fuente = d, nombre
            if len(mejor) >= VELAS_OBJETIVO * 0.9:
                break
        except Exception:
            continue
    if len(mejor) < 400:
        return None, None
    df = pd.DataFrame(mejor, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp'), fuente


def calcular_indicadores(df):
    df = df.copy()
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()
    d = df['close'].diff()
    g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    p = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + g / p))
    e12 = df['close'].ewm(span=12, adjust=False).mean()
    e26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = e12 - e26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df.dropna()


def simular(df, margen, rsi_max, stop_loss_pct):
    en_pos = False
    entrada = stop = None
    ops = []

    for i in range(len(df)):
        f = df.iloc[i]
        senal_entrada = (
            f['close'] > f['EMA_200'] * margen
            and f['EMA_9'] > f['EMA_21']
            and f['RSI'] < rsi_max
            and f['MACD'] > f['MACD_signal']
        )
        senal_salida = (f['MACD'] < f['MACD_signal']) or (f['RSI'] > 75)

        if not en_pos:
            if senal_entrada:
                en_pos = True
                entrada = f['close']
                stop = entrada * (1 - stop_loss_pct / 100)
        else:
            if f['low'] <= stop:
                ops.append((stop - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                en_pos = False
            elif senal_salida:
                ops.append((f['close'] - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                en_pos = False

    return ops


def metricas(ops):
    if not ops:
        return {"n": 0, "ret": 0.0, "acierto": 0.0}
    gan = [o for o in ops if o > 0]
    cap = 1000
    for o in ops:
        cap *= (1 + o / 100)
    return {
        "n": len(ops),
        "ret": round((cap - 1000) / 1000 * 100, 1),
        "acierto": round(len(gan) / len(ops) * 100, 1),
    }


def buscar_mejor_combinacion(df_in):
    """Prueba todas las combinaciones y devuelve la de mejor retorno,
    junto con TODOS los resultados (para la prueba de suavidad despues)."""
    resultados = {}
    for margen in MARGENES_EMA:
        for rsi_max in TOPES_RSI:
            for stop in STOPS_LOSS:
                ops = simular(df_in, margen, rsi_max, stop)
                m = metricas(ops)
                if m["n"] >= MIN_OPERACIONES_IN_SAMPLE:
                    resultados[(margen, rsi_max, stop)] = m

    if not resultados:
        return None, resultados

    mejor_combo = max(resultados, key=lambda k: resultados[k]["ret"])
    return mejor_combo, resultados


def prueba_suavidad(mejor_combo, todos_resultados):
    """Revisa si las combinaciones VECINAS al mejor tambien son buenas."""
    margen, rsi_max, stop = mejor_combo
    idx_margen = MARGENES_EMA.index(margen)
    idx_rsi = TOPES_RSI.index(rsi_max)
    idx_stop = STOPS_LOSS.index(stop)

    vecinos_ret = []
    for dm in [-1, 0, 1]:
        for dr in [-1, 0, 1]:
            for ds in [-1, 0, 1]:
                if dm == 0 and dr == 0 and ds == 0:
                    continue
                im, ir, ist = idx_margen + dm, idx_rsi + dr, idx_stop + ds
                if 0 <= im < len(MARGENES_EMA) and 0 <= ir < len(TOPES_RSI) and 0 <= ist < len(STOPS_LOSS):
                    combo_vecino = (MARGENES_EMA[im], TOPES_RSI[ir], STOPS_LOSS[ist])
                    if combo_vecino in todos_resultados:
                        vecinos_ret.append(todos_resultados[combo_vecino]["ret"])

    if not vecinos_ret:
        return "sin vecinos suficientes para evaluar"

    promedio_vecinos = sum(vecinos_ret) / len(vecinos_ret)
    positivos = sum(1 for v in vecinos_ret if v > 0)

    return {
        "n_vecinos": len(vecinos_ret),
        "promedio_vecinos": round(promedio_vecinos, 1),
        "vecinos_positivos": positivos,
        "pct_positivos": round(positivos / len(vecinos_ret) * 100),
    }


def evaluar_activo(simbolo):
    df_bruto, fuente = obtener_historial(simbolo)
    if df_bruto is None:
        return {"simbolo": simbolo, "error": "sin datos suficientes"}

    df = calcular_indicadores(df_bruto)
    if len(df) < 350:
        return {"simbolo": simbolo, "error": f"solo {len(df)} velas"}

    corte = int(len(df) * 0.70)
    df_in = df.iloc[:corte]
    df_out = df.iloc[corte:]

    mejor_combo, todos_resultados = buscar_mejor_combinacion(df_in)
    if mejor_combo is None:
        return {"simbolo": simbolo, "error": "ninguna combinacion tuvo suficientes operaciones"}

    margen, rsi_max, stop = mejor_combo
    m_in = todos_resultados[mejor_combo]

    ops_out = simular(df_out, margen, rsi_max, stop)
    m_out = metricas(ops_out)

    suavidad = prueba_suavidad(mejor_combo, todos_resultados)

    # Comparar tambien contra los parametros ORIGINALES de SOL, para ver
    # si de verdad vale la pena usar numeros propios
    ops_sol_params_out = simular(df_out, 1.015, 65, 5.0)
    m_sol_params_out = metricas(ops_sol_params_out)

    tam = len(df) // VENTANAS
    gana = con_datos = 0
    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        ops_v = simular(df.iloc[ini:fin], margen, rsi_max, stop)
        if not ops_v:
            continue
        con_datos += 1
        if metricas(ops_v)["ret"] > 0:
            gana += 1

    return {
        "simbolo": simbolo,
        "velas": len(df),
        "fuente": fuente,
        "combo": {"margen": margen, "rsi_max": rsi_max, "stop": stop},
        "in": m_in,
        "out": m_out,
        "sol_params_out": m_sol_params_out,
        "suavidad": suavidad,
        "ventanas_ganadas": gana,
        "ventanas_con_datos": con_datos,
        "n_combinaciones_probadas": len(todos_resultados),
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Optimizacion de Parametros Propios - BTC y ETH - {fecha}", ""]
    L.append(
        f"Se probaron {len(MARGENES_EMA)}x{len(TOPES_RSI)}x{len(STOPS_LOSS)} = "
        f"{len(MARGENES_EMA)*len(TOPES_RSI)*len(STOPS_LOSS)} combinaciones de "
        f"margen sobre EMA200, tope de RSI, y stop-loss, buscando la mejor version "
        f"IN-SAMPLE (70% inicial), luego validada en el 30% nunca visto."
    )
    L.append("Comisiones reales incluidas en todo momento.")
    L.append("")

    for r in resultados:
        if "error" in r:
            L.append(f"{r['simbolo']}: no se pudo evaluar ({r['error']})")
            L.append("")
            continue

        L.append(f"=== {r['simbolo']} ({r['velas']} velas, {r['fuente']}) ===")
        L.append(
            f"Mejor combinacion encontrada (de {r['n_combinaciones_probadas']} probadas): "
            f"margen {r['combo']['margen']}, RSI < {r['combo']['rsi_max']}, "
            f"stop-loss {r['combo']['stop']}%"
        )
        L.append(
            f"In-sample (donde se busco): {r['in']['n']} ops, {r['in']['ret']}% retorno, "
            f"{r['in']['acierto']}% acierto"
        )
        L.append(
            f"Out-of-sample (validacion real): {r['out']['n']} ops, {r['out']['ret']}% retorno, "
            f"{r['out']['acierto']}% acierto"
        )
        L.append(f"Ventanas ganadas: {r['ventanas_ganadas']}/{r['ventanas_con_datos']}")
        L.append("")

        L.append("Comparacion directa - misma moneda con los parametros ORIGINALES de SOL:")
        L.append(
            f"  Con parametros de SOL en {r['simbolo']}: {r['sol_params_out']['n']} ops, "
            f"{r['sol_params_out']['ret']}% retorno out-of-sample"
        )
        L.append(
            f"  Con parametros PROPIOS optimizados: {r['out']['ret']}% retorno out-of-sample"
        )
        diferencia = r['out']['ret'] - r['sol_params_out']['ret']
        L.append(f"  Diferencia: {diferencia:+.1f} puntos porcentuales")
        L.append("")

        L.append("Prueba de suavidad (¿es un numero de suerte o una zona real?):")
        s = r['suavidad']
        if isinstance(s, dict):
            L.append(
                f"  De {s['n_vecinos']} combinaciones vecinas a la mejor, "
                f"{s['vecinos_positivos']} fueron rentables ({s['pct_positivos']}%), "
                f"con retorno promedio de {s['promedio_vecinos']}%"
            )
            if s['pct_positivos'] >= 60:
                L.append("  -> Zona ESTABLE: los vecinos tambien funcionan, buena señal.")
            else:
                L.append("  -> Zona INESTABLE: pocos vecinos funcionan, sospecha de sobreajuste.")
        else:
            L.append(f"  {s}")
        L.append("")

    L.append("=== CRITERIO DE DECISION ===")
    L.append(
        "Solo se consideraria adoptar parametros propios para un activo si: "
        "(1) el retorno out-of-sample es POSITIVO, (2) supera claramente a usar "
        "los parametros de SOL en esa misma moneda, (3) gana en la mayoria de "
        "ventanas rodantes, Y (4) la zona es ESTABLE (no un numero de suerte aislado)."
    )
    L.append(
        "Si no cumple las 4, la conclusion seria: ni con numeros propios BTC o ETH "
        "logran una estrategia de tendencia validada en este periodo de mercado."
    )

    return "\n".join(L)


def subir_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado.')
        return
    try:
        m = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
        creds = Credentials.from_service_account_info(
            json.loads(m.group(0)), scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)
        with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
            f.write(texto)
        q = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
        files = service.files().list(q=q, fields='files(id)', pageSize=1).execute().get('files', [])
        media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/plain')
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')
        else:
            meta = {'name': NOMBRE_ARCHIVO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
            service.files().create(body=meta, media_body=media, fields='id').execute()
            print(f'{NOMBRE_ARCHIVO} creado en Drive.')
    except Exception as e:
        print('No se pudo subir:', e)


def main():
    resultados = []
    for simbolo in ACTIVOS:
        print(f"Optimizando {simbolo}...")
        try:
            resultados.append(evaluar_activo(simbolo))
        except Exception as e:
            resultados.append({"simbolo": simbolo, "error": str(e)[:150]})

    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
