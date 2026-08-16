"""
Agente de Sentimiento con Claude (Anthropic API) - Trading-nicabot
Analiza noticias de todas las criptomonedas del portafolio de Juan Carlos
usando Claude Haiku (el modelo mas economico).

Requiere:
- Variable de entorno ANTHROPIC_API_KEY (secret en GitHub)
- Libreria 'anthropic' en requirements.txt
"""

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

import anthropic
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_SENTIMIENTO = 'sentimiento_multi_activo.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'
ANTHROPIC_API_KEY = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()

MODELO = 'claude-haiku-4-5-20251001'

# Lista de activos: los 5 principales + todas las criptos del portafolio de Juan Carlos
ACTIVOS = {
    "BTC": "Bitcoin BTC criptomoneda",
    "ETH": "Ethereum ETH criptomoneda",
    "SOL": "Solana SOL criptomoneda",
    "XRP": "XRP Ripple criptomoneda",
    "ONDO": "Ondo Finance ONDO criptomoneda",
    "ADA": "Cardano ADA criptomoneda",
    "BONK": "Bonk BONK criptomoneda",
    "ENA": "Ethena ENA criptomoneda",
    "KAS": "Kaspa KAS criptomoneda",
    "MNT": "Mantle MNT criptomoneda",
    "PEPE": "Pepe PEPE criptomoneda",
    "SHIB": "Shiba Inu SHIB criptomoneda",
    "WLFI": "World Liberty Financial WLFI criptomoneda",
    "DOGE": "Dogecoin DOGE criptomoneda",
    "SUI": "Sui SUI criptomoneda",
    "AVAX": "Avalanche AVAX criptomoneda",
}

cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# ----------------------------
# 1. Descargar noticias recientes (gratis, Google News RSS)
# ----------------------------

def obtener_noticias(query, idioma="es-419", pais="MX", max_noticias=10):
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
# 2. Agente analista por activo (llamada a Claude)
# ----------------------------

def analizar_activo_con_claude(simbolo, titulares):
    if not titulares:
        return {
            "simbolo": simbolo,
            "sentimiento": "Sin datos",
            "resumen": "No se encontraron noticias recientes.",
        }

    lista_titulares = "\n".join(f"- {t}" for t in titulares)

    prompt = f"""Eres un analista financiero especializado en criptomonedas.
Analiza estos titulares recientes sobre {simbolo} y responde SOLO en formato JSON,
sin texto adicional, con esta estructura exacta:

{{"sentimiento": "Alcista" o "Bajista" o "Neutral", "resumen": "una frase corta en español, maximo 20 palabras, explicando por que"}}

Titulares:
{lista_titulares}
"""

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )

    texto = respuesta.content[0].text.strip()

    try:
        datos = json.loads(texto)
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', texto, re.DOTALL)
        datos = json.loads(match.group(0)) if match else {
            "sentimiento": "Neutral", "resumen": "No se pudo interpretar el analisis."
        }

    return {
        "simbolo": simbolo,
        "sentimiento": datos.get("sentimiento", "Neutral"),
        "resumen": datos.get("resumen", ""),
    }


# ----------------------------
# 3. Agente orquestador (junta todo en un solo reporte)
# ----------------------------

def generar_reporte_final(resultados, senal_tecnica_texto=""):
    resumen_activos = "\n".join(
        f"- {r['simbolo']}: {r['sentimiento']} — {r['resumen']}" for r in resultados
    )

    prompt = f"""Eres el orquestador de un equipo de analisis de criptomonedas.
Recibiste estos analisis de sentimiento de noticias por activo:

{resumen_activos}

{"Ademas, esta es la senal tecnica actual del bot de SOL:" + chr(10) + senal_tecnica_texto if senal_tecnica_texto else ""}

Escribe un resumen ejecutivo corto en español (maximo 6 lineas), en formato de lista simple,
sin tablas, apto para ser leido por un lector de pantalla. Menciona si el sentimiento general
del mercado es alcista, bajista o mixto, cuales activos destacan mas (positivo o negativo),
y si SOL coincide o no con su señal tecnica.
"""

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=350,
        messages=[{"role": "user", "content": prompt}],
    )

    return respuesta.content[0].text.strip()


# ----------------------------
# 4. Subir a Google Drive (mismo patron que bot_paper.py)
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
# 5. Punto de entrada
# ----------------------------

def main():
    if not ANTHROPIC_API_KEY:
        print('ANTHROPIC_API_KEY no configurado. No se puede continuar.')
        return

    resultados = []
    for simbolo, query in ACTIVOS.items():
        print(f"Analizando {simbolo} con Claude...")
        titulares = obtener_noticias(query)
        analisis = analizar_activo_con_claude(simbolo, titulares)
        resultados.append(analisis)

    senal_tecnica_texto = ""
    try:
        with open("senal_sol.txt", "r", encoding="utf-8") as f:
            senal_tecnica_texto = f.read()
    except FileNotFoundError:
        pass

    print("Generando resumen ejecutivo (orquestador)...")
    resumen_ejecutivo = generar_reporte_final(resultados, senal_tecnica_texto)

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [f"Reporte de Sentimiento Multi-Activo (Claude AI) - {fecha}", ""]
    lineas.append("Resumen ejecutivo:")
    lineas.append(resumen_ejecutivo)
    lineas.append("")
    lineas.append("Detalle por activo:")
    for r in resultados:
        lineas.append(f"- {r['simbolo']}: {r['sentimiento']} — {r['resumen']}")

    reporte = "\n".join(lineas)

    print("")
    print(reporte)
    subir_sentimiento_a_drive(reporte)


if __name__ == "__main__":
    main()
