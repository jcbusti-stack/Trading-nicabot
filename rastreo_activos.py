"""
Rastreo Sistematico de Activos - Trading-nicabot
Inspirado en el principio de Renaissance: buscar varias ventajas pequeñas en
activos poco correlacionados, en vez de una sola estrategia genial.

Aplica la MISMA estrategia validada de SOL a ~25 activos y los somete a la
metodologia completa establecida en trading-profile-1.md:
  1. In-sample / out-of-sample: debe funcionar en AMBOS
  2. Ventanas rodantes: debe ganar en la mayoria
  3. Comisiones incluidas SIEMPRE
  4. Minimo de operaciones para que sea estadisticamente serio

Lo esperable es que la mayoria FALLE. Ese es el punto: Renaissance descartaba
casi todo lo que probaba. Encontrar 2 o 3 activos validos ya seria un exito.

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

NOMBRE_ARCHIVO = 'rastreo_activos_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
PORCENTAJE_IN_SAMPLE = 0.70
STOP_LOSS_PCT = 5.0
VENTANAS = 6

# Costos reales de Kraken (comision + deslizamiento, por cada lado)
COSTO_POR_OPERACION_PCT = (0.26 + 0.05) * 2

# Criterios de aprobacion (estrictos a proposito)
MIN_OPERACIONES_OUT = 8      # muestra minima para tomarlo en serio
MIN_RETORNO_OUT = 10.0       # % de retorno out-of-sample, ya con costos
MIN_RETORNO_IN = 10.0        # tambien debe funcionar in-sample
MIN_VENTANAS_GANADAS = 0.55  # debe ganar en mas del 55% de ventanas con datos

EXCHANGES = ['binance', 'bybit', 'okx']

# Universo amplio: L1, L2, DeFi, memecoins, RWA, infraestructura
ACTIVOS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "LINK", "MATIC",
    "ATOM", "NEAR", "APT", "ARB", "OP", "INJ", "TIA", "SUI", "LTC", "UNI",
    "AAVE", "FIL", "ICP", "DOGE", "ONDO", "RENDER", "SEI", "TON",
]


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


def simular(df):
    en_pos = False
    entrada = stop = None
    ops = []
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
                stop = entrada * (1 - STOP_LOSS_PCT / 100)
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
        return {"n": 0, "ret": 0.0, "acierto": 0.0, "prom": 0.0, "dd": 0.0}
    gan = [o for o in ops if o > 0]
    cap = pico = 1000.0
    max_dd = 0.0
    for o in ops:
        cap *= (1 + o / 100)
        pico = max(pico, cap)
        max_dd = max(max_dd, (pico - cap) / pico * 100)
    return {
        "n": len(ops),
        "ret": round((cap - 1000) / 1000 * 100, 1),
        "acierto": round(len(gan) / len(ops) * 100, 1),
        "prom": round(sum(ops) / len(ops), 2),
        "dd": round(max_dd, 1),
    }


def evaluar_activo(simbolo):
    df_bruto, fuente = obtener_historial(simbolo)
    if df_bruto is None:
        return {"simbolo": simbolo, "error": "sin datos suficientes"}

    df = calcular_indicadores(df_bruto)
    if len(df) < 350:
        return {"simbolo": simbolo, "error": f"solo {len(df)} velas utiles"}

    corte = int(len(df) * PORCENTAJE_IN_SAMPLE)
    m_in = metricas(simular(df.iloc[:corte]))
    m_out = metricas(simular(df.iloc[corte:]))

    # Ventanas rodantes
    tam = len(df) // VENTANAS
    ganadas = con_datos = 0
    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        ops_v = simular(df.iloc[ini:fin])
        if not ops_v:
            continue
        con_datos += 1
        if metricas(ops_v)["ret"] > 0:
            ganadas += 1

    ratio_ventanas = (ganadas / con_datos) if con_datos > 0 else 0.0

    aprueba = (
        m_out["n"] >= MIN_OPERACIONES_OUT
        and m_out["ret"] >= MIN_RETORNO_OUT
        and m_in["ret"] >= MIN_RETORNO_IN
        and ratio_ventanas >= MIN_VENTANAS_GANADAS
    )

    return {
        "simbolo": simbolo,
        "fuente": fuente,
        "velas": len(df),
        "desde": df.index[0].strftime("%Y-%m"),
        "in": m_in,
        "out": m_out,
        "ventanas_ganadas": ganadas,
        "ventanas_con_datos": con_datos,
        "ratio": round(ratio_ventanas * 100),
        "aprueba": aprueba,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Rastreo Sistematico de Activos - {fecha}", ""]
    L.append(
        "Metodo: misma estrategia validada de SOL (EMA 9/21/200 + RSI + MACD + "
        f"stop-loss 5%), velas de 12h, CON comisiones ({COSTO_POR_OPERACION_PCT:.2f}% "
        "por operacion completa) descontadas."
    )
    L.append("")
    L.append("CRITERIOS DE APROBACION (estrictos a proposito):")
    L.append(f"  - Minimo {MIN_OPERACIONES_OUT} operaciones out-of-sample")
    L.append(f"  - Retorno out-of-sample >= {MIN_RETORNO_OUT}%")
    L.append(f"  - Retorno in-sample >= {MIN_RETORNO_IN}% (debe funcionar en AMBOS periodos)")
    L.append(f"  - Ganar en >= {int(MIN_VENTANAS_GANADAS*100)}% de las ventanas rodantes")
    L.append("")

    validos = [r for r in resultados if r.get("aprueba")]
    fallidos = [r for r in resultados if not r.get("aprueba") and "error" not in r]
    sin_datos = [r for r in resultados if "error" in r]

    L.append("=" * 50)
    L.append(f"RESULTADO: {len(validos)} activos APROBADOS de {len(resultados)} evaluados")
    L.append("=" * 50)
    L.append("")

    if validos:
        L.append(">>> ACTIVOS QUE PASAN TODA LA VALIDACION <<<")
        L.append("")
        for r in sorted(validos, key=lambda x: x["out"]["ret"], reverse=True):
            L.append(f"{r['simbolo']} (desde {r['desde']}, {r['velas']} velas, {r['fuente']}):")
            L.append(
                f"  Out-of-sample: {r['out']['n']} ops, {r['out']['ret']}% retorno, "
                f"{r['out']['acierto']}% acierto, {r['out']['prom']}% por operacion, "
                f"drawdown {r['out']['dd']}%"
            )
            L.append(
                f"  In-sample: {r['in']['n']} ops, {r['in']['ret']}% retorno, "
                f"{r['in']['acierto']}% acierto"
            )
            L.append(f"  Ventanas ganadas: {r['ventanas_ganadas']} de {r['ventanas_con_datos']} ({r['ratio']}%)")
            L.append("")
    else:
        L.append("Ningun activo paso todos los criterios.")
        L.append(
            "Esto NO es un fracaso: significa que la estrategia es especifica de SOL "
            "y no debe aplicarse a ciegas a otros activos."
        )
        L.append("")

    L.append("--- Activos que NO pasaron (ordenados por retorno out-of-sample) ---")
    L.append("")
    for r in sorted(fallidos, key=lambda x: x["out"]["ret"], reverse=True):
        razones = []
        if r["out"]["n"] < MIN_OPERACIONES_OUT:
            razones.append(f"solo {r['out']['n']} ops")
        if r["out"]["ret"] < MIN_RETORNO_OUT:
            razones.append(f"out {r['out']['ret']}%")
        if r["in"]["ret"] < MIN_RETORNO_IN:
            razones.append(f"in {r['in']['ret']}%")
        if r["ratio"] < MIN_VENTANAS_GANADAS * 100:
            razones.append(f"ventanas {r['ratio']}%")
        L.append(f"- {r['simbolo']}: falla por {', '.join(razones)}")

    if sin_datos:
        L.append("")
        L.append("--- Sin datos suficientes ---")
        for r in sin_datos:
            L.append(f"- {r['simbolo']}: {r['error']}")

    L.append("")
    L.append("=== QUE HACER CON ESTO ===")
    L.append(
        "- Si aparecen 2 o 3 activos aprobados, considerar repartir el capital "
        "destinado a la estrategia (~$300) entre ellos y SOL, en vez de todo en SOL. "
        "Eso reduce la varianza sin cambiar la estrategia."
    )
    L.append(
        "- ANTES de operar cualquiera de ellos con dinero real, correr la validacion "
        "completa (validacion_momentum.py adaptado) sobre ese activo especifico."
    )
    L.append(
        "- Si ninguno pasa, la conclusion honesta es que la ventaja es especifica de "
        "SOL. Mantener el plan actual y no forzar diversificacion artificial."
    )
    L.append(
        "- OJO con la correlacion: si dos activos aprobados suben y bajan juntos "
        "(como suele pasar entre altcoins), diversificar entre ellos protege menos "
        "de lo que parece."
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
    total = len(ACTIVOS)
    for i, simbolo in enumerate(ACTIVOS, 1):
        print(f"[{i}/{total}] Evaluando {simbolo}...")
        try:
            resultados.append(evaluar_activo(simbolo))
        except Exception as e:
            resultados.append({"simbolo": simbolo, "error": str(e)[:80]})

    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
