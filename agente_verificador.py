"""
Agente Verificador Final - Trading-nicabot
Lee los reportes de los otros agentes (senal tecnica, sentimiento,
escaner de oportunidades) MAS el portafolio real de Kraken, y le hace
una revision cruzada con Claude, personalizada a las posiciones reales
de Juan Carlos.

Se ejecuta DESPUES de los otros scripts, para que ya existan los archivos.

Requiere: ANTHROPIC_API_KEY, GCP_SA_KEY (ya configurados)
"""

import io
import json
import os
import re
from datetime import datetime

import anthropic
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

NOMBRE_ARCHIVO = 'veredicto_final.txt'
NOMBRE_PORTFOLIO = 'portfolio.csv'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'
ANTHROPIC_API_KEY = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()

MODELO = 'claude-haiku-4-5-20251001'

cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def leer_archivo_local(nombre):
    """Lee un archivo si existe en el directorio de trabajo actual (mismo runner)."""
    try:
        with open(nombre, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ""


def obtener_credenciales_drive():
    match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
    info = json.loads(match.group(0))
    return Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive']
    )


def descargar_portfolio_desde_drive():
    """Descarga portfolio.csv desde Drive (lo actualiza main.py en otro workflow)."""
    if not GCP_SA_KEY:
        return ""
    try:
        creds = obtener_credenciales_drive()
        service = build('drive', 'v3', credentials=creds)

        query = f"name = '{NOMBRE_PORTFOLIO}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
        files = results.get('files', [])
        if not files:
            print('portfolio.csv no encontrado en Drive.')
            return ""

        request = service.files().get_media(fileId=files[0]['id'])
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return buf.getvalue().decode('utf-8')
    except Exception as e:
        print('No se pudo descargar portfolio.csv:', e)
        return ""


def resumir_portfolio(csv_texto):
    """Convierte el CSV en un resumen corto de posiciones reales, para el prompt."""
    if not csv_texto:
        return ""

    lineas = csv_texto.strip().split("\n")
    posiciones = []
    total = ""

    for linea in lineas[1:]:
        partes = linea.split(",")
        if len(partes) < 6:
            continue
        seccion, tipo, activo, cantidad, precio, valor_usd = partes[:6]
        if seccion == "balance" and tipo != "earn_flex_ya_incluido_en_spot":
            try:
                valor = float(valor_usd)
                posiciones.append((activo, tipo, valor))
            except ValueError:
                continue
        elif seccion == "total":
            total = valor_usd

    posiciones.sort(key=lambda x: x[2], reverse=True)
    resumen = [f"Valor total del portafolio: ${total}"]
    resumen.append("Posiciones (de mayor a menor):")
    for activo, tipo, valor in posiciones[:12]:
        resumen.append(f"- {activo} ({tipo}): ${valor:.2f}")

    return "\n".join(resumen)


def generar_veredicto(senal_tecnica, sentimiento, oportunidades, portfolio_resumen):
    prompt = f"""Eres el verificador final de un sistema de analisis de criptomonedas.
Tu trabajo es cruzar la informacion de varias fuentes independientes y dar un veredicto
honesto y PERSONALIZADO a las posiciones reales de Juan Carlos, sin inventar datos que
no esten aqui.

SEÑAL TECNICA DE SOL (unica con backtesting validado):
{senal_tecnica or "No disponible"}

SENTIMIENTO DE NOTICIAS (16 monedas, generado por IA):
{sentimiento or "No disponible"}

ESCANER DE OPORTUNIDADES (mismos indicadores tecnicos aplicados a otras monedas,
SIN backtesting propio, solo informativo):
{oportunidades or "No disponible"}

PORTAFOLIO REAL DE JUAN CARLOS EN KRAKEN:
{portfolio_resumen or "No disponible"}

Escribe un veredicto final en español, maximo 10 lineas, en formato de lista simple,
sin tablas, apto para ser leido por un lector de pantalla. Debe incluir:
1. Si la señal tecnica y el sentimiento de SOL coinciden o se contradicen.
2. Que posiciones REALES de su portafolio estan mas expuestas a riesgo segun el
   sentimiento negativo (menciona el activo y su valor en dolares si aplica).
3. Que posiciones reales tienen sentimiento positivo actualmente.
4. Un veredicto final claro: "mantenerse fuera", "vigilar de cerca", o similar.
   NUNCA digas "comprar" o "vender" de forma directa - solo da contexto para que
   Juan Carlos decida.
"""

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=500,
        messages=[{"role": "user", "content": prompt}],
    )

    return respuesta.content[0].text.strip()


def subir_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado, no se sube a Drive.')
        return
    try:
        creds = obtener_credenciales_drive()
        service = build('drive', 'v3', credentials=creds)

        with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
            f.write(texto)

        query = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
        results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
        files = results.get('files', [])

        media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/plain')
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')
        else:
            metadata = {'name': NOMBRE_ARCHIVO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
            service.files().create(body=metadata, media_body=media, fields='id').execute()
            print(f'{NOMBRE_ARCHIVO} creado en Drive.')
    except Exception as e:
        print('No se pudo subir el veredicto a Drive:', e)


def main():
    if not ANTHROPIC_API_KEY:
        print('ANTHROPIC_API_KEY no configurado. No se puede continuar.')
        return

    senal_tecnica = leer_archivo_local('senal_sol.txt')
    sentimiento = leer_archivo_local('sentimiento_multi_activo.txt')
    oportunidades = leer_archivo_local('oportunidades.txt')

    print("Descargando portafolio real desde Drive...")
    portfolio_csv = descargar_portfolio_desde_drive()
    portfolio_resumen = resumir_portfolio(portfolio_csv)

    print("Generando veredicto final (revision cruzada + portafolio real)...")
    veredicto = generar_veredicto(senal_tecnica, sentimiento, oportunidades, portfolio_resumen)

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    reporte = f"Veredicto Final - Revision Cruzada - {fecha}\n\n{veredicto}"

    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
