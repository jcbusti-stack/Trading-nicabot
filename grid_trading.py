"""
Backtesting - Grid Trading (SOL) - Trading-nicabot

Grid trading: divide un rango de precios en niveles ("cuadricula"). Compra
cuando el precio baja a un nivel, vende cuando sube al siguiente. Funciona
mejor en mercados LATERALES, no en tendencias fuertes (que es justo el
escenario de tu estrategia actual de SOL).

Como el precio de SOL se mueve mucho en 5 años, la cuadricula se RECALCULA
cada cierto tiempo (rolling grid) en vez de ser fija - si no, el precio se
sale del rango y el bot deja de operar por meses.

Comparacion correcta: grid trading vs simplemente COMPRAR Y MANTENER (buy
and hold) en el mismo periodo, con comisiones reales. Esa es la barra que
grid trading tiene que superar para tener sentido.

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

NOMBRE_ARCHIVO = 'grid_trading_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
TIMEFRAME = '12h'
VELAS_OBJETIVO = 4000
EXCHANGES = ['binance', 'bybit', 'okx']

CAPITAL_INICIAL = 1000
N_GRIDS = 10                 # niveles de precio dentro del rango
VELAS_LOOKBACK = 60          # ~30 dias, para calcular el rango de la cuadricula
VELAS_REBALANCEO = 90        # ~45 dias, cada cuanto se recalcula la cuadricula
MARGEN_RANGO_PCT = 5.0       # % de colchon arriba/abajo del min/max reciente

COSTO_POR_LADO_PCT = 0.26 + 0.05  # comision + deslizamiento, cada operacion (no round-trip)

VENTANAS = 6


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


def simular_grid(df):
    """
    Simula un grid bot con cuadricula que se recalcula cada VELAS_REBALANCEO.
    Devuelve: lista de valores de capital total (cash + posiciones a precio
    de mercado) en cada vela, y el numero de operaciones.
    """
    cash = CAPITAL_INICIAL
    holdings_por_nivel = None
    grid_levels = None
    capital_por_grid = None
    operaciones = 0
    curva_capital = []

    precio_anterior = None

    for i in range(len(df)):
        precio = df.iloc[i]['close']

        # Recalcular la cuadricula al inicio y cada VELAS_REBALANCEO velas
        necesita_recalculo = (
            grid_levels is None or i % VELAS_REBALANCEO == 0
        )
        if necesita_recalculo and i >= VELAS_LOOKBACK:
            # Liquidar posiciones abiertas al precio actual antes de recalcular
            if holdings_por_nivel:
                for qty in holdings_por_nivel.values():
                    if qty > 0:
                        cash += qty * precio * (1 - COSTO_POR_LADO_PCT / 100)
                        operaciones += 1

            ventana = df.iloc[max(0, i - VELAS_LOOKBACK):i + 1]
            minimo = ventana['low'].min() * (1 - MARGEN_RANGO_PCT / 100)
            maximo = ventana['high'].max() * (1 + MARGEN_RANGO_PCT / 100)

            grid_levels = [
                minimo + (maximo - minimo) * j / N_GRIDS for j in range(N_GRIDS + 1)
            ]
            holdings_por_nivel = {j: 0.0 for j in range(N_GRIDS)}
            capital_por_grid = cash / N_GRIDS if N_GRIDS > 0 else cash

        if grid_levels is not None and precio_anterior is not None:
            # Revisar cruces hacia ABAJO -> comprar en ese nivel
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

        # Valor total: cash + holdings a precio de mercado
        valor_holdings = sum(holdings_por_nivel.values()) * precio if holdings_por_nivel else 0.0
        curva_capital.append(cash + valor_holdings)
        precio_anterior = precio

    return curva_capital, operaciones


def simular_buy_and_hold(df):
    precio_inicial = df.iloc[0]['close']
    qty = (CAPITAL_INICIAL / precio_inicial) * (1 - COSTO_POR_LADO_PCT / 100)
    curva = [qty * df.iloc[i]['close'] for i in range(len(df))]
    return curva


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


def evaluar():
    df, fuente = obtener_historial()

    curva_grid, ops_grid = simular_grid(df)
    curva_bh = simular_buy_and_hold(df)

    m_grid = metricas_curva(curva_grid)
    m_bh = metricas_curva(curva_bh)

    # Fuera de muestra: ultimo 30% del periodo
    corte = int(len(df) * 0.70)
    m_grid_out = metricas_curva(curva_grid[corte:])
    m_bh_out = metricas_curva(curva_bh[corte:])

    # Ventanas rodantes: en cuantas gana grid vs buy-and-hold
    tam = len(df) // VENTANAS
    gana_grid = gana_bh = con_datos = 0
    ventanas_detalle = []
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
        gana = "grid" if r_g > r_b else "buy&hold"
        if r_g > r_b:
            gana_grid += 1
        else:
            gana_bh += 1
        ventanas_detalle.append((v + 1, trozo['timestamp'].iloc[0] if 'timestamp' in trozo else None, r_g, r_b, gana))

    meta = {
        "velas": len(df),
        "desde": df.index[0].strftime("%Y-%m-%d"),
        "hasta": df.index[-1].strftime("%Y-%m-%d"),
        "fuente": fuente,
        "ops_grid": ops_grid,
    }

    return m_grid, m_bh, m_grid_out, m_bh_out, gana_grid, gana_bh, con_datos, ventanas_detalle, meta


def generar_reporte(m_grid, m_bh, m_grid_out, m_bh_out, gana_grid, gana_bh, con_datos, ventanas, meta):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Grid Trading vs Buy&Hold (SOL) - {fecha}", ""]
    L.append(
        f"Datos: {meta['velas']} velas de 12h ({meta['desde']} a {meta['hasta']}), fuente {meta['fuente']}."
    )
    L.append(
        f"Grid: {N_GRIDS} niveles, recalculada cada ~{VELAS_REBALANCEO//2} dias segun el rango "
        f"de los ultimos ~{VELAS_LOOKBACK//2} dias (+/- {MARGEN_RANGO_PCT}% de colchon). "
        f"Comisiones: {COSTO_POR_LADO_PCT:.2f}% por cada operacion (no por round-trip)."
    )
    L.append(f"Total de operaciones del grid en todo el periodo: {meta['ops_grid']}")
    L.append("")

    L.append("=== PERIODO COMPLETO ===")
    L.append(f"Grid trading:     {m_grid['retorno']}% retorno, drawdown maximo {m_grid['dd']}%")
    L.append(f"Comprar y mantener: {m_bh['retorno']}% retorno, drawdown maximo {m_bh['dd']}%")
    diferencia = m_grid['retorno'] - m_bh['retorno']
    L.append(f"Diferencia: grid {'gana' if diferencia > 0 else 'pierde'} por {abs(diferencia):.1f} puntos")
    L.append("")

    L.append("=== FUERA DE MUESTRA (ultimo 30% del periodo) ===")
    L.append(f"Grid trading:     {m_grid_out['retorno']}% retorno")
    L.append(f"Comprar y mantener: {m_bh_out['retorno']}% retorno")
    L.append("")

    L.append("=== VENTANAS RODANTES ===")
    L.append(f"Grid gano en {gana_grid} de {con_datos} ventanas. Buy&hold gano en {gana_bh}.")
    for num, _, r_g, r_b, gana in ventanas:
        L.append(f"  Ventana {num}: grid {r_g}% vs buy&hold {r_b}% -> gana {gana}")
    L.append("")

    L.append("=== VEREDICTO ===")
    if gana_grid > con_datos * 0.55 and m_grid_out['retorno'] > m_bh_out['retorno']:
        L.append(
            "Grid trading superó a comprar-y-mantener en la mayoría de ventanas Y en "
            "el periodo out-of-sample. Candidato a explorar más, con la misma metodología "
            "de validación completa antes de usarlo con dinero real."
        )
    else:
        L.append(
            "Grid trading NO superó de forma consistente a simplemente comprar y "
            "mantener. Esto es común en criptomonedas con tendencias fuertes de largo "
            "plazo como SOL: el grid captura pequeños movimientos pero se pierde las "
            "subidas grandes, y paga muchas comisiones en el camino."
        )
    L.append("")
    L.append(
        "NOTA IMPORTANTE: grid trading suele funcionar mejor en pares que se mueven "
        "lateralmente por mucho tiempo (rango estable), no en activos con tendencias "
        "largas como ha tenido SOL. Podría valer la pena probarlo en una moneda con "
        "comportamiento más lateral, si se identifica alguna."
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
    m_grid, m_bh, m_grid_out, m_bh_out, gana_grid, gana_bh, con_datos, ventanas, meta = evaluar()
    reporte = generar_reporte(m_grid, m_bh, m_grid_out, m_bh_out, gana_grid, gana_bh, con_datos, ventanas, meta)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
