"""
Verificador de Señales Externas - Trading-nicabot

Lleva un registro en PAPEL (sin dinero real) de señales compartidas en el
canal de Telegram "Trading Pockets VIP SEÑALES", para medir su tasa de
acierto real con el tiempo, ANTES de considerar seguir alguna con dinero.

IMPORTANTE:
- Se registra SIN apalancamiento y sin el tamaño de posicion que ellos
  proponen (10% + apalancamiento 10x) - eso va contra las reglas fijas
  del perfil de Juan Carlos. Aqui solo se mide si la DIRECCION de la señal
  (si el precio se mueve como ellos predicen) es acertada o no.
- Esto NO es una recomendacion de Claude ni de Trading-nicabot - es
  simplemente un registro de lo que otra fuente externa predijo.

Como agregar una señal nueva: edita manualmente senales_externas.csv en
Drive, agregando una fila con estado "ABIERTA". Este script revisa el
precio actual y actualiza el estado a "TP1 ALCANZADO", "SL ALCANZADO",
o deja "ABIERTA" si no ha llegado a ningun nivel.

Requiere: GCP_SA_KEY
"""

import io
import json
import os
import re
from datetime import datetime

import ccxt
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

NOMBRE_ARCHIVO = 'senales_externas_v2.csv'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

exchange = ccxt.okx()


def obtener_credenciales_drive():
    match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
    info = json.loads(match.group(0))
    return Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive']
    )


def descargar_csv(service):
    query = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
    files = results.get('files', [])
    if not files:
        print(f'{NOMBRE_ARCHIVO} no encontrado en Drive.')
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


def obtener_precio_actual(simbolo):
    try:
        ticker = exchange.fetch_ticker(f"{simbolo}/USDT")
        return ticker['last']
    except Exception as e:
        print(f'No se pudo obtener precio de {simbolo}: {e}')
        return None


def evaluar_fila(fila, precio_actual):
    """
    Revisa si el precio actual toco algun TP o el SL, para señales SHORT
    (vender/bajar) o LONG (comprar/subir). Devuelve el nuevo estado y el
    resultado en % si se cerro.
    """
    direccion = fila['direccion'].upper()
    entrada = float(fila['entrada'])
    sl = float(fila['sl'])

    tps = []
    for clave in ['tp1', 'tp2', 'tp3', 'tp4']:
        val = fila.get(clave, '').strip()
        if val:
            tps.append(float(val))

    if direccion == 'SHORT':
        # En short, gana si el precio BAJA hacia los TPs, pierde si SUBE al SL
        if precio_actual >= sl:
            pnl = (entrada - sl) / entrada * 100
            return 'SL ALCANZADO', round(pnl, 2)
        for i, tp in enumerate(tps, 1):
            if precio_actual <= tp:
                pnl = (entrada - tp) / entrada * 100
                return f'TP{i} ALCANZADO', round(pnl, 2)
    else:  # LONG
        if precio_actual <= sl:
            pnl = (sl - entrada) / entrada * 100
            return 'SL ALCANZADO', round(pnl, 2)
        for i, tp in enumerate(tps, 1):
            if precio_actual >= tp:
                pnl = (tp - entrada) / entrada * 100
                return f'TP{i} ALCANZADO', round(pnl, 2)

    return 'ABIERTA', None


def procesar_csv(texto_csv):
    lineas = texto_csv.strip().split('\n')
    encabezado = lineas[0].split(',')
    filas = []

    for linea in lineas[1:]:
        valores = linea.split(',')
        fila = dict(zip(encabezado, valores))
        filas.append(fila)

    reporte_cambios = []

    for fila in filas:
        if fila.get('estado', '').strip() != 'ABIERTA':
            continue  # ya se cerro, no revisar de nuevo

        precio_actual = obtener_precio_actual(fila['moneda'])
        if precio_actual is None:
            continue

        nuevo_estado, resultado = evaluar_fila(fila, precio_actual)

        if nuevo_estado != 'ABIERTA':
            fila['estado'] = nuevo_estado
            fila['resultado_pct'] = str(resultado) if resultado is not None else ''
            reporte_cambios.append(
                f"{fila['moneda']} ({fila['direccion']}) del {fila['fecha']}: "
                f"{nuevo_estado}, resultado {resultado}%"
            )
        else:
            print(f"{fila['moneda']}: sigue abierta, precio actual ${precio_actual}")

    # Reconstruir CSV
    lineas_nuevas = [','.join(encabezado)]
    for fila in filas:
        lineas_nuevas.append(','.join(fila.get(c, '') for c in encabezado))

    return '\n'.join(lineas_nuevas), reporte_cambios


def subir_csv(service, file_id, texto_csv):
    with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
        f.write(texto_csv)
    media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/csv')
    service.files().update(fileId=file_id, media_body=media).execute()
    print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')


def generar_resumen(texto_csv):
    lineas = texto_csv.strip().split('\n')
    encabezado = lineas[0].split(',')
    filas = [dict(zip(encabezado, l.split(','))) for l in lineas[1:]]

    cerradas = [f for f in filas if f['estado'] not in ('ABIERTA', '')]
    ganadoras = [f for f in cerradas if 'TP' in f['estado']]

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Resumen Señales Externas (papel, sin apalancamiento) - {fecha}", ""]
    L.append(f"Total registradas: {len(filas)} | Abiertas: {len(filas) - len(cerradas)} | Cerradas: {len(cerradas)}")

    if cerradas:
        tasa = round(len(ganadoras) / len(cerradas) * 100)
        L.append(f"Tasa de acierto hasta ahora: {tasa}% ({len(ganadoras)} de {len(cerradas)})")
    else:
        L.append("Todavia no hay suficientes señales cerradas para calcular tasa de acierto.")

    return "\n".join(L)


def main():
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado.')
        return

    creds = obtener_credenciales_drive()
    service = build('drive', 'v3', credentials=creds)

    file_id, texto_csv = descargar_csv(service)
    if file_id is None:
        return

    texto_actualizado, cambios = procesar_csv(texto_csv)

    if cambios:
        print("Cambios detectados:")
        for c in cambios:
            print(f"  - {c}")
        subir_csv(service, file_id, texto_actualizado)
    else:
        print("Sin cambios - todas las señales abiertas siguen sin tocar TP ni SL.")

    print("")
    print(generar_resumen(texto_actualizado))


if __name__ == "__main__":
    main()
