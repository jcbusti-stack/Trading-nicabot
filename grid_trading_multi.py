"""
Backtesting - Grid Trading Multi-Activo - Trading-nicabot

Aplica la misma prueba de grid trading (vs comprar y mantener) que se hizo
para SOL, pero a los mismos 28 activos del rastreo sistematico. Busca
especificamente activos con comportamiento LATERAL, donde grid trading
deberia tener mas ventaja que en activos con tendencias fuertes como SOL.

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

NOMBRE_ARCHIVO = 'grid_trading_multi_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
EXCHANGES = ['binance', 'bybit', 'okx']

CAPITAL_INICIAL = 1000
N_GRIDS = 10
VELAS_LOOKBACK = 60
VELAS_REBALANCEO = 90
MARGEN_RANGO_PCT = 5.0
COSTO_POR_LADO_PCT = 0.26 + 0.05
VENTANAS = 6

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


def simular_grid(df):
    cash = CAPITAL_INICIAL
    holdings_por_nivel = None
    grid_levels = None
    capital_por_grid = None
    operaciones = 0
    curva_capital = []
    precio_anterior = None

    for i in range(len(df)):
        precio = df.iloc[i]['close']
        necesita_recalculo = grid_levels is None or i % VELAS_REBALANCEO == 0

        if necesita_recalculo and i >= VELAS_LOOKBACK:
            if holdings_por_nivel:
                for qty in holdings_por_nivel.values():
                    if qty > 0:
                        cash += qty * precio * (1 - COSTO_POR_LADO_PCT / 100)
                        operaciones += 1

            ventana = df.iloc[max(0, i - VELAS_LOOKBACK):i + 1]
            minimo = ventana['low'].min() * (1 - MARGEN_RANGO_PCT / 100)
            maximo = ventana['high'].max() * (1 + MARGEN_RANGO_PCT / 100)
            grid_levels = [minimo + (maximo - minimo) * j / N_GRIDS for j in range(N_GRIDS + 1)]
            holdings_por_nivel = {j: 0.0 for j in range(N_GRIDS)}
            capital_por_grid = cash / N_GRIDS if N_GRIDS > 0 else cash

        if grid_levels is not None and precio_anterior is not None:
            for j in range(N_GRIDS):
                nivel = grid_levels[j]
                cruzo_abajo = precio_anterior > nivel >= precio
                cruzo_arriba = precio_anterior < grid_levels[j + 1] <= precio

                if cruzo_abajo and holdings_por_nivel[j] == 0 and cash >= capital_por_grid:
                    qty = (capital_por_grid / nivel) * (1 - COSTO_POR_LADO_PCT / 100)
                    holdings_por_nivel[j] = qty
                    cash -= capital_por_grid
                    operaciones += 1

                if cruzo_arriba and holdings_por_nivel[j] > 0:
                    qty = holdings_por_nivel[j]
                    cash += qty * grid_levels[j + 1] * (1 - COSTO_POR_LADO_PCT / 100)
                    holdings_por_nivel[j] = 0.0
                    operaciones += 1

        valor_holdings = sum(holdings_por_nivel.values()) * precio if holdings_por_nivel else 0.0
        curva_capital.append(cash + valor_holdings)
        precio_anterior = precio

    return curva_capital, operaciones


def simular_buy_and_hold(df):
    precio_inicial = df.iloc[0]['close']
    qty = (CAPITAL_INICIAL / precio_inicial) * (1 - COSTO_POR_LADO_PCT / 100)
    return [qty * df.iloc[i]['close'] for i in range(len(df))]


def metricas_curva(curva):
    if not curva:
        return {"retorno": 0.0, "dd": 0.0}
    inicial = curva[0]
    pico = curva[0]
    max_dd = 0.0
    for v in curva:
        pico = max(pico, v)
        dd = (pico - v) / pico * 100 if pico > 0 else 0
        max_dd = max(max_dd, dd)
    retorno = (curva[-1] - inicial) / inicial * 100
    return {"retorno": round(retorno, 1), "dd": round(max_dd, 1)}


def evaluar_activo(simbolo):
    df, fuente = obtener_historial(simbolo)
    if df is None:
        return {"simbolo": simbolo, "error": "sin datos suficientes"}

    curva_grid, ops = simular_grid(df)
    curva_bh = simular_buy_and_hold(df)
    m_grid = metricas_curva(curva_grid)
    m_bh = metricas_curva(curva_bh)

    corte = int(len(df) * 0.70)
    m_grid_out = metricas_curva(curva_grid[corte:])
    m_bh_out = metricas_curva(curva_bh[corte:])

    tam = len(df) // VENTANAS
    gana_grid = con_datos = 0
    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        if fin - ini < VELAS_LOOKBACK + 10:
            continue
        trozo = df.iloc[ini:fin].reset_index(drop=True)
        c_g, _ = simular_grid(trozo)
        c_b = simular_buy_and_hold(trozo)
        r_g = metricas_curva(c_g)["retorno"]
        r_b = metricas_curva(c_b)["retorno"]
        con_datos += 1
        if r_g > r_b:
            gana_grid += 1

    ratio_ventanas = (gana_grid / con_datos * 100) if con_datos > 0 else 0

    # Aprueba si grid gana en out-of-sample Y en la mayoria de ventanas
    aprueba = m_grid_out["retorno"] > m_bh_out["retorno"] and ratio_ventanas >= 55

    return {
        "simbolo": simbolo,
        "fuente": fuente,
        "velas": len(df),
        "ops": ops,
        "grid": m_grid,
        "bh": m_bh,
        "grid_out": m_grid_out,
        "bh_out": m_bh_out,
        "ventanas_ganadas": gana_grid,
        "ventanas_con_datos": con_datos,
        "ratio_ventanas": round(ratio_ventanas),
        "aprueba": aprueba,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Grid Trading Multi-Activo - {fecha}", ""]
    L.append(
        f"Grid: {N_GRIDS} niveles, recalculada cada ~45 dias segun rango de ~30 dias, "
        f"colchon {MARGEN_RANGO_PCT}%. Comisiones {COSTO_POR_LADO_PCT:.2f}% por operacion."
    )
    L.append(
        "Criterio de aprobacion: grid debe superar a comprar-y-mantener en el periodo "
        "out-of-sample Y ganar en al menos 55% de las ventanas rodantes."
    )
    L.append("")

    validos = [r for r in resultados if r.get("aprueba")]
    fallidos = [r for r in resultados if "error" not in r and not r.get("aprueba")]
    sin_datos = [r for r in resultados if "error" in r]

    L.append("=" * 50)
    L.append(f"RESULTADO: {len(validos)} activos donde GRID supera a buy&hold, de {len(resultados)} evaluados")
    L.append("=" * 50)
    L.append("")

    if validos:
        L.append(">>> ACTIVOS DONDE GRID TRADING SUPERA A COMPRAR-Y-MANTENER <<<")
        for r in sorted(validos, key=lambda x: x["grid_out"]["retorno"], reverse=True):
            L.append(f"{r['simbolo']} ({r['velas']} velas, {r['ops']} operaciones del grid, {r['fuente']}):")
            L.append(
                f"  Out-of-sample: grid {r['grid_out']['retorno']}% vs buy&hold {r['bh_out']['retorno']}%"
            )
            L.append(f"  Ventanas ganadas por grid: {r['ventanas_ganadas']}/{r['ventanas_con_datos']} ({r['ratio_ventanas']}%)")
            L.append(f"  Periodo completo: grid {r['grid']['retorno']}% vs buy&hold {r['bh']['retorno']}%")
            L.append("")
    else:
        L.append("Ningun activo mostro ventaja consistente de grid trading sobre comprar-y-mantener.")
        L.append("")

    L.append("--- Todos los activos (ordenados por que tan cerca estuvo grid de ganar) ---")
    for r in sorted(fallidos, key=lambda x: x["grid_out"]["retorno"] - x["bh_out"]["retorno"], reverse=True):
        dif = r["grid_out"]["retorno"] - r["bh_out"]["retorno"]
        L.append(
            f"- {r['simbolo']}: grid {r['grid_out']['retorno']}% vs buy&hold {r['bh_out']['retorno']}% "
            f"(diferencia {dif:+.1f}), ventanas {r['ratio_ventanas']}%"
        )

    if sin_datos:
        L.append("")
        L.append("--- Sin datos suficientes ---")
        for r in sin_datos:
            L.append(f"- {r['simbolo']}: {r['error']}")

    L.append("")
    L.append("=== CONCLUSION ===")
    if validos:
        L.append(
            "Se encontraron activos donde grid trading podria tener ventaja. Antes de "
            "usar con dinero real, someterlos a la validacion completa (comisiones ya "
            "incluidas aqui, pero falta prueba de suavidad de parametros y verificar que "
            "el comportamiento lateral se mantenga hacia adelante)."
        )
    else:
        L.append(
            "Ningun activo de los 28 mostro ventaja consistente de grid trading sobre "
            "simplemente comprar y mantener. La conclusion se sostiene: en 2021-2026, "
            "las criptomonedas grandes tuvieron tendencias demasiado fuertes (al alza o "
            "a la baja) para que grid trading (diseñado para mercados laterales) le "
            "gane a la alternativa simple de comprar y mantener."
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
    for i, simbolo in enumerate(ACTIVOS, 1):
        print(f"[{i}/{len(ACTIVOS)}] Evaluando {simbolo}...")
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
