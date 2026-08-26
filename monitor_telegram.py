"""
Monitor de Canal de Telegram - Trading-nicabot

Revisa TODOS los chats de Telegram cuyo nombre contenga "Trading Pockets"
(pueden ser mas de uno - se detecto que existen al menos dos grupos
distintos con nombres parecidos, ej. "Trading Pockets" y "Trading Pockets
VIP SEÑALES"). Dentro de cada uno, si tiene "Temas" (Forum Topics)
activados, busca el tema que contenga "SEÑALES".

Guarda un ultimo-id-revisado POR CADA chat+tema por separado (no uno solo
global), para no perder mensajes cuando hay varias fuentes con sus propios
numeros de mensaje independientes.

Si un mensaje PARECE una señal de trading (TP1, SL, Apalancamiento, BUY,
SELL, o avisos de cierre), lo guarda en un log para que Juan Carlos y
Claude lo revisen juntos en el chat.

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
from telethon.tl.functions.messages import GetForumTopicsRequest

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

API_ID = int(os.environ.get('TELEGRAM_API_ID', '0'))
API_HASH = os.environ.get('TELEGRAM_API_HASH', '').strip()
SESSION_STRING = os.environ.get('TELEGRAM_SESSION', '').strip()
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

# Busqueda flexible del GRUPO - solo se revisa el que coincida con esto.
# Se uso "VIP" ademas de "Trading Pockets" para evitar confundirlo con el
# grupo gratuito parecido ("Trading Pockets 📈"), que a veces publica
# señales similares pero no siempre iguales - causo un error de registro
# el 26 de agosto de 2026 al mezclarlos.
NOMBRE_CANAL_BUSQUEDA = "Trading Pockets VIP"

# Busqueda flexible del TEMA de señales dentro de cada grupo (si tiene Temas)
NOMBRE_TEMA_BUSQUEDA = "SEÑALES"

NOMBRE_ESTADO = 'telegram_ultimo_id.txt'
NOMBRE_LOG = 'mensajes_telegram_nuevos.txt'

PALABRAS_CLAVE_SEÑAL = [
    'TP1', 'TP2', 'TP3', 'TP4', 'SL:', 'Apalancamiento', 'BUY', 'SELL',
    'SHORT', 'LONG', 'Precio de entrada',
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


def cargar_estado(texto):
    """El estado ahora es un JSON: {"clave_fuente": ultimo_id, ...}
    Compatible con el formato viejo (un solo numero) si aun no se ha
    actualizado - en ese caso se descarta y se empieza de cero por fuente."""
    texto = texto.strip()
    if not texto:
        return {}
    try:
        datos = json.loads(texto)
        if isinstance(datos, dict):
            return datos
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


async def encontrar_canales(client, busqueda):
    """Devuelve TODOS los chats cuyo nombre contenga la busqueda, no solo
    el primero - puede haber varios grupos con nombres parecidos."""
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
        return []

    print(f"Se encontraron {len(coincidencias)} chat(s) que coinciden:")
    for d in coincidencias:
        print(f"  - {d.name}")

    return coincidencias


async def encontrar_temas(client, canal, busqueda):
    """Devuelve una lista de (topico_id, titulo) que coincidan con la
    busqueda dentro de un chat. Si el chat no usa Temas, devuelve [(None, '')]
    para indicar 'revisar el chat completo, sin filtrar por tema'."""
    busqueda_normalizada = busqueda.lower()
    try:
        resultado = await client(GetForumTopicsRequest(
            peer=canal, offset_date=None, offset_id=0, offset_topic=0, limit=100
        ))
    except Exception:
        return [(None, "(sin temas)")]

    coincidencias = []
    for topico in resultado.topics:
        titulo = getattr(topico, 'title', '')
        if busqueda_normalizada in titulo.lower():
            coincidencias.append((topico.id, titulo))

    if not coincidencias:
        return [(None, "(sin tema que coincida, se revisa todo)")]

    return coincidencias


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
    estado = cargar_estado(estado_texto)

    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.start()

    canales = await encontrar_canales(client, NOMBRE_CANAL_BUSQUEDA)
    if not canales:
        await client.disconnect()
        return

    todos_mensajes_nuevos = []  # lista de (mensaje, nombre_fuente)

    for dialogo in canales:
        canal = dialogo.entity
        temas = await encontrar_temas(client, canal, NOMBRE_TEMA_BUSQUEDA)

        for topico_id, titulo_tema in temas:
            clave_fuente = f"{dialogo.id}_{topico_id}"
            ultimo_id_revisado = estado.get(clave_fuente, 0)

            kwargs_mensajes = {'min_id': ultimo_id_revisado, 'limit': 50}
            if topico_id is not None:
                kwargs_mensajes['reply_to'] = topico_id

            mensajes_de_esta_fuente = []
            async for mensaje in client.iter_messages(canal, **kwargs_mensajes):
                if mensaje.id <= ultimo_id_revisado:
                    continue
                if mensaje.text:
                    mensajes_de_esta_fuente.append(mensaje)

            if mensajes_de_esta_fuente:
                nuevo_max = max(m.id for m in mensajes_de_esta_fuente)
                estado[clave_fuente] = nuevo_max
                nombre_fuente = f"{dialogo.name} / {titulo_tema}"
                for m in mensajes_de_esta_fuente:
                    todos_mensajes_nuevos.append((m, nombre_fuente))
            else:
                # Aunque no haya mensajes nuevos, guardar que ya se reviso
                # esta fuente (para no quedar en 0 siempre si el chat esta
                # vacio de mensajes recientes).
                estado.setdefault(clave_fuente, ultimo_id_revisado)

    if not todos_mensajes_nuevos:
        print('Sin mensajes nuevos en ninguna fuente desde la ultima revision.')
        subir_texto(service, NOMBRE_ESTADO, json.dumps(estado), file_id=estado_id)
        await client.disconnect()
        return

    todos_mensajes_nuevos.sort(key=lambda par: par[0].date)
    señales_detectadas = [(m, f) for m, f in todos_mensajes_nuevos if parece_señal(m.text)]

    print(f'{len(todos_mensajes_nuevos)} mensajes nuevos en total, {len(señales_detectadas)} parecen señales.')

    if señales_detectadas:
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        log_id, log_previo = descargar_texto(service, NOMBRE_LOG)
        lineas = [log_previo] if log_previo else []
        lineas.append(f"\n=== Revision {fecha} ===")
        for m, fuente in señales_detectadas:
            hora_msg = m.date.strftime("%Y-%m-%d %H:%M UTC")
            lineas.append(f"\n--- [{fuente}] Mensaje del {hora_msg} (id {m.id}) ---")
            lineas.append(m.text)

        texto_final = "\n".join(lineas)
        subir_texto(service, NOMBRE_LOG, texto_final, file_id=log_id)

    subir_texto(service, NOMBRE_ESTADO, json.dumps(estado), file_id=estado_id)
    print('Estado actualizado para todas las fuentes revisadas.')

    await client.disconnect()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main())
