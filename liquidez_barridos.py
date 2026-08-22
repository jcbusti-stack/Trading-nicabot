"""
Backtesting - Zonas de Liquidez y Barridos - Trading-nicabot

Concepto (similar a lo que el canal de Telegram marca como "BOS"):
El precio forma varios maximos o minimos parecidos entre si ("iguales") -
ahi se acumulan ordenes de stop-loss de otros traders (liquidez). Cuando
el precio rompe brevemente ese nivel para "barrer" esas ordenes y luego
regresa con fuerza en la direccion contraria, es una señal de reversion.

Reglas concretas (para que sea medible, no subjetivo como en las graficas
dibujadas a mano):
  1. Se buscan "minimos iguales" (varios minimos recientes dentro de un
     margen de tolerancia) - eso es una zona de liquidez hacia abajo.
  2. Si una vela BAJA momentaneamente debajo de esa zona (barre la liquidez)
     pero CIERRA de vuelta arriba de la zona, es señal de COMPRA.
  3. Mismo concepto al reves con maximos iguales, para señales de VENTA.
  4. Stop-loss justo debajo/encima de la mecha del barrido.
  5. Objetivo: 2 veces la distancia del riesgo (relacion 1:2, estandar en
     este tipo de estrategias).

Con comisiones reales incluidas. Se prueba en SOL, BTC y ETH.

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

NOMBRE_ARCHIVO = 'liquidez_barridos_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
EXCHANGES = ['binance', 'bybit', 'okx']
VENTANAS = 6
COSTO_POR_OPERACION_PCT = (0.26 + 0.05) * 2

ACTIVOS = ["SOL", "BTC", "ETH"]

# Parametros del concepto de liquidez
VENTANA_FRACTAL = 2         # velas a cada lado para confirmar un maximo/minimo local
TOLERANCIA_IGUALES_PCT = 0.5  # cuanto pueden diferir 2 minimos/maximos para contar como "iguales"
BUFFER_BARRIDO_PCT = 0.05   # cuanto debe pasar el precio del nivel para contar como "barrido"
RATIO_RIESGO_BENEFICIO = 2.0  # objetivo = 2x la distancia del stop-loss


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


def encontrar_fractales(df):
    """Marca minimos y maximos locales (fractales de N velas a cada lado)."""
    minimos = [False] * len(df)
    maximos = [False] * len(df)
    lows = df['low'].values
    highs = df['high'].values

    for i in range(VENTANA_FRACTAL, len(df) - VENTANA_FRACTAL):
        ventana_low = lows[i - VENTANA_FRACTAL:i + VENTANA_FRACTAL + 1]
        ventana_high = highs[i - VENTANA_FRACTAL:i + VENTANA_FRACTAL + 1]
        if lows[i] == min(ventana_low):
            minimos[i] = True
        if highs[i] == max(ventana_high):
            maximos[i] = True

    return minimos, maximos


def simular_liquidez(df):
    minimos, maximos = encontrar_fractales(df)
    lows = df['low'].values
    highs = df['high'].values
    closes = df['close'].values

    zonas_minimos_recientes = []  # lista de precios de minimos fractales recientes
    zonas_maximos_recientes = []

    en_pos = False
    direccion = None
    entrada = stop = objetivo = None
    ops = []

    MAX_ZONAS_RECORDADAS = 10

    for i in range(len(df)):
        # Actualizar zonas conocidas con fractales confirmados
        if minimos[i]:
            zonas_minimos_recientes.append(lows[i])
            zonas_minimos_recientes = zonas_minimos_recientes[-MAX_ZONAS_RECORDADAS:]
        if maximos[i]:
            zonas_maximos_recientes.append(highs[i])
            zonas_maximos_recientes = zonas_maximos_recientes[-MAX_ZONAS_RECORDADAS:]

        if en_pos:
            if direccion == 'LONG':
                if lows[i] <= stop:
                    ops.append((stop - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                    en_pos = False
                elif highs[i] >= objetivo:
                    ops.append((objetivo - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                    en_pos = False
            else:  # SHORT
                if highs[i] >= stop:
                    ops.append((entrada - stop) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                    en_pos = False
                elif lows[i] <= objetivo:
                    ops.append((entrada - objetivo) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                    en_pos = False
            continue

        # Buscar "minimos iguales" -> zona de liquidez hacia abajo
        for j in range(len(zonas_minimos_recientes)):
            for k in range(j + 1, len(zonas_minimos_recientes)):
                nivel_a = zonas_minimos_recientes[j]
                nivel_b = zonas_minimos_recientes[k]
                if abs(nivel_a - nivel_b) / nivel_a * 100 <= TOLERANCIA_IGUALES_PCT:
                    nivel_zona = min(nivel_a, nivel_b)
                    umbral_barrido = nivel_zona * (1 - BUFFER_BARRIDO_PCT / 100)
                    if lows[i] < umbral_barrido and closes[i] > nivel_zona:
                        entrada = closes[i]
                        stop = lows[i]
                        riesgo = entrada - stop
                        if riesgo > 0:
                            objetivo = entrada + riesgo * RATIO_RIESGO_BENEFICIO
                            en_pos = True
                            direccion = 'LONG'
                        break
            if en_pos:
                break
        if en_pos:
            continue

        # Buscar "maximos iguales" -> zona de liquidez hacia arriba
        for j in range(len(zonas_maximos_recientes)):
            for k in range(j + 1, len(zonas_maximos_recientes)):
                nivel_a = zonas_maximos_recientes[j]
                nivel_b = zonas_maximos_recientes[k]
                if abs(nivel_a - nivel_b) / nivel_a * 100 <= TOLERANCIA_IGUALES_PCT:
                    nivel_zona = max(nivel_a, nivel_b)
                    umbral_barrido = nivel_zona * (1 + BUFFER_BARRIDO_PCT / 100)
                    if highs[i] > umbral_barrido and closes[i] < nivel_zona:
                        entrada = closes[i]
                        stop = highs[i]
                        riesgo = stop - entrada
                        if riesgo > 0:
                            objetivo = entrada - riesgo * RATIO_RIESGO_BENEFICIO
                            en_pos = True
                            direccion = 'SHORT'
                        break
            if en_pos:
                break

    return ops


def metricas(ops):
    if not ops:
        return {"n": 0, "ret": 0.0, "acierto": 0.0, "prom": 0.0}
    gan = [o for o in ops if o > 0]
    cap = 1000
    for o in ops:
        cap *= (1 + o / 100)
    return {
        "n": len(ops),
        "ret": round((cap - 1000) / 1000 * 100, 1),
        "acierto": round(len(gan) / len(ops) * 100, 1),
        "prom": round(sum(ops) / len(ops), 2),
    }


def evaluar_activo(simbolo):
    df, fuente = obtener_historial(simbolo)
    if df is None:
        return {"simbolo": simbolo, "error": "sin datos suficientes"}

    if len(df) < 350:
        return {"simbolo": simbolo, "error": f"solo {len(df)} velas"}

    corte = int(len(df) * 0.70)
    ops_in = simular_liquidez(df.iloc[:corte].reset_index(drop=True))
    ops_out = simular_liquidez(df.iloc[corte:].reset_index(drop=True))
    m_in = metricas(ops_in)
    m_out = metricas(ops_out)

    tam = len(df) // VENTANAS
    gana = con_datos = 0
    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        ops_v = simular_liquidez(df.iloc[ini:fin].reset_index(drop=True))
        if not ops_v:
            continue
        con_datos += 1
        if metricas(ops_v)["ret"] > 0:
            gana += 1

    return {
        "simbolo": simbolo,
        "velas": len(df),
        "fuente": fuente,
        "in": m_in,
        "out": m_out,
        "ventanas_ganadas": gana,
        "ventanas_con_datos": con_datos,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Zonas de Liquidez y Barridos - {fecha}", ""]
    L.append(
        f"Concepto: comprar cuando el precio 'barre' minimos iguales y cierra "
        f"de vuelta arriba (o al reves con maximos, para vender). Objetivo "
        f"1:{RATIO_RIESGO_BENEFICIO:.0f} riesgo-beneficio. Comisiones incluidas."
    )
    L.append(
        f"Tolerancia para 'niveles iguales': {TOLERANCIA_IGUALES_PCT}%. "
        f"Fractal de {VENTANA_FRACTAL} velas a cada lado."
    )
    L.append("")

    for r in resultados:
        if "error" in r:
            L.append(f"{r['simbolo']}: no se pudo evaluar ({r['error']})")
            L.append("")
            continue

        L.append(f"=== {r['simbolo']} ({r['velas']} velas, {r['fuente']}) ===")
        L.append(
            f"In-sample: {r['in']['n']} ops, {r['in']['ret']}% retorno, "
            f"{r['in']['acierto']}% acierto, {r['in']['prom']}% promedio/op"
        )
        L.append(
            f"Out-of-sample: {r['out']['n']} ops, {r['out']['ret']}% retorno, "
            f"{r['out']['acierto']}% acierto, {r['out']['prom']}% promedio/op"
        )
        L.append(f"Ventanas ganadas: {r['ventanas_ganadas']}/{r['ventanas_con_datos']}")
        L.append("")

    L.append("=== COMO LEER ESTO ===")
    L.append(
        "Debe ganar en AMBOS periodos (in y out) Y en la mayoria de ventanas "
        "para considerarse un candidato real, igual que con todas las ideas "
        "anteriores."
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
        print(f"Evaluando {simbolo}...")
        try:
            resultados.append(evaluar_activo(simbolo))
        except Exception as e:
            resultados.append({"simbolo": simbolo, "error": str(e)[:100]})

    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
