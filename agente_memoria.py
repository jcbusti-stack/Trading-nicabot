"""
Agente de Memoria y Precision - Trading-nicabot
Guarda un historial diario de las predicciones de sentimiento de cada
moneda, y las compara contra el precio real del dia siguiente para medir
que tan preciso ha sido cada activo a lo largo del tiempo.

No usa la API de Claude (no tiene costo adicional) - es comparacion pura
de datos: sentimiento predicho vs movimiento de precio real.

Se ejecuta AL FINAL del workflow diario, despues del verificador.

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

NOMBRE_HISTORIAL = 'historial_predicciones.json'
NOMBRE_REPORTE = 'precision_agentes.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'

DIAS_A_CONSERVAR = 30


def obtener_credenciales_drive():
    match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
    info = json.loads(match.group(0))
    return Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive']
    )


def buscar_archivo(service, nombre):
    query = f"name = '{nombre}' and '{FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
    files = results.get('files', [])
    return files[0]['id'] if files else None


def descargar_texto(service, file_id):
    if not file_id:
        return ""
    try:
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        return buf.getvalue().decode('utf-8')
    except Exception as e:
        print(f"No se pudo descargar: {e}")
        return ""


def subir_texto(service, nombre, texto, mimetype='text/plain'):
    with open(nombre, 'w', encoding='utf-8') as f:
        f.write(texto)
    file_id = buscar_archivo(service, nombre)
    media = MediaFileUpload(nombre, mimetype=mimetype)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f'{nombre} actualizado en Drive.')
    else:
        metadata = {'name': nombre, 'mimeType': mimetype, 'parents': [FOLDER_ID]}
        service.files().create(body=metadata, media_body=media, fields='id').execute()
        print(f'{nombre} creado en Drive.')


def parsear_sentimiento(texto):
    """Extrae {SIMBOLO: 'Alcista'/'Bajista'/'Neutral'} del reporte de sentimiento."""
    resultado = {}
    for linea in texto.split("\n"):
        m = re.match(r'-\s*([A-Z]+):\s*(Alcista|Bajista|Neutral)', linea.strip())
        if m:
            resultado[m.group(1)] = m.group(2)
    return resultado


def parsear_precios_escaner(texto):
    """Extrae {SIMBOLO: precio} del reporte del escaner de oportunidades."""
    resultado = {}
    for linea in texto.split("\n"):
        m = re.match(r'-\s*([A-Z]+):.*?precio\s+([\d.eE+-]+)', linea.strip())
        if m:
            try:
                resultado[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return resultado


def parsear_precio_sol(texto):
    m = re.search(r'Precio SOL:\s*\$?([\d.]+)', texto)
    return float(m.group(1)) if m else None


def evaluar_predicciones(historial, sentimiento_hoy, precios_hoy):
    """Compara la ultima prediccion guardada contra los precios de hoy."""
    if not historial.get("snapshots"):
        return historial

    ultimo = historial["snapshots"][-1]
    predicciones_previas = ultimo.get("sentimiento", {})
    precios_previos = ultimo.get("precios", {})

    stats = historial.setdefault("stats", {})

    for simbolo, sentimiento in predicciones_previas.items():
        if sentimiento == "Neutral" or sentimiento == "Sin datos":
            continue
        if simbolo not in precios_previos or simbolo not in precios_hoy:
            continue

        precio_antes = precios_previos[simbolo]
        precio_ahora = precios_hoy[simbolo]
        if precio_antes <= 0:
            continue

        cambio_pct = (precio_ahora - precio_antes) / precio_antes * 100
        subio = cambio_pct > 0.3
        bajo = cambio_pct < -0.3

        acierto = None
        if sentimiento == "Alcista" and subio:
            acierto = True
        elif sentimiento == "Alcista" and bajo:
            acierto = False
        elif sentimiento == "Bajista" and bajo:
            acierto = True
        elif sentimiento == "Bajista" and subio:
            acierto = False

        if acierto is None:
            continue

        registro = stats.setdefault(simbolo, {"aciertos": 0, "total": 0})
        registro["total"] += 1
        if acierto:
            registro["aciertos"] += 1

    return historial


def generar_reporte_precision(stats):
    if not stats:
        return (
            "Todavia no hay suficiente historial para medir precision.\n"
            "Se necesitan al menos 2 dias de datos consecutivos.\n"
            "Vuelve a revisar mañana."
        )

    filas = []
    for simbolo, datos in stats.items():
        total = datos["total"]
        aciertos = datos["aciertos"]
        if total == 0:
            continue
        pct = (aciertos / total) * 100
        filas.append((simbolo, pct, aciertos, total))

    filas.sort(key=lambda x: x[1], reverse=True)

    lineas = ["Precision del sentimiento por moneda (dias evaluados vs precio real):", ""]
    for simbolo, pct, aciertos, total in filas:
        lineas.append(f"- {simbolo}: {pct:.0f}% de acierto ({aciertos} de {total} dias)")

    lineas.append("")
    lineas.append(
        "Nota: se compara si el sentimiento del dia anterior (alcista/bajista) "
        "coincidio con la direccion real del precio al dia siguiente. "
        "Los sentimientos 'Neutral' no se evaluan."
    )
    return "\n".join(lineas)


def main():
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado. No se puede continuar.')
        return

    creds = obtener_credenciales_drive()
    service = build('drive', 'v3', credentials=creds)

    # Cargar historial previo
    historial_id = buscar_archivo(service, NOMBRE_HISTORIAL)
    historial_texto = descargar_texto(service, historial_id)
    try:
        historial = json.loads(historial_texto) if historial_texto.strip() else {"snapshots": [], "stats": {}}
    except json.JSONDecodeError:
        historial = {"snapshots": [], "stats": {}}
    historial.setdefault("snapshots", [])
    historial.setdefault("stats", {})

    # Leer los reportes de hoy (mismo runner, ya se generaron antes en el workflow)
    sentimiento_texto = ""
    oportunidades_texto = ""
    senal_sol_texto = ""
    try:
        with open("sentimiento_multi_activo.txt", "r", encoding="utf-8") as f:
            sentimiento_texto = f.read()
    except FileNotFoundError:
        pass
    try:
        with open("oportunidades.txt", "r", encoding="utf-8") as f:
            oportunidades_texto = f.read()
    except FileNotFoundError:
        pass
    try:
        with open("senal_sol.txt", "r", encoding="utf-8") as f:
            senal_sol_texto = f.read()
    except FileNotFoundError:
        pass

    sentimiento_hoy = parsear_sentimiento(sentimiento_texto)
    precios_hoy = parsear_precios_escaner(oportunidades_texto)
    precio_sol = parsear_precio_sol(senal_sol_texto)
    if precio_sol:
        precios_hoy["SOL"] = precio_sol

    # Evaluar predicciones de ayer contra precios de hoy
    historial = evaluar_predicciones(historial, sentimiento_hoy, precios_hoy)

    # Agregar snapshot de hoy
    hoy = datetime.now().strftime("%Y-%m-%d")
    historial["snapshots"].append({
        "fecha": hoy,
        "sentimiento": sentimiento_hoy,
        "precios": precios_hoy,
    })
    historial["snapshots"] = historial["snapshots"][-DIAS_A_CONSERVAR:]

    # Generar reporte legible
    reporte = generar_reporte_precision(historial["stats"])
    reporte_completo = f"Precision de Agentes - Actualizado {hoy}\n\n{reporte}"

    print(reporte_completo)

    subir_texto(service, NOMBRE_HISTORIAL, json.dumps(historial, indent=2), mimetype='application/json')
    subir_texto(service, NOMBRE_REPORTE, reporte_completo, mimetype='text/plain')


if __name__ == "__main__":
    main()
