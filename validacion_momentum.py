"""
Validacion Robusta - Quitar el filtro de momentum (EMA9 > EMA21)
Trading-nicabot

El backtesting de frecuencia sugirio que quitar el momentum mejora los
resultados de SOL. Antes de tocar el bot en vivo, esta prueba lo somete a
tres validaciones independientes:

  1. VENTANAS RODANTES: divide el historial en muchas ventanas consecutivas
     y cuenta en cuantas gana cada version. Una mejora real deberia ganar en
     la mayoria, no solo en una division afortunada.

  2. VARIOS MARCOS TEMPORALES: 8h, 12h y 24h. Si el efecto solo aparece en
     12h, probablemente es casualidad de ese marco.

  3. OTRAS MONEDAS: BTC y ETH. Si quitar el momentum es una mejora genuina
     del metodo, deberia notarse tambien en otros activos.

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

NOMBRE_ARCHIVO = 'validacion_momentum_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

VELAS_OBJETIVO = 4000
STOP_LOSS_PCT = 5.0
EXCHANGES = ['binance', 'bybit', 'kraken', 'okx']

VENTANAS = 8  # cuantas ventanas independientes para la prueba rodante


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
        for v in lote:
            velas[v[0]] = v
        siguiente = lote[-1][0] + ms_por_vela
        if siguiente <= desde:
            break
        desde = siguiente
        time.sleep(exchange.rateLimit / 1000)

    return [velas[k] for k in sorted(velas)]


def obtener_historial(par, timeframe, objetivo=VELAS_OBJETIVO):
    mejor, nombre_mejor = [], None
    for nombre in EXCHANGES:
        try:
            datos = descargar_paginado(nombre, par, timeframe, objetivo)
            if len(datos) > len(mejor):
                mejor, nombre_mejor = datos, nombre
            if len(mejor) >= objetivo * 0.9:
                break
        except Exception:
            continue

    if not mejor:
        return None, None

    df = pd.DataFrame(mejor, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    return df.set_index('timestamp'), nombre_mejor


def calcular_indicadores(df):
    df = df.copy()
    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
    df['EMA_200'] = df['close'].ewm(span=200, adjust=False).mean()
    delta = df['close'].diff()
    g = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    p = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['RSI'] = 100 - (100 / (1 + g / p))
    e12 = df['close'].ewm(span=12, adjust=False).mean()
    e26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = e12 - e26
    df['MACD_signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df.dropna()


def simular(df, exigir_momentum):
    en_pos = False
    entrada = None
    stop = None
    ops = []

    for i in range(len(df)):
        f = df.iloc[i]
        c_tend = f['close'] > f['EMA_200'] * 1.015
        c_rsi = f['RSI'] < 65
        c_macd = f['MACD'] > f['MACD_signal']
        c_mom = (f['EMA_9'] > f['EMA_21']) if exigir_momentum else True

        senal_entrada = c_tend and c_rsi and c_macd and c_mom
        senal_salida = (f['MACD'] < f['MACD_signal']) or (f['RSI'] > 75)

        if not en_pos:
            if senal_entrada:
                en_pos = True
                entrada = f['close']
                stop = entrada * (1 - STOP_LOSS_PCT / 100)
        else:
            if f['low'] <= stop:
                ops.append((stop - entrada) / entrada * 100)
                en_pos = False
            elif senal_salida:
                ops.append((f['close'] - entrada) / entrada * 100)
                en_pos = False

    return ops


def retorno_compuesto(ops):
    if not ops:
        return 0.0
    cap = 1000
    for o in ops:
        cap *= (1 + o / 100)
    return (cap - 1000) / 1000 * 100


def resumen(ops):
    if not ops:
        return {"n": 0, "ret": 0.0, "acierto": 0.0, "prom": 0.0}
    gan = [o for o in ops if o > 0]
    return {
        "n": len(ops),
        "ret": round(retorno_compuesto(ops), 1),
        "acierto": round(len(gan) / len(ops) * 100, 1),
        "prom": round(sum(ops) / len(ops), 2),
    }


# ---------- PRUEBA 1: ventanas rodantes ----------

def prueba_ventanas(df):
    tam = len(df) // VENTANAS
    filas = []
    gana_sin_mom = 0
    gana_actual = 0
    empates = 0

    for v in range(VENTANAS):
        ini = v * tam
        fin = (v + 1) * tam if v < VENTANAS - 1 else len(df)
        trozo = df.iloc[ini:fin]
        if len(trozo) < 50:
            continue

        con = resumen(simular(trozo, True))
        sin = resumen(simular(trozo, False))

        if con["n"] == 0 and sin["n"] == 0:
            veredicto = "sin operaciones"
            empates += 1
        elif sin["ret"] > con["ret"] + 0.5:
            veredicto = "gana SIN momentum"
            gana_sin_mom += 1
        elif con["ret"] > sin["ret"] + 0.5:
            veredicto = "gana CON momentum (actual)"
            gana_actual += 1
        else:
            veredicto = "empate"
            empates += 1

        filas.append({
            "ventana": v + 1,
            "desde": trozo.index[0].strftime("%Y-%m"),
            "hasta": trozo.index[-1].strftime("%Y-%m"),
            "con": con,
            "sin": sin,
            "veredicto": veredicto,
        })

    return filas, gana_sin_mom, gana_actual, empates


# ---------- PRUEBA 2 y 3 ----------

def prueba_simple(par, timeframe):
    df_bruto, fuente = obtener_historial(par, timeframe)
    if df_bruto is None or len(df_bruto) < 300:
        return None
    df = calcular_indicadores(df_bruto)
    if len(df) < 250:
        return None
    return {
        "par": par,
        "timeframe": timeframe,
        "velas": len(df),
        "fuente": fuente,
        "con": resumen(simular(df, True)),
        "sin": resumen(simular(df, False)),
    }


def generar_reporte(ventanas, g_sin, g_con, emp, marcos, monedas, meta):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Validacion Robusta - Quitar filtro de momentum - {fecha}", ""]
    L.append(
        f"Hipotesis a validar: quitar la condicion EMA9 > EMA21 mejora la "
        f"estrategia de SOL. Datos base: {meta['velas']} velas de 12h "
        f"({meta['desde']} a {meta['hasta']}), fuente {meta['fuente']}."
    )
    L.append("")

    # --- Prueba 1 ---
    L.append("=== PRUEBA 1: VENTANAS RODANTES (la mas importante) ===")
    L.append(
        "Se divide todo el historial en ventanas consecutivas independientes. "
        "Una mejora real deberia ganar en la mayoria de ventanas."
    )
    L.append("")
    L.append(f"MARCADOR: gana SIN momentum en {g_sin} ventanas | "
             f"gana la version ACTUAL en {g_con} ventanas | empates o sin datos: {emp}")
    L.append("")
    for f in ventanas:
        L.append(f"Ventana {f['ventana']} ({f['desde']} a {f['hasta']}): {f['veredicto']}")
        L.append(
            f"  CON momentum (actual): {f['con']['n']} ops, {f['con']['ret']}% retorno, "
            f"{f['con']['acierto']}% acierto"
        )
        L.append(
            f"  SIN momentum:          {f['sin']['n']} ops, {f['sin']['ret']}% retorno, "
            f"{f['sin']['acierto']}% acierto"
        )
    L.append("")

    # --- Prueba 2 ---
    L.append("=== PRUEBA 2: OTROS MARCOS TEMPORALES (SOL) ===")
    L.append(
        "Si el efecto solo aparece en 12h, probablemente es casualidad de ese marco."
    )
    L.append("")
    for r in marcos:
        if r is None:
            continue
        dif = r['sin']['ret'] - r['con']['ret']
        signo = "MEJOR" if dif > 0 else "peor"
        L.append(f"SOL en {r['timeframe']} ({r['velas']} velas, {r['fuente']}): SIN momentum sale {signo}")
        L.append(f"  CON momentum: {r['con']['n']} ops, {r['con']['ret']}% retorno, {r['con']['prom']}% promedio")
        L.append(f"  SIN momentum: {r['sin']['n']} ops, {r['sin']['ret']}% retorno, {r['sin']['prom']}% promedio")
    L.append("")

    # --- Prueba 3 ---
    L.append("=== PRUEBA 3: OTRAS MONEDAS (velas de 12h) ===")
    L.append(
        "Si quitar el momentum es una mejora genuina del metodo, deberia notarse "
        "tambien en otros activos. AVISO: estas monedas no tienen estrategia "
        "validada propia, esto es solo para ver si el efecto se repite."
    )
    L.append("")
    for r in monedas:
        if r is None:
            continue
        dif = r['sin']['ret'] - r['con']['ret']
        signo = "MEJOR" if dif > 0 else "peor"
        L.append(f"{r['par']} ({r['velas']} velas): SIN momentum sale {signo}")
        L.append(f"  CON momentum: {r['con']['n']} ops, {r['con']['ret']}% retorno, {r['con']['prom']}% promedio")
        L.append(f"  SIN momentum: {r['sin']['n']} ops, {r['sin']['ret']}% retorno, {r['sin']['prom']}% promedio")
    L.append("")

    # --- Conclusion automatica ---
    L.append("=== COMO DECIDIR ===")
    L.append(
        "ADOPTAR el cambio solo si: gana en la mayoria de ventanas rodantes, "
        "Y mejora en al menos 2 de los 3 marcos temporales, Y se repite en "
        "al menos una de las otras monedas."
    )
    L.append(
        "NO ADOPTAR si gana solo en algunas ventanas sueltas o solo en 12h: "
        "eso indica que la mejora fue suerte de la division 70/30 original, "
        "no una ventaja real del metodo."
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
    print("Descargando base (SOL 12h)...")
    df_bruto, fuente = obtener_historial("SOL/USDT", "12h")
    if df_bruto is None:
        print("No se pudo descargar el historial base.")
        return
    df = calcular_indicadores(df_bruto)

    meta = {
        "velas": len(df),
        "desde": df.index[0].strftime("%Y-%m-%d"),
        "hasta": df.index[-1].strftime("%Y-%m-%d"),
        "fuente": fuente,
    }

    print("Prueba 1: ventanas rodantes...")
    ventanas, g_sin, g_con, emp = prueba_ventanas(df)

    print("Prueba 2: otros marcos temporales...")
    marcos = []
    for tf in ["8h", "12h", "1d"]:
        print(f"  {tf}...")
        marcos.append(prueba_simple("SOL/USDT", tf))

    print("Prueba 3: otras monedas...")
    monedas = []
    for par in ["BTC/USDT", "ETH/USDT"]:
        print(f"  {par}...")
        monedas.append(prueba_simple(par, "12h"))

    reporte = generar_reporte(ventanas, g_sin, g_con, emp, marcos, monedas, meta)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
