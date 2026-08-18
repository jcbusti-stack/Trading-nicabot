"""
Backtesting - Marcos de Tiempo mas Rapidos (SOL) - Trading-nicabot

Prueba la MISMA estrategia validada (EMA 9/21/200 + RSI + MACD + stop 5%,
SIN cambiar ninguna condicion) en marcos de tiempo mas rapidos que las
12 horas actuales: 1h, 4h, y 12h como referencia.

Objetivo: responder si revisar el precio mas seguido encuentra mas señales
BUENAS, o solo mas señales (con mas comisiones y mas ruido).

Con comisiones reales incluidas SIEMPRE.

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

NOMBRE_ARCHIVO = 'backtesting_marcos_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

PAR = "SOL/USDT"
STOP_LOSS_PCT = 5.0
COSTO_POR_OPERACION_PCT = (0.26 + 0.05) * 2  # comision + deslizamiento, round-trip
EXCHANGES = ['binance', 'bybit', 'okx']
VENTANAS = 6

# Marcos a probar: nombre -> (timeframe de ccxt, velas objetivo a pedir)
# Se pide mas velas en marcos rapidos para cubrir un periodo de tiempo similar,
# pero los exchanges suelen limitar cuanto historial dan en 1h, asi que se
# consigue lo que se pueda.
MARCOS = {
    "1 hora":   ("1h", 4000),
    "4 horas":  ("4h", 4000),
    "12 horas (actual)": ("12h", 4000),
}


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


def obtener_historial(timeframe, objetivo):
    mejor, fuente = [], None
    for nombre in EXCHANGES:
        try:
            d = descargar_paginado(nombre, PAR, timeframe, objetivo)
            if len(d) > len(mejor):
                mejor, fuente = d, nombre
            if len(mejor) >= objetivo * 0.9:
                break
        except Exception as e:
            print(f"  {nombre} fallo en {timeframe}: {e}")
    if not mejor:
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
    """MISMA estrategia validada, sin cambiar ninguna condicion."""
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


def evaluar_marco(nombre_marco, timeframe, objetivo):
    print(f"Descargando {nombre_marco} ({timeframe})...")
    df_bruto, fuente = obtener_historial(timeframe, objetivo)
    if df_bruto is None or len(df_bruto) < 300:
        return {"marco": nombre_marco, "error": "sin datos suficientes"}

    df = calcular_indicadores(df_bruto)
    if len(df) < 250:
        return {"marco": nombre_marco, "error": f"solo {len(df)} velas utiles"}

    corte = int(len(df) * 0.70)
    m_in = metricas(simular(df.iloc[:corte]))
    m_out = metricas(simular(df.iloc[corte:]))

    tam = len(df) // VENTANAS
    gana = con_datos = 0
    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        ops_v = simular(df.iloc[ini:fin])
        if not ops_v:
            continue
        con_datos += 1
        if metricas(ops_v)["ret"] > 0:
            gana += 1

    dias_cubiertos = (df.index[-1] - df.index[0]).days

    return {
        "marco": nombre_marco,
        "timeframe": timeframe,
        "fuente": fuente,
        "velas": len(df),
        "dias": dias_cubiertos,
        "desde": df.index[0].strftime("%Y-%m-%d"),
        "hasta": df.index[-1].strftime("%Y-%m-%d"),
        "in": m_in,
        "out": m_out,
        "ventanas_ganadas": gana,
        "ventanas_con_datos": con_datos,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Marcos de Tiempo Mas Rapidos (SOL) - {fecha}", ""]
    L.append(
        "MISMA estrategia validada (EMA 9/21/200 + RSI + MACD + stop-loss 5%), "
        f"sin cambiar ninguna condicion. Comisiones reales incluidas ({COSTO_POR_OPERACION_PCT:.2f}% "
        "por operacion completa)."
    )
    L.append("")
    L.append(
        "AVISO IMPORTANTE: los exchanges limitan cuanto historial dan en marcos "
        "rapidos (1h suele tener menos dias de historia disponible que 12h). "
        "Compara los resultados solo si cubren un periodo de tiempo similar - "
        "revisa la columna 'dias cubiertos' de cada marco antes de sacar conclusiones."
    )
    L.append("")

    for r in resultados:
        if "error" in r:
            L.append(f"{r['marco']}: no se pudo evaluar ({r['error']})")
            L.append("")
            continue

        L.append(f"=== {r['marco']} ===")
        L.append(
            f"Datos: {r['velas']} velas ({r['desde']} a {r['hasta']}, "
            f"{r['dias']} dias cubiertos), fuente {r['fuente']}"
        )
        L.append(
            f"In-sample:  {r['in']['n']} ops, {r['in']['ret']}% retorno, "
            f"{r['in']['acierto']}% acierto, {r['in']['prom']}% promedio/op, dd {r['in']['dd']}%"
        )
        L.append(
            f"Out-of-sample: {r['out']['n']} ops, {r['out']['ret']}% retorno, "
            f"{r['out']['acierto']}% acierto, {r['out']['prom']}% promedio/op, dd {r['out']['dd']}%"
        )
        L.append(
            f"Ventanas ganadas: {r['ventanas_ganadas']}/{r['ventanas_con_datos']}"
        )
        L.append("")

    L.append("=== COMO LEER ESTO ===")
    L.append(
        "- Si un marco rapido tiene MUCHAS mas operaciones pero el 'promedio por "
        "operacion' es mucho menor, las comisiones se comen la ventaja - mas "
        "señales no es mejor si cada una vale menos."
    )
    L.append(
        "- El criterio para adoptar un marco distinto al de 12h: debe superar en "
        "retorno out-of-sample Y ganar en la mayoria de ventanas Y cubrir un "
        "periodo de tiempo comparable (no vale comparar 200 dias de 1h contra "
        "2000 dias de 12h)."
    )
    L.append(
        "- Si los marcos rapidos simplemente no tienen suficiente historial "
        "disponible en los exchanges publicos, la pregunta queda sin poder "
        "responderse con certeza - hay que decirlo con honestidad, no forzar "
        "una conclusion con datos insuficientes."
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
    for nombre, (timeframe, objetivo) in MARCOS.items():
        try:
            resultados.append(evaluar_marco(nombre, timeframe, objetivo))
        except Exception as e:
            resultados.append({"marco": nombre, "error": str(e)[:100]})

    reporte = generar_reporte(resultados)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
