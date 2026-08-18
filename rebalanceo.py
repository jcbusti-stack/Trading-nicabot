"""
Agente de Rebalanceo - Trading-nicabot
Compara el portafolio real de Kraken contra los rangos objetivo definidos en
trading-profile-1.md, y avisa cuando alguna categoria se desvia mas de 5
puntos porcentuales de su rango (la regla fija de Juan Carlos).

NO ejecuta ninguna operacion - solo informa. Las decisiones son suyas.

Requiere: GCP_SA_KEY (ya configurado)
"""

import io
import json
import os
import re
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

NOMBRE_ARCHIVO = 'rebalanceo.txt'
NOMBRE_PORTFOLIO = 'portfolio.csv'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

# Tolerancia antes de avisar: regla fija del perfil de Juan Carlos
TOLERANCIA_PUNTOS = 5.0

# Categorias segun trading-profile-1.md (actualizado 14 agosto 2026)
CATEGORIAS = {
    "BTC + ETH": {
        "activos": {"BTC", "XBT", "ETH"},
        "min": 25.0,
        "max": 30.0,
    },
    "ONDO + RWAs": {
        "activos": {"ONDO"},
        "min": 15.0,
        "max": 20.0,
    },
    "Altcoins L1/L2": {
        "activos": {"KAS", "SOL", "SUI", "ADA", "XRP", "AVAX", "MNT", "ENA"},
        "min": 25.0,
        "max": 30.0,
    },
    "Memecoins": {
        "activos": {"PEPE", "SHIB", "DOGE", "BONK"},
        "min": 0.0,
        "max": 20.0,
    },
    "Efectivo / stablecoins": {
        "activos": {"USDC", "USDT", "USD", "ZUSD"},
        "min": 5.0,
        "max": 10.0,
    },
}

# Activos sin objetivo formal de porcentaje (compras especulativas puntuales)
SIN_OBJETIVO = {"WLFI"}


def obtener_credenciales_drive():
    match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
    return Credentials.from_service_account_info(
        json.loads(match.group(0)), scopes=['https://www.googleapis.com/auth/drive']
    )


def descargar_archivo_drive(service, nombre):
    query = f"name = '{nombre}' and '{FOLDER_ID}' in parents and trashed = false"
    files = service.files().list(q=query, fields='files(id)', pageSize=1).execute().get('files', [])
    if not files:
        print(f'{nombre} no encontrado en Drive.')
        return ""
    try:
        request = service.files().get_media(fileId=files[0]['id'])
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return buf.getvalue().decode('utf-8')
    except Exception as e:
        print(f'No se pudo descargar {nombre}:', e)
        return ""


def leer_posiciones(csv_texto):
    """Devuelve {activo: valor_usd_total} y el total de la cartera."""
    posiciones = {}
    total = 0.0

    for linea in csv_texto.strip().split("\n")[1:]:
        partes = linea.split(",")
        if len(partes) < 6:
            continue
        seccion, tipo, activo, cantidad, precio, valor_usd = partes[:6]

        if seccion == "balance":
            # Ignorar la fila de flex que ya viene contada en spot
            if tipo == "earn_flex_ya_incluido_en_spot":
                continue
            try:
                valor = float(valor_usd)
            except ValueError:
                continue
            activo = activo.upper().strip()
            posiciones[activo] = posiciones.get(activo, 0.0) + valor

        elif seccion == "total":
            try:
                total = float(valor_usd)
            except ValueError:
                pass

    if total <= 0:
        total = sum(posiciones.values())

    return posiciones, total


