"""
Monitor de Canal de Telegram - Trading-nicabot

Revisa el canal "Trading Pockets VIP SEÑALES" en busca de mensajes nuevos
desde la ultima vez que corrio. Si un mensaje PARECE una señal de trading
(contiene palabras como TP1, SL, Apalancamiento, BUY, SELL), lo guarda en
un log para que Juan Carlos y Claude lo revisen juntos en el chat.

IMPORTANTE: este script NO interpreta ni registra automaticamente la señal
en el CSV de seguimiento - solo AVISA que llego un mensaje nuevo que parece
señal. El registro estructurado se sigue haciendo con Claude en el chat.

Requiere: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION, GCP_SA_KEY
"""

import io
import json
import os
import re
from datetime import datetime

from telethon import TelegramClient
from telethon.sessions import StringSession

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

API_ID = int(os.environ.get('TELEGRAM_API_ID', '0'))
API_HASH = os.environ.get('TELEGRAM_API_HASH', '').strip()
SESSION_STRING = os.environ.get('TELEGRAM_SESSION', '').strip()
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

# Busqueda flexible: coincide con cualquier dialogo cuyo titulo CONTENGA esto
# (no necesita ser exacto). Ajusta si el nombre real es distinto.
NOMBRE_CANAL_BUSQUEDA = "Trading Pockets"

NOMBRE_ESTADO = 'telegram_ultimo_id.txt'
NOMBRE_LOG = 'mensajes_telegram_nuevos.txt'

PALABRAS_CLAVE_SEÑAL = [
    # Señales de entrada (abrir operacion)
    'TP1', 'TP2', 'TP3', 'TP4', 'SL:', 'Apalancamiento', 'BUY', 'SELL',
    'SHORT', 'LONG', 'Precio de entrada',
    # Mensajes de cierre (avisan que una operacion ya termino)
    'SL en', 'Colocamos SL', 'cerramos', 'cerrado', 'cerrada',
    'liquidados', 'liquidada', '🏆', 'Take Profit', 'Stop Loss',
]


def obtener_credenciales_drive():
    match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
    info = json.loads(match.group(0))
    return Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive']
    )


def descargar_texto(service, nombre):
    query = f"name = '{nombre}' and '{FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
    files = results.get('files', [])
    if not files:
        return None, ""
    file_id = files[0]['id']
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return file_id, buf.getvalue().decode('utf-8')


def subir_texto(service, nombre, texto, file_id=None):
    with open(nombre, 'w', encoding='utf-8') as f:
        f.write(texto)
    media = MediaFileUpload(nombre, mimetype='text/plain')
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        metadata = {'name': nombre, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
        service.files().create(body=metadata, media_body=media, fields='id').execute()
    print(f'{nombre} actualizado en Drive.')


def parece_señal(texto):
    texto_upper = texto.upper()
    return any(palabra.upper() in texto_upper for palabra in PALABRAS_CLAVE_SEÑAL)


async def encontrar_canal(client, busqueda):
    """
    Busca entre TODOS los chats del usuario uno cuyo titulo contenga la
    palabra de busqueda (sin importar mayusculas/minusculas ni acentos).
    Esto funciona con titulos de grupos/canales, a diferencia de
    get_entity() que solo funciona con @usuarios exactos.
    """
    busqueda_normalizada = busqueda.lower()
    coincidencias = []

    async for dialogo in client.iter_dialogs():
        titulo = (dialogo.name or "").lower()
        if busqueda_normalizada in titulo:
            coincidencias.append(dialogo)

    if not coincidencias:
        print("No se encontro ningun chat que contenga esa palabra. Chats disponibles:")
        async for dialogo in client.iter_dialogs(limit=30):
            print(f"  - {dialogo.name}")
        return None

    if len(coincidencias) > 1:
        print(f"Se encontraron {len(coincidencias)} coincidencias, usando la primera:")
        for d in coincidencias:
            print(f"  - {d.name}")

    return coincidencias[0].entity


async def main():
    if not API_ID or not API_HASH or not SESSION_STRING:
        print('Faltan credenciales de Telegram. No se puede continuar.')
        return
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado.')
        return

    creds = obtener_credenciales_drive()
    service = build('drive', 'v3', credentials=creds)

    estado_id, estado_texto = descargar_texto(service, NOMBRE_ESTADO)
    ultimo_id_revisado = int(estado_texto.strip()) if estado_texto.strip().isdigit() else 0

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()

    canal = await encontrar_canal(client, NOMBRE_CANAL_BUSQUEDA)
    if canal is None:
        await client.disconnect()
        return

    mensajes_nuevos = []
    async for mensaje in client.iter_messages(canal, min_id=ultimo_id_revisado, limit=50):
        if mensaje.id <= ultimo_id_revisado:
            continue
        if mensaje.text:
            mensajes_nuevos.append(mensaje)

    mensajes_nuevos.sort(key=lambda m: m.id)

    if not mensajes_nuevos:
        print('Sin mensajes nuevos desde la ultima revision.')
        await client.disconnect()
        return

    max_id = max(m.id for m in mensajes_nuevos)
    señales_detectadas = [m for m in mensajes_nuevos if parece_señal(m.text)]

    print(f'{len(mensajes_nuevos)} mensajes nuevos, {len(señales_detectadas)} parecen señales.')

    if señales_detectadas:
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_id, log_previo = descargar_texto(service, NOMBRE_LOG)
        lineas = [log_previo] if log_previo else []
        lineas.append(f"\n=== Revision {fecha} ===")
        for m in señales_detectadas:
            hora_msg = m.date.strftime("%Y-%m-%d %H:%M UTC")
            lineas.append(f"\n--- Mensaje del {hora_msg} (id {m.id}) ---")
            lineas.append(m.text)

        texto_final = "\n".join(lineas)
        subir_texto(service, NOMBRE_LOG, texto_final, file_id=log_id)

    subir_texto(service, NOMBRE_ESTADO, str(max_id), file_id=estado_id)
    print(f'Ultimo ID revisado actualizado a {max_id}.')

    await client.disconnect()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
