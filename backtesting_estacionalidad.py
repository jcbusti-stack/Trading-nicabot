"""
Backtesting - Estacionalidad Agosto-Septiembre-Octubre - Trading-nicabot

Verifica con datos reales la creencia comun de que agosto-septiembre son
meses debiles en cripto y octubre suele recuperar. Se calcula para BTC, ETH
y SOL, usando TODO el historial diario disponible en cada exchange (no solo
los 5.5 años de SOL, para tener mas muestra en BTC/ETH que llevan mas tiempo).

Herramienta MANUAL. Es informativo - no cambia nada del bot en vivo, y NO
se convierte en filtro de entrada sin la misma validacion rigurosa de las
demas ideas.
"""

import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime

import ccxt
import pandas as pd

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_ARCHIVO = 'estacionalidad_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

EXCHANGES = ['binance', 'bybit', 'okx']
TIMEFRAME = '1d'
VELAS_OBJETIVO = 4000  # ~11 años en velas diarias, para agarrar todo lo disponible

ACTIVOS = [
    "BTC", "ETH", "SOL", "XRP", "ONDO", "ADA", "BONK", "ENA", "KAS", "MNT",
    "PEPE", "SHIB", "WLFI", "DOGE", "SUI", "AVAX",
]

NOMBRES_MES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
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


