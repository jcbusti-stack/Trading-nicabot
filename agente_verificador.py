"""
Agente Verificador Final - Trading-nicabot
Lee los reportes de los otros 3 agentes (senal tecnica, sentimiento,
escaner de oportunidades) y les hace una revision cruzada con Claude,
senalando coincidencias, contradicciones, y un veredicto final claro.

Se ejecuta DESPUES de los otros 3 scripts, para que ya existan los archivos.

Requiere: ANTHROPIC_API_KEY, GCP_SA_KEY (ya configurados)
"""

import json
import os
import re
from datetime import datetime

import anthropic
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.http import MediaIoBaseDownload
import io

NOMBRE_ARCHIVO = 'veredicto_final.txt'
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


def generar_veredicto(senal_tecnica, sentimiento, oportunidades):
    prompt = f"""Eres el verificador final de un sistema de analisis de criptomonedas.
Tu trabajo es cruzar la informacion de 3 fuentes independientes y dar un veredicto
honesto, sin inventar datos que no esten aqui.

SEÑAL TECNICA DE SOL (unica con backtesting validado):
{senal_tecnica or "No disponible"}

SENTIMIENTO DE NOTICIAS (16 monedas, generado por IA):
{sentimiento or "No disponible"}

ESCANER DE OPORTUNIDADES (mismos indicadores tecnicos aplicados a otras monedas,
SIN backtesting propio, solo informativo):
{oportunidades or "No disponible"}

Escribe un veredicto final en español, maximo 8 lineas, en formato de lista simple,
apto para lector de pantalla (sin tablas). Debe incluir:
1. Si la señal tecnica y el sentimiento de SOL coinciden o se contradicen.
2. Si el escaner de oportunidades encontro algo que merezca atencion, aclarando
   siempre que no tiene backtesting propio.
3. Un veredicto final claro: "mantenerse fuera", "vigilar de cerca", o similar.
   NUNCA digas "comprar" o "vender" de forma directa - solo da contexto para que
   Juan Carlos decida.
"""

    respuesta = cliente.messages.create(
        model=MODELO,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    return respuesta.content[0].text.strip()


def subir_a_drive(texto):
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

    print("Generando veredicto final (revision cruzada)...")
    veredicto = generar_veredicto(senal_tecnica, sentimiento, oportunidades)

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    reporte = f"Veredicto Final - Revision Cruzada - {fecha}\n\n{veredicto}"

    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
