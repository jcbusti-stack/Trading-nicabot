"""
Simulacion Monte Carlo con comisiones - Trading-nicabot

Responde dos preguntas que ningun backtesting anterior respondio:

  1. ¿El 45.4% de retorno fue por la estrategia, o por la SUERTE del orden en
     que salieron las operaciones? Monte Carlo baraja el orden miles de veces
     y muestra el rango real de resultados posibles.

  2. ¿Cuanto se come Kraken en comisiones? Todos los backtestings anteriores
     las ignoraban. Aqui se descuentan de verdad.

Tambien calcula el DRAWDOWN MAXIMO: la peor caida desde un pico, que es lo
que de verdad se siente al operar en vivo.

Herramienta MANUAL. No cambia nada del bot.
"""

import json
import os
import random
import re
import time
from datetime import datetime

import ccxt
import pandas as pd

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_ARCHIVO = 'montecarlo_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
STOP_LOSS_PCT = 5.0
EXCHANGES = ['binance', 'bybit', 'kraken', 'okx']

# Comisiones reales de Kraken (spot, nivel basico).
# Se cobra al entrar Y al salir, por eso se cuenta dos veces por operacion.
COMISION_POR_LADO_PCT = 0.26
# Deslizamiento: diferencia entre el precio esperado y el real al ejecutar.
DESLIZAMIENTO_POR_LADO_PCT = 0.05

SIMULACIONES = 5000
CAPITAL_INICIAL = 1000


def descargar_paginado(nombre_exchange, par, timeframe, objetivo):
    ex = getattr(ccxt, nombre_exchange)({'enableRateLimit': True})
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


def obtener_historial():
    mejor, fuente = [], None
    for nombre in EXCHANGES:
        try:
            print(f"Intentando {nombre}...")
            d = descargar_paginado(nombre, PAR, TIMEFRAME, VELAS_OBJETIVO)
            print(f"  {nombre}: {len(d)} velas.")
            if len(d) > len(mejor):
                mejor, fuente = d, nombre
            if len(mejor) >= VELAS_OBJETIVO * 0.9:
                break
        except Exception as e:
            print(f"  {nombre} no disponible: {e}")
    if not mejor:
        raise RuntimeError("Sin datos.")
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


def simular(df, con_costos):
    """Devuelve la lista de resultados por operacion, en porcentaje."""
    costo_total = 0.0
    if con_costos:
        costo_total = (COMISION_POR_LADO_PCT + DESLIZAMIENTO_POR_LADO_PCT) * 2

    en_pos = False
    entrada = None
    stop = None
    ops = []

    for i in range(len(df)):
        f = df.iloc[i]
        c_t = f['close'] > f['EMA_200'] * 1.015
        c_r = f['RSI'] < 65
        c_m = f['MACD'] > f['MACD_signal']
        c_mo = f['EMA_9'] > f['EMA_21']
        entrada_ok = c_t and c_r and c_m and c_mo
        salida_ok = (f['MACD'] < f['MACD_signal']) or (f['RSI'] > 75)

        if not en_pos:
            if entrada_ok:
                en_pos = True
                entrada = f['close']
                stop = entrada * (1 - STOP_LOSS_PCT / 100)
        else:
            if f['low'] <= stop:
                bruto = (stop - entrada) / entrada * 100
                ops.append(bruto - costo_total)
                en_pos = False
            elif salida_ok:
                bruto = (f['close'] - entrada) / entrada * 100
                ops.append(bruto - costo_total)
                en_pos = False

    return ops


def calcular_curva(ops, capital=CAPITAL_INICIAL):
    """Devuelve capital final y drawdown maximo (peor caida desde un pico)."""
    cap = capital
    pico = capital
    max_dd = 0.0
    for o in ops:
        cap *= (1 + o / 100)
        if cap > pico:
            pico = cap
        caida = (pico - cap) / pico * 100
        if caida > max_dd:
            max_dd = caida
    retorno = (cap - capital) / capital * 100
    return retorno, max_dd


def percentil(lista, p):
    if not lista:
        return 0.0
    ordenada = sorted(lista)
    idx = int(len(ordenada) * p / 100)
    idx = min(idx, len(ordenada) - 1)
    return ordenada[idx]


def monte_carlo(ops):
    """Baraja el orden de las operaciones miles de veces."""
    retornos = []
    drawdowns = []
    ops_copia = list(ops)

    for _ in range(SIMULACIONES):
        random.shuffle(ops_copia)
        r, dd = calcular_curva(ops_copia)
        retornos.append(r)
        drawdowns.append(dd)

    negativos = sum(1 for r in retornos if r < 0)

    return {
        "peor": round(min(retornos), 1),
        "p5": round(percentil(retornos, 5), 1),
        "p25": round(percentil(retornos, 25), 1),
        "mediana": round(percentil(retornos, 50), 1),
        "p75": round(percentil(retornos, 75), 1),
        "p95": round(percentil(retornos, 95), 1),
        "mejor": round(max(retornos), 1),
        "prob_perdida": round(negativos / len(retornos) * 100, 1),
        "dd_mediana": round(percentil(drawdowns, 50), 1),
        "dd_p95": round(percentil(drawdowns, 95), 1),
        "dd_peor": round(max(drawdowns), 1),
    }