def obtener_historial(simbolo):
    par = f"{simbolo}/USDT"
    mejor, fuente = [], None
    for nombre in EXCHANGES:
        try:
            d = descargar_paginado(nombre, par, TIMEFRAME, VELAS_OBJETIVO)
            if len(d) > len(mejor):
                mejor, fuente = d, nombre
        except Exception:
            continue
    if len(mejor) < 60:
        return None, None
    df = pd.DataFrame(mejor, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df = df.set_index('timestamp')
    return df, fuente


def retornos_por_mes(df):
    """Devuelve {(año, mes): retorno_pct} usando el cierre del primer y
    ultimo dia disponible de cada mes calendario."""
    df = df.copy()
    df['año'] = df.index.year
    df['mes'] = df.index.month

    resultados = {}
    for (a, m), grupo in df.groupby(['año', 'mes']):
        grupo = grupo.sort_index()
        if len(grupo) < 5:  # mes incompleto, no confiable (ej. mes actual)
            continue
        precio_inicio = grupo['close'].iloc[0]
        precio_fin = grupo['close'].iloc[-1]
        retorno = (precio_fin - precio_inicio) / precio_inicio * 100
        resultados[(a, m)] = retorno

    return resultados


def resumen_por_mes_calendario(retornos_por_año_mes):
    """Agrupa por mes (1-12) a traves de todos los años disponibles."""
    por_mes = defaultdict(list)
    for (a, m), r in retornos_por_año_mes.items():
        por_mes[m].append(r)

    resumen = {}
    for m in range(1, 13):
        valores = por_mes.get(m, [])
        if not valores:
            resumen[m] = None
            continue
        positivos = sum(1 for v in valores if v > 0)
        resumen[m] = {
            "promedio": round(sum(valores) / len(valores), 1),
            "mediana": round(sorted(valores)[len(valores) // 2], 1),
            "n_años": len(valores),
            "pct_positivos": round(positivos / len(valores) * 100),
        }
    return resumen


def evaluar_activo(simbolo):
    df, fuente = obtener_historial(simbolo)
    if df is None:
        return {"simbolo": simbolo, "error": "sin datos suficientes"}

    retornos = retornos_por_mes(df)
    resumen = resumen_por_mes_calendario(retornos)

    return {
        "simbolo": simbolo,
        "fuente": fuente,
        "desde": df.index[0].strftime("%Y-%m"),
        "hasta": df.index[-1].strftime("%Y-%m"),
        "resumen": resumen,
    }


def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Backtesting - Estacionalidad Ago/Sep/Oct - {fecha}", ""]
    L.append(
        "Verifica con datos reales la creencia de que agosto-septiembre son "
        "debiles en cripto y octubre recupera. Retorno de cada mes calculado "
        "del primer al ultimo dia con datos disponibles ese mes."
    )
    L.append("")

    for r in resultados:
        if "error" in r:
            L.append(f"{r['simbolo']}: no se pudo evaluar ({r['error']})")
            L.append("")
            continue

        L.append(f"=== {r['simbolo']} (datos desde {r['desde']} hasta {r['hasta']}, fuente {r['fuente']}) ===")
        for m in range(1, 13):
            datos = r['resumen'][m]
            marca = ""
            if m in (8, 9, 10):
                marca = "  <-- " + ("AGO" if m == 8 else "SEP" if m == 9 else "OCT")
            if datos is None:
                L.append(f"  {NOMBRES_MES[m]}: sin datos suficientes{marca}")
            else:
                L.append(
                    f"  {NOMBRES_MES[m]}: promedio {datos['promedio']:+.1f}%, "
                    f"mediana {datos['mediana']:+.1f}%, positivo en {datos['pct_positivos']}% "
                    f"de {datos['n_años']} años{marca}"
                )
        L.append("")

    L.append("=== COMPARACION DIRECTA: AGOSTO/SEPTIEMBRE vs OCTUBRE ===")
    L.append("")
    confirma = 0
    contradice = 0
    sin_datos = 0
    for r in resultados:
        if "error" in r:
            sin_datos += 1
            continue
        ago = r['resumen'][8]
        sep = r['resumen'][9]
        oct_ = r['resumen'][10]
        if ago and sep and oct_:
            promedio_ago_sep = (ago['promedio'] + sep['promedio']) / 2
            L.append(
                f"{r['simbolo']}: Ago+Sep promedio {promedio_ago_sep:+.1f}% "
                f"vs Octubre {oct_['promedio']:+.1f}%"
            )
            if oct_['promedio'] > promedio_ago_sep:
                L.append(f"  -> Octubre SI fue mejor en promedio, con {oct_['n_años']} años de muestra")
                confirma += 1
            else:
                L.append(f"  -> Octubre NO fue mejor en promedio, con {oct_['n_años']} años de muestra")
                contradice += 1
        else:
            L.append(f"{r['simbolo']}: datos insuficientes para comparar")
            sin_datos += 1
        L.append("")

    total_evaluadas = confirma + contradice
    L.append("=== RESUMEN GENERAL DE LAS 16 MONEDAS ===")
    if total_evaluadas > 0:
        L.append(
            f"Octubre fue mejor que Ago+Sep en {confirma} de {total_evaluadas} monedas "
            f"con datos suficientes ({round(confirma/total_evaluadas*100)}%). "
            f"{sin_datos} moneda(s) sin historial suficiente para evaluar."
        )
    else:
        L.append("No hubo suficientes monedas con datos para sacar un resumen.")
    L.append("")

    L.append("=== AVISO HONESTO SOBRE ESTA PRUEBA ===")
    L.append(
        "- La muestra es PEQUEÑA: cripto solo tiene ~10-15 años de historia, "
        "asi que cada mes calendario se calcula con muy pocos años (a veces "
        "3-11). Un patron con tan pocos datos puede ser coincidencia."
    )
    L.append(
        "- Esto NO paso por walk-forward, ni por prueba en multiples activos "
        "de forma robusta, ni se descontaron comisiones (no aplica igual "
        "porque no es una estrategia de entrada/salida, es solo un patron "
        "de retorno mensual)."
    )
    L.append(
        "- Aunque el patron exista en el promedio historico, NO garantiza "
        "que se repita este año - los mercados no funcionan como un "
        "calendario fijo."
    )
    L.append(
        "- Esto es informacion para tener contexto, NO una señal para "
        "comprar o vender. No cambia el veredicto del sistema, que se basa "
        "en indicadores tecnicos y sentimiento actual, no en el mes del año."
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
