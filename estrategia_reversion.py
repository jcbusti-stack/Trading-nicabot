"""
Backtesting - Estrategia de Reversion a la Media (RSI sobreventa) - Trading-nicabot

Busca una SEGUNDA estrategia genuinamente distinta a la de SOL (que sigue
tendencia). Esta busca "rebotes": comprar cuando el precio esta muy castigado
(RSI bajo) y vender cuando se recupera un poco, apostando a que rebota en
vez de seguir cayendo o subiendo.

Objetivo: si esta estrategia gana dinero en OTROS periodos distintos a
cuando gana la de SOL, se podrian combinar para reducir la varianza total
(diversificacion real, no solo aplicar la misma formula a otro activo).

AVISO: el grid trading (tambien basado en reversion) ya fallo en 28 activos
por las tendencias fuertes del periodo. Es posible que esto tambien falle -
se prueba con los mismos criterios estrictos de siempre.

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

NOMBRE_ARCHIVO = 'estrategia_reversion_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
EXCHANGES = ['binance', 'bybit', 'okx']
VENTANAS = 6
COSTO_POR_OPERACION_PCT = (0.26 + 0.05) * 2

# Se prueba en SOL (para comparar directo contra la estrategia de tendencia)
# y en un par de activos que fallaron la estrategia de tendencia, para ver
# si tal vez SI les queda mejor la reversion.
ACTIVOS = ["SOL", "ADA", "AVAX"]

# Parametros de la estrategia de reversion
RSI_ENTRADA = 30       # comprar cuando el RSI cae por debajo de esto (sobreventa)
RSI_SALIDA = 55         # vender cuando el RSI se recupera hasta aqui
STOP_LOSS_PCT = 8.0     # mas ancho que la de tendencia, porque comprar en caida es mas volatil


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
    d = df['close'].diff()
    g = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    p = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + g / p))
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()
    return df.dropna()


def simular_reversion(df):
    """Compra en sobreventa (RSI bajo), vende cuando se recupera o toca stop."""
    en_pos = False
    entrada = stop = None
    ops = []
    fechas_ops = []

    for i in range(len(df)):
        f = df.iloc[i]

        if not en_pos:
            if f['RSI'] < RSI_ENTRADA:
                en_pos = True
                entrada = f['close']
                stop = entrada * (1 - STOP_LOSS_PCT / 100)
                fecha_entrada = df.index[i]
        else:
            if f['low'] <= stop:
                pnl = (stop - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT
                ops.append(pnl)
                fechas_ops.append((fecha_entrada, df.index[i]))
                en_pos = False
            elif f['RSI'] > RSI_SALIDA:
                pnl = (f['close'] - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT
                ops.append(pnl)
                fechas_ops.append((fecha_entrada, df.index[i]))
                en_pos = False

    return ops, fechas_ops


def simular_tendencia(df):
    """La estrategia YA validada de SOL, para comparar periodos de ganancia."""
    df = df.copy()
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    e12 = df['close'].ewm(span=12, adjust=False).mean()
    e26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = e12 - e26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df = df.dropna()

    en_pos = False
    entrada = stop = None
    ops = []
    fechas_ops = []

    for i in range(len(df)):
        f = df.iloc[i]
        senal_entrada = (
            f['close'] > f['EMA_200'] * 1.015
            and f['EMA_9'] > f['EMA_21']
            and f['RSI'] < 65
            and f['MACD'] > f['MACD_signal']
        )
        senal_salida = (f['MACD'] < f['MACD_signal']) or (f['RSI'] > 75)

        if not en_pos:
            if senal_entrada:
                en_pos = True
                entrada = f['close']
                stop = entrada * 0.95
                fecha_entrada = df.index[i]
        else:
            if f['low'] <= stop:
                ops.append((stop - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                fechas_ops.append((fecha_entrada, df.index[i]))
                en_pos = False
            elif senal_salida:
                ops.append((f['close'] - entrada) / entrada * 100 - COSTO_POR_OPERACION_PCT)
                fechas_ops.append((fecha_entrada, df.index[i]))
                en_pos = False

    return ops, fechas_ops


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


def calcular_correlacion_temporal(fechas_a, fechas_b):
    """
    Revisa si las operaciones GANADORAS de la estrategia A caen en periodos
    donde la estrategia B tambien esta ganando (correlacionadas) o en
    periodos distintos (buena señal de diversificacion real).
    """
    if not fechas_a or not fechas_b:
        return "Sin suficientes operaciones para comparar"

    solapes = 0
    for inicio_a, fin_a in fechas_a:
        for inicio_b, fin_b in fechas_b:
            if inicio_a <= fin_b and inicio_b <= fin_a:
                solapes += 1
                break

    pct_solape = round(solapes / len(fechas_a) * 100) if fechas_a else 0
    return f"{pct_solape}% de las operaciones de reversion se solapan en tiempo con operaciones de tendencia"


def evaluar_activo(simbolo):
    df_bruto, fuente = obtener_historial(simbolo)
    if df_bruto is None:
        return {"simbolo": simbolo, "error": "sin datos suficientes"}

    df = calcular_indicadores(df_bruto)
    if len(df) < 350:
        return {"simbolo": simbolo, "error": f"solo {len(df)} velas utiles"}

    corte = int(len(df) * 0.70)

    ops_in, _ = simular_reversion(df.iloc[:corte])
    ops_out, fechas_out_rev = simular_reversion(df.iloc[corte:])
    m_in = metricas(ops_in)
    m_out = metricas(ops_out)

    ops_tend_out, fechas_out_tend = simular_tendencia(df.iloc[corte:])
    m_tend_out = metricas(ops_tend_out)

    tam = len(df) // VENTANAS
    gana = con_datos = 0
    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        ops_v, _ = simular_reversion(df.iloc[ini:fin])
        if not ops_v:
            continue
        con_datos += 1
        if metricas(ops_v)["ret"] > 0:
            gana += 1

    correlacion = calcular_correlacion_temporal(fechas_out_rev, fechas_out_tend)

    return {
        "simbolo": simbolo,
        "velas": len(df),
        "fuente": fuente,
        "in": m_in,
        "out": m_out,
        "tendencia_out": m_tend_out,
        "ventanas_ganadas": gana,
        "ventanas_con_datos": con_datos,
        "correlacion": correlacion,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Estrategia de Reversion a la Media - {fecha}", ""]
    L.append(
        f"Regla: comprar cuando RSI < {RSI_ENTRADA} (sobreventa), vender cuando "
        f"RSI > {RSI_SALIDA} o stop-loss de {STOP_LOSS_PCT}%. Comisiones incluidas."
    )
    L.append(
        "Objetivo: encontrar una estrategia que gane en periodos DISTINTOS a "
        "cuando gana la estrategia de tendencia de SOL, para diversificar de verdad."
    )
    L.append("")

    for r in resultados:
        if "error" in r:
            L.append(f"{r['simbolo']}: no se pudo evaluar ({r['error']})")
            L.append("")
            continue

        L.append(f"=== {r['simbolo']} ({r['velas']} velas, {r['fuente']}) ===")
        L.append(
            f"Reversion in-sample: {r['in']['n']} ops, {r['in']['ret']}% retorno, "
            f"{r['in']['acierto']}% acierto"
        )
        L.append(
            f"Reversion out-of-sample: {r['out']['n']} ops, {r['out']['ret']}% retorno, "
            f"{r['out']['acierto']}% acierto"
        )
        L.append(f"Ventanas ganadas: {r['ventanas_ganadas']}/{r['ventanas_con_datos']}")
        L.append(
            f"Comparacion - Tendencia (SOL) out-of-sample en el mismo periodo: "
            f"{r['tendencia_out']['n']} ops, {r['tendencia_out']['ret']}% retorno"
        )
        L.append(f"Solape temporal: {r['correlacion']}")
        L.append("")

    L.append("=== COMO LEER ESTO ===")
    L.append(
        "- Si el retorno de reversion es negativo, no sirve como estrategia "
        "propia, sin importar el solape."
    )
    L.append(
        "- Si el retorno es positivo Y el solape temporal es bajo (menos del "
        "40-50%), es un candidato real para diversificar - gana cuando SOL no gana."
    )
    L.append(
        "- Si el retorno es positivo pero el solape es alto, no diversifica de "
        "verdad, solo es otra forma de apostar a lo mismo."
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
