"""Sube el resumen de la senal a Google Drive (si GCP_SA_KEY esta configurado)."""
import json
import logging
import os
import re

log = logging.getLogger('nicabot.notificador')
# drive.file solo permite tocar archivos que creo esta cuenta de servicio.
# Si el archivo ya existia creado por otro, usar DRIVE_SCOPE=https://www.googleapis.com/auth/drive
SCOPE_DEFECTO = 'https://www.googleapis.com/auth/drive'


def subir_texto(texto, nombre, folder_id):
    with open(nombre, 'w', encoding='utf-8') as f:
        f.write(texto)
    sa = (os.environ.get('GCP_SA_KEY') or '').strip()
    if not sa:
        log.info('GCP_SA_KEY no configurado, no se sube a Drive (archivo local %s).', nombre)
        return False
    if not folder_id:
        log.warning('Sin folder de Drive configurado; no se sube.')
        return False
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        m = re.search(r'\{.*\}', sa, re.DOTALL)
        creds = Credentials.from_service_account_info(
            json.loads(m.group(0)), scopes=[os.environ.get('DRIVE_SCOPE') or SCOPE_DEFECTO])
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        q = f"name = '{nombre}' and '{folder_id}' in parents and trashed = false"
        files = service.files().list(q=q, fields='files(id)', pageSize=1).execute().get('files', [])
        media = MediaFileUpload(nombre, mimetype='text/plain')
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
        else:
            service.files().create(body={'name': nombre, 'mimeType': 'text/plain', 'parents': [folder_id]},
                                   media_body=media, fields='id').execute()
        log.info('%s subido a Drive.', nombre)
        return True
    except Exception as e:  # no tumbar el bot por Drive
        log.error('No se pudo subir a Drive: %s', e)
        return False