def generar_reporte(sin_c, con_c, mc, meta):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Monte Carlo con comisiones (SOL) - {fecha}", ""]
    L.append(
        f"Datos: {meta['velas']} velas de 12h ({meta['desde']} a {meta['hasta']}), "
        f"fuente {meta['fuente']}. {meta['n_ops']} operaciones totales."
    )
    L.append(
        f"Costos aplicados: {COMISION_POR_LADO_PCT}% de comision + "
        f"{DESLIZAMIENTO_POR_LADO_PCT}% de deslizamiento, por cada lado. "
        f"Total {(COMISION_POR_LADO_PCT + DESLIZAMIENTO_POR_LADO_PCT) * 2:.2f}% por operacion completa."
    )
    L.append("")

    L.append("=== PARTE 1: IMPACTO REAL DE LAS COMISIONES ===")
    L.append("")
    L.append(f"SIN descontar costos (como en los backtestings anteriores):")
    L.append(f"  Retorno total: {sin_c['ret']}% | drawdown maximo: {sin_c['dd']}%")
    L.append(f"CON costos reales de Kraken descontados:")
    L.append(f"  Retorno total: {con_c['ret']}% | drawdown maximo: {con_c['dd']}%")
    diferencia = sin_c['ret'] - con_c['ret']
    L.append("")
    L.append(f"Las comisiones se comen {diferencia:.1f} puntos porcentuales de retorno.")
    if con_c['ret'] > 0:
        L.append("La estrategia SIGUE siendo rentable despues de costos.")
    else:
        L.append("ALERTA: la estrategia deja de ser rentable despues de costos.")
    L.append("")

    L.append("=== PARTE 2: MONTE CARLO (¿fue estrategia o suerte?) ===")
    L.append(
        f"Se baraja el orden de las {meta['n_ops']} operaciones {SIMULACIONES} veces, "
        "con costos incluidos. Esto muestra el rango de resultados que pudo haber "
        "ocurrido con las mismas operaciones en distinto orden."
    )
    L.append("")
    L.append(f"Peor caso de {SIMULACIONES} simulaciones: {mc['peor']}%")
    L.append(f"Percentil 5 (muy mala suerte):        {mc['p5']}%")
    L.append(f"Percentil 25:                          {mc['p25']}%")
    L.append(f"MEDIANA (el caso tipico):              {mc['mediana']}%")
    L.append(f"Percentil 75:                          {mc['p75']}%")
    L.append(f"Percentil 95 (muy buena suerte):       {mc['p95']}%")
    L.append(f"Mejor caso:                            {mc['mejor']}%")
    L.append("")
    L.append(f"Probabilidad de terminar en perdida: {mc['prob_perdida']}%")
    L.append("")

    L.append("=== PARTE 3: DRAWDOWN (lo que de verdad se siente) ===")
    L.append(
        "El drawdown es la peor caida desde un pico. Es lo que veras en tu cuenta "
        "en el peor momento, aunque al final termines en ganancia."
    )
    L.append("")
    L.append(f"Drawdown tipico (mediana):    {mc['dd_mediana']}%")
    L.append(f"Drawdown en mal escenario:    {mc['dd_p95']}%")
    L.append(f"Peor drawdown simulado:       {mc['dd_peor']}%")
    L.append("")
    L.append(
        "IMPORTANTE: tu perfil dice que tu limite de drawdown mensual aceptable "
        "es 5%. Compara ese numero con los de arriba. Si el drawdown tipico de la "
        "estrategia supera tu tolerancia, el problema no es la estrategia sino el "
        "tamaño de posicion que uses."
    )
    L.append("")

    L.append("=== COMO LEER ESTO ===")
    L.append(
        "- Si la MEDIANA es claramente positiva y la probabilidad de perdida es "
        "baja, la estrategia tiene ventaja real, no fue suerte."
    )
    L.append(
        "- Si el percentil 5 es muy negativo, significa que una mala racha de "
        "operaciones puede dolerte fuerte aunque la estrategia sea buena."
    )
    L.append(
        "- El drawdown es la cifra mas util para decidir cuanto capital arriesgar."
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
    df_bruto, fuente = obtener_historial()
    df = calcular_indicadores(df_bruto)

    print("Simulando sin costos...")
    ops_sin = simular(df, con_costos=False)
    r_sin, dd_sin = calcular_curva(ops_sin)

    print("Simulando con costos reales...")
    ops_con = simular(df, con_costos=True)
    r_con, dd_con = calcular_curva(ops_con)

    print(f"Corriendo {SIMULACIONES} simulaciones Monte Carlo...")
    mc = monte_carlo(ops_con)

    meta = {
        "velas": len(df),
        "desde": df.index[0].strftime("%Y-%m-%d"),
        "hasta": df.index[-1].strftime("%Y-%m-%d"),
        "fuente": fuente,
        "n_ops": len(ops_con),
    }

    reporte = generar_reporte(
        {"ret": round(r_sin, 1), "dd": round(dd_sin, 1)},
        {"ret": round(r_con, 1), "dd": round(dd_con, 1)},
        mc, meta
    )
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
