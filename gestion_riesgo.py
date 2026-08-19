"""
Agente de Gestion de Riesgo + Veto Informativo de IA - Trading-nicabot

Cuando hay señal de entrada valida en SOL:
1. Calcula un tamaño de posicion sugerido (regla del 1% de riesgo).
2. Le pide a Claude que revise el sentimiento y las ballenas del dia y
   te avise si ve algo preocupante que valga la pena considerar.

IMPORTANTE: el paso 2 es SOLO INFORMATIVO. La IA nunca bloquea, cancela,
ni modifica la señal de entrada validada por backtesting. Solo te da una
alerta adicional para que TU decidas con mas contexto. La unica cosa que
SI bloquea entradas es el kill switch (ver bot_paper.py), que se basa en
una regla fija de drawdown, no en el criterio de una IA.

Requiere: GCP_SA_KEY (ya configurado), ANTHROPIC_API_KEY (para el veto)
"""

import io
import json
import os
import re
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

NOMBRE_ARCHIVO = 'gestion_riesgo.txt'
NOMBRE_PORTFOLIO = 'portfolio.csv'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'
ANTHROPIC_API_KEY = (os.environ.get('ANTHROPIC_API_KEY') or '').strip()

RIESGO_POR_OPERACION_PCT = 1.0
STOP_LOSS_PCT = 5.0
MODELO = 'claude-haiku-4-5-20251001'


def obtener_credenciales_drive():
    match = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
    info = json.loads(match.group(0))
    return Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive']
    )


def descargar_archivo_drive(service, nombre):
    query = f"name = '{nombre}' and '{FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields='files(id)', pageSize=1).execute()
    files = results.get('files', [])
    if not files:
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


def parsear_senal_sol(texto):
    datos = {}
    m = re.search(r'Precio SOL:\s*\$?([\d.]+)', texto)
    if m:
        datos['precio'] = float(m.group(1))
    m = re.search(r'Señal de entrada completa:\s*(SI|NO)', texto, re.IGNORECASE)
    if m:
        datos['senal_completa'] = m.group(1).upper() == 'SI'
    return datos


def obtener_capital_total(csv_texto):
    for linea in csv_texto.strip().split("\n"):
        partes = linea.split(",")
        if len(partes) >= 6 and partes[0] == "total":
            try:
                return float(partes[5])
            except ValueError:
                return None
    return None


def calcular_posicion(capital_total, precio_entrada):
    monto_riesgo = capital_total * (RIESGO_POR_OPERACION_PCT / 100)
    tamano_posicion_usd = monto_riesgo / (STOP_LOSS_PCT / 100)
    tamano_posicion_usd = min(tamano_posicion_usd, capital_total)
    cantidad_sol = tamano_posicion_usd / precio_entrada
    precio_stop_loss = precio_entrada * (1 - STOP_LOSS_PCT / 100)

    return {
        "monto_riesgo": monto_riesgo,
        "tamano_posicion_usd": tamano_posicion_usd,
        "cantidad_sol": cantidad_sol,
        "precio_stop_loss": precio_stop_loss,
    }