def analizar(posiciones, total):
    resultado = []
    activos_clasificados = set()

    for nombre, config in CATEGORIAS.items():
        valor = sum(v for a, v in posiciones.items() if a in config["activos"])
        activos_clasificados |= {a for a in posiciones if a in config["activos"]}
        pct = (valor / total * 100) if total > 0 else 0.0

        if pct < config["min"]:
            desvio = config["min"] - pct
            direccion = "POR DEBAJO"
        elif pct > config["max"]:
            desvio = pct - config["max"]
            direccion = "POR ENCIMA"
        else:
            desvio = 0.0
            direccion = "DENTRO"

        resultado.append({
            "categoria": nombre,
            "valor": valor,
            "pct": pct,
            "min": config["min"],
            "max": config["max"],
            "desvio": desvio,
            "direccion": direccion,
            "alerta": desvio > TOLERANCIA_PUNTOS,
        })

    # Lo que no cayo en ninguna categoria
    otros = {a: v for a, v in posiciones.items()
             if a not in activos_clasificados and v > 0}

    return resultado, otros


def generar_reporte(resultado, otros, total):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Rebalanceo - Portafolio vs Objetivo - {fecha}", ""]
    L.append(f"Valor total de la cartera cripto: ${total:,.2f}")
    L.append(
        f"Regla aplicada: avisar cuando una categoria se desvia mas de "
        f"{TOLERANCIA_PUNTOS:.0f} puntos porcentuales de su rango objetivo."
    )
    L.append("")

    alertas = [r for r in resultado if r["alerta"]]

    if alertas:
        L.append("*** ACCION SUGERIDA - CATEGORIAS FUERA DE RANGO ***")
        for r in alertas:
            accion = "reducir" if r["direccion"] == "POR ENCIMA" else "aumentar"
            L.append(
                f"- {r['categoria']}: {r['pct']:.1f}% "
                f"(objetivo {r['min']:.0f}-{r['max']:.0f}%), "
                f"{r['desvio']:.1f} puntos {r['direccion'].lower()} - considerar {accion}"
            )
        L.append("")
    else:
        L.append("Ninguna categoria supera la tolerancia. No hay accion urgente.")
        L.append("")

    L.append("Detalle de todas las categorias:")
    for r in resultado:
        estado = "ALERTA" if r["alerta"] else r["direccion"]
        L.append(
            f"- {r['categoria']}: {r['pct']:.1f}% (${r['valor']:,.2f}) | "
            f"objetivo {r['min']:.0f}-{r['max']:.0f}% | {estado}"
        )

    if otros:
        L.append("")
        L.append("Posiciones sin objetivo formal de porcentaje:")
        for activo, valor in sorted(otros.items(), key=lambda x: x[1], reverse=True):
            pct = (valor / total * 100) if total > 0 else 0.0
            nota = " (especulativa, mantener pequeña)" if activo in SIN_OBJETIVO else ""
            L.append(f"- {activo}: ${valor:,.2f} ({pct:.1f}%){nota}")

    L.append("")
    L.append(
        "IMPORTANTE: esto es solo informativo, calculado contra los rangos de "
        "trading-profile-1.md. No se ejecuta ninguna operacion automaticamente. "
        "Si un rango cambia, hay que actualizarlo en el perfil y en este script."
    )

    return "\n".join(L)


def subir_a_drive(service, texto):
    with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
        f.write(texto)

    query = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
    files = service.files().list(q=query, fields='files(id)', pageSize=1).execute().get('files', [])
    media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/plain')

    if files:
        service.files().update(fileId=files[0]['id'], media_body=media).execute()
        print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')
    else:
        meta = {'name': NOMBRE_ARCHIVO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
        service.files().create(body=meta, media_body=media, fields='id').execute()
        print(f'{NOMBRE_ARCHIVO} creado en Drive.')


def main():
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado. No se puede continuar.')
        return

    creds = obtener_credenciales_drive()
    service = build('drive', 'v3', credentials=creds)

    print("Descargando portafolio desde Drive...")
    csv_texto = descargar_archivo_drive(service, NOMBRE_PORTFOLIO)
    if not csv_texto:
        print("No se pudo leer el portafolio. Abortando.")
        return

    posiciones, total = leer_posiciones(csv_texto)
    resultado, otros = analizar(posiciones, total)
    reporte = generar_reporte(resultado, otros, total)

    print("")
    print(reporte)
    subir_a_drive(service, reporte)


if __name__ == "__main__":
    main()
