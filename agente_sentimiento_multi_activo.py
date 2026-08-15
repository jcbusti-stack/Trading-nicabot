"""
Agente de Sentimiento Multi-Activo - Trading-nicabot
Analiza noticias recientes de BTC, ETH, SOL, XRP, BNB y ONDO,
y genera un puntaje de sentimiento por cada uno.

No requiere API key de pago: usa el feed RSS público de Google News.
"""

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_SENTIMIENTO = 'sentimiento_multi_activo.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

# ----------------------------
# 1. Activos a monitorear
# ----------------------------

ACTIVOS = {
    "BTC": "Bitcoin BTC criptomoneda",
    "ETH": "Ethereum ETH criptomoneda",
    "SOL": "Solana SOL criptomoneda",
    "XRP": "XRP Ripple criptomoneda",
    "BNB": "BNB Binance Coin criptomoneda",
    "ONDO": "Ondo Finance ONDO criptomoneda",
}


# ----------------------------
# 2. Descargar noticias recientes
# ----------------------------

def obtener_noticias(query, idioma="es-419", pais="MX", max_noticias=12):
    """Descarga titulares recientes desde Google News RSS. No requiere API key."""
    query_url = query.replace(" ", "+")
    url = (
        f"https://news.google.com/rss/search?q={query_url}"
        f"&hl={idioma}&gl={pais}&ceid={pais}:{idioma}"
    )
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read()
    except Exception as error:
        print(f"Aviso: no se pudieron descargar noticias para '{query}' ({error})")
        return []

    root = ET.fromstring(data)
    titulares = []
    for item in root.findall(".//item")[:max_noticias]:
        titulo = item.find("title").text if item.find("title") is not None else ""
        titulares.append(titulo)
    return titulares


# ----------------------------
# 3. Analizar sentimiento (basado en palabras clave)
# ----------------------------

PALABRAS_POSITIVAS = [
    "alcista", "sube", "subida", "rally", "ganancia", "récord", "recuperación",
    "compra", "acumulación", "aprobación", "impulso", "optimismo", "crece",
    "bullish", "surge", "soars", "gains", "adoption", "breakthrough",
    "institutional", "partnership", "upgrade", "positive"
]

PALABRAS_NEGATIVAS = [
    "bajista", "cae", "caída", "crash", "pérdida", "venta masiva", "liquidación",
    "hackeo", "estafa", "regulación estricta", "prohibición", "temor", "riesgo",
    "bearish", "drops", "plunge", "sell-off", "hack", "scam", "ban",
    "lawsuit", "investigation", "negative", "warning"
]


def analizar_sentimiento(titulares):
    puntaje = 0
    for titulo in titulares:
        texto = titulo.lower()
        positivos = sum(1 for palabra in PALABRAS_POSITIVAS if palabra in texto)
        negativos = sum(1 for palabra in PALABRAS_NEGATIVAS if palabra in texto)
        puntaje += (positivos - negativos)

    if puntaje > 1:
        resumen = "Alcista"
    elif puntaje < -1:
        resumen = "Bajista"
    else:
        resumen = "Neutral"

    return {"puntaje": puntaje, "resumen": resumen, "titulares": titulares}


# ----------------------------
# 4. Analizar todos los activos
# ----------------------------

def analizar_todos_los_activos():
    resultados = {}
    for simbolo, query in ACTIVOS.items():
        print(f"Analizando {simbolo}...")
        noticias = obtener_noticias(query)
        resultados[simbolo] = analizar_sentimiento(noticias)
    return resultados


# ----------------------------
# 5. Generar reporte final (formato lista, apto para TalkMan)
# ----------------------------

def generar_reporte(resultados):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = []
    lineas.append(f"Reporte de Sentimiento Multi-Activo - {fecha}")
    lineas.append("")

    for simbolo, datos in resultados.items():
        lineas.append(f"{simbolo}: sentimiento {datos['resumen']}, puntaje {datos['puntaje']}.")

    lineas.append("")
    lineas.append("Detalle de titulares por activo:")
    for simbolo, datos in resultados.items():
        lineas.append("")
        lineas.append(f"--- {simbolo} ---")
        if datos["titulares"]:
            for titulo in datos["titulares"][:5]:
                lineas.append(f"- {titulo}")
        else:
            lineas.append("- Sin noticias recientes disponibles.")

    lineas.append("")
    lineas.append(
        "Nota: el sentimiento de noticias es informativo. "
        "Las decisiones de entrada/salida del bot de SOL siguen dependiendo "
        "únicamente de la estrategia técnica EMA/RSI/MACD ya validada."
    )

    return "\n".join(lineas)


# ----------------------------
# 6. Subir reporte a Google Drive (mismo patrón que bot_paper.py)
# ----------------------------

def subir_sentimiento_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado, no se sube a Drive.')
        return
    try:
        match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
        info = json.loads(match.group(0))
        creds = Credentials.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)

        with open(NOMBRE_SENTIMIENTO, 'w', encoding='utf-8') as f:
            f.write(texto)

        query = f"name = '{NOMBRE_SENTIMIENTO}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
        files = results.get('files', [])

        media = MediaFileUpload(NOMBRE_SENTIMIENTO, mimetype='text/plain')
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f'{NOMBRE_SENTIMIENTO} actualizado en Drive.')
        else:
            metadata = {'name': NOMBRE_SENTIMIENTO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
            service.files().create(body=metadata, media_body=media, fields='id').execute()
            print(f'{NOMBRE_SENTIMIENTO} creado en Drive.')
    except Exception as e:
        print('No se pudo subir el sentimiento a Drive:', e)


# ----------------------------
# 7. Punto de entrada
# ----------------------------

def main():
    resultados = analizar_todos_los_activos()
    reporte = generar_reporte(resultados)

    print("")
    print("Listo. Subiendo reporte a Drive...")
    print("")
    print(reporte)

    subir_sentimiento_a_drive(reporte)


if __name__ == "__main__":
    main()