def generar_veto_ia(sentimiento_texto, ballenas_texto, precio):
    """
    Le pregunta a Claude si hay algo preocupante en el contexto actual.
    SOLO INFORMATIVO - nunca bloquea la operacion, solo agrega contexto.
    """
    if not ANTHROPIC_API_KEY:
        return "No disponible (falta ANTHROPIC_API_KEY)."

    try:
        import anthropic
        cliente = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        prompt = f"""Acaba de aparecer una señal de entrada TECNICA valida para
comprar SOL a ${precio:.2f}, segun una estrategia ya validada con backtesting
(EMA/RSI/MACD). Esta señal técnica es la que manda - tu trabajo NO es aprobarla
ni rechazarla, es revisar el contexto y avisar si hay algo genuinamente
preocupante que Juan Carlos deberia saber antes de decidir.

SENTIMIENTO DE NOTICIAS DE HOY (SOL y mercado general):
{sentimiento_texto[:3000] if sentimiento_texto else "No disponible"}

BALLENAS (movimientos grandes de BTC/ETH del bloque mas reciente):
{ballenas_texto[:2000] if ballenas_texto else "No disponible"}

Responde en español, maximo 5 lineas, en formato de lista simple, apto para
lector de pantalla. Indica:
1. Si ves algo genuinamente alarmante (ej: noticia de hackeo, caida de un
   exchange, evento de riesgo real) - o si el contexto es normal.
2. Nunca digas "no compres" ni "cancela la señal" - solo describe lo que ves
   y deja la decision a Juan Carlos.
3. Si no hay nada preocupante, dilo claramente y brevemente, no inventes
   riesgos que no existen solo para parecer util.
"""

        respuesta = cliente.messages.create(
            model=MODELO,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return respuesta.content[0].text.strip()

    except Exception as e:
        return f"No se pudo generar el veto informativo: {e}"


def generar_reporte(senal, capital_total, veto_texto):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [f"Gestion de Riesgo - {fecha}", ""]
    lineas.append(
        f"Regla aplicada: arriesgar maximo {RIESGO_POR_OPERACION_PCT:.0f}% del capital "
        f"total por operacion. Stop-loss de la estrategia validada: {STOP_LOSS_PCT:.0f}%."
    )
    lineas.append("")

    if not senal.get('senal_completa'):
        lineas.append(
            "No hay señal de entrada activa en SOL ahora mismo. "
            "No se calcula tamaño de posicion."
        )
        return "\n".join(lineas)

    if capital_total is None:
        lineas.append(
            "Hay señal de entrada activa en SOL, pero no se pudo leer el "
            "capital total del portafolio para calcular el tamaño sugerido."
        )
        return "\n".join(lineas)

    precio = senal.get('precio')
    if not precio:
        lineas.append("Hay señal activa pero no se encontro el precio de SOL.")
        return "\n".join(lineas)

    calc = calcular_posicion(capital_total, precio)

    lineas.append("SEÑAL DE ENTRADA ACTIVA EN SOL.")
    lineas.append("")
    lineas.append(f"Capital total del portafolio: ${capital_total:,.2f}")
    lineas.append(f"Precio actual de SOL: ${precio:,.2f}")
    lineas.append(f"Monto maximo a arriesgar en esta operacion: ${calc['monto_riesgo']:,.2f}")
    lineas.append(f"Tamaño de posicion sugerido: ${calc['tamano_posicion_usd']:,.2f}")
    lineas.append(f"Cantidad aproximada de SOL: {calc['cantidad_sol']:.4f}")
    lineas.append(f"Nivel de stop-loss sugerido: ${calc['precio_stop_loss']:,.2f}")
    lineas.append("")
    lineas.append(
        "IMPORTANTE: esto es solo un calculo de referencia, no una orden. "
        "Juan Carlos decide si entra, con que monto, y ejecuta manualmente en Kraken."
    )
    lineas.append("")
    lineas.append("--- VETO INFORMATIVO DE IA (no bloquea nada, solo avisa) ---")
    lineas.append(veto_texto)

    return "\n".join(lineas)


def subir_a_drive(service, texto):
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


def main():
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado. No se puede continuar.')
        return

    creds = obtener_credenciales_drive()
    service = build('drive', 'v3', credentials=creds)

    senal_texto = ""
    try:
        with open('senal_sol.txt', 'r', encoding='utf-8') as f:
            senal_texto = f.read()
    except FileNotFoundError:
        print('senal_sol.txt no encontrado en el runner actual.')

    senal = parsear_senal_sol(senal_texto)

    print("Descargando portafolio desde Drive...")
    portfolio_csv = descargar_archivo_drive(service, NOMBRE_PORTFOLIO)
    capital_total = obtener_capital_total(portfolio_csv)

    veto_texto = ""
    if senal.get('senal_completa'):
        print("Señal activa detectada - generando veto informativo de IA...")
        sentimiento_texto = descargar_archivo_drive(service, 'sentimiento_multi_activo.txt')
        ballenas_texto = descargar_archivo_drive(service, 'ballenas.txt')
        veto_texto = generar_veto_ia(sentimiento_texto, ballenas_texto, senal.get('precio', 0))

    reporte = generar_reporte(senal, capital_total, veto_texto)
    print("")
    print(reporte)

    subir_a_drive(service, reporte)


if __name__ == "__main__":
    main()
