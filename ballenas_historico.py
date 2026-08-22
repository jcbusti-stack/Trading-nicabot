"""
Ballenas Historico (BTC y ETH) - Trading-nicabot

Version mejorada del agente de ballenas: en vez de revisar solo el bloque
mas reciente, revisa un bloque de cada uno de los ultimos 14 dias, para ver
si hay una TENDENCIA de acumulacion o distribucion sostenida - no solo una
fotografia de un momento.

AVISO HONESTO: esto sigue siendo una version gratuita y limitada. Los
servicios de pago (Glassnode, CryptoQuant) analizan TODOS los bloques del
dia, no uno solo por dia como aqui. Esto es una señal aproximada, util
como complemento, no una medicion precisa de flujo total.

Herramienta MANUAL. No cambia nada del bot en vivo.
"""

import json
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_ARCHIVO = 'ballenas_historico_resultados.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'
ETHERSCAN_API_KEY = (os.environ.get('ETHERSCAN_API_KEY') or '').strip()

UMBRAL_USD = 1_000_000
DIAS_A_REVISAR = 14


def peticion_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode('utf-8'))


def peticion_texto(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return response.read().decode('utf-8').strip()


def obtener_precios_historicos():
    """Precio actual como aproximacion - CoinGecko gratis no da historico
    diario facil sin limite de tasa estricto, se usa precio actual para
    todos los dias (limitacion aceptada y documentada)."""
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd"
    datos = peticion_json(url)
    return datos['bitcoin']['usd'], datos['ethereum']['usd']


def bloque_bitcoin_por_fecha(timestamp_unix):
    """Blockchain.info permite buscar bloques por rango de tiempo."""
    url = f"https://blockchain.info/blocks/{timestamp_unix * 1000}?format=json"
    try:
        datos = peticion_json(url)
        if datos and len(datos) > 0:
            return datos[0]['hash']
    except Exception:
        pass
    return None


def contar_ballenas_bitcoin(hash_bloque, precio_btc):
    if not hash_bloque:
        return None
    try:
        bloque = peticion_json(f"https://blockchain.info/rawblock/{hash_bloque}?format=json")
    except Exception:
        return None

    grandes = 0
    for tx in bloque.get('tx', []):
        if not tx.get('out'):
            continue
        salida_mayor_sat = max((o.get('value', 0) for o in tx['out']), default=0)
        monto_usd = (salida_mayor_sat / 1e8) * precio_btc
        if monto_usd >= UMBRAL_USD:
            grandes += 1
    return grandes


def bloque_ethereum_por_fecha(timestamp_unix):
    if not ETHERSCAN_API_KEY:
        return None
    url = (
        f"https://api.etherscan.io/v2/api?chainid=1&module=block"
        f"&action=getblocknobytime&timestamp={timestamp_unix}&closest=before"
        f"&apikey={ETHERSCAN_API_KEY}"
    )
    try:
        resultado = peticion_json(url)
        return resultado.get('result')
    except Exception:
        return None


def contar_ballenas_ethereum(numero_bloque, precio_eth):
    if not numero_bloque:
        return None
    try:
        numero_hex = hex(int(numero_bloque))
        url = (
            f"https://api.etherscan.io/v2/api?chainid=1&module=proxy"
            f"&action=eth_getBlockByNumber&tag={numero_hex}&boolean=true"
            f"&apikey={ETHERSCAN_API_KEY}"
        )
        bloque = peticion_json(url)
        transacciones = bloque.get('result', {}).get('transactions', [])
    except Exception:
        return None

    grandes = 0
    for tx in transacciones:
        valor_wei_hex = tx.get('value', '0x0')
        try:
            valor_wei = int(valor_wei_hex, 16)
        except ValueError:
            continue
        monto_usd = (valor_wei / 1e18) * precio_eth
        if monto_usd >= UMBRAL_USD:
            grandes += 1
    return grandes


def calcular_tendencia(valores):
    """Compara la primera mitad del periodo contra la segunda mitad."""
    validos = [v for v in valores if v is not None]
    if len(validos) < 4:
        return "datos insuficientes"

    mitad = len(validos) // 2
    primera = sum(validos[:mitad]) / mitad
    segunda = sum(validos[mitad:]) / (len(validos) - mitad)

    if primera == 0:
        return "sin actividad en el periodo inicial para comparar"

    cambio_pct = (segunda - primera) / primera * 100
    if cambio_pct > 20:
        return f"AUMENTANDO ({cambio_pct:+.0f}%) - mas actividad de ballenas en dias recientes"
    elif cambio_pct < -20:
        return f"DISMINUYENDO ({cambio_pct:+.0f}%) - menos actividad de ballenas en dias recientes"
    else:
        return f"ESTABLE ({cambio_pct:+.0f}%) - sin cambio claro"


def generar_reporte(datos_btc, datos_eth, precio_btc, precio_eth):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    L = [f"Ballenas - Historico {DIAS_A_REVISAR} dias (BTC y ETH) - {fecha}", ""]
    L.append(
        f"Umbral: transferencias mayores a ${UMBRAL_USD:,.0f}. Un bloque revisado "
        f"por dia (aproximacion, no cobertura total del dia)."
    )
    L.append("")

    L.append("BITCOIN, por dia (mas antiguo primero):")
    for fecha_str, conteo in datos_btc:
        texto_conteo = str(conteo) if conteo is not None else "sin datos"
        L.append(f"  {fecha_str}: {texto_conteo} transferencia(s) grande(s)")
    valores_btc = [c for _, c in datos_btc]
    L.append(f"  Tendencia: {calcular_tendencia(valores_btc)}")
    L.append("")

    L.append("ETHEREUM, por dia (mas antiguo primero):")
    if not ETHERSCAN_API_KEY:
        L.append("  No disponible - falta ETHERSCAN_API_KEY")
    else:
        for fecha_str, conteo in datos_eth:
            texto_conteo = str(conteo) if conteo is not None else "sin datos"
            L.append(f"  {fecha_str}: {texto_conteo} transferencia(s) grande(s)")
        valores_eth = [c for _, c in datos_eth]
        L.append(f"  Tendencia: {calcular_tendencia(valores_eth)}")
    L.append("")

    L.append(
        "NOTA: precio usado es el actual para todos los dias (limitacion de la "
        "version gratuita) - los conteos de dias viejos pueden estar un poco "
        "desviados si el precio cambio mucho desde entonces."
    )
    L.append(
        "Esto sigue siendo aproximado (1 bloque por dia, no el dia completo). "
        "Util para ver tendencia general, no para contar transacciones exactas."
    )

    return "\n".join(L)


def subir_a_drive(texto):
    if not GCP_SA_KEY:
        print('GCP_SA_KEY no configurado.')
        return
    try:
        m = re.search(r'\{.*\}', GCP_SA_KEY, re.DOTALL)
        creds = Credentials.from_service_account_info(
            json.loads(m.group(0)), scopes=['https://www.googleapis.com/auth/drive']
        )
        service = build('drive', 'v3', credentials=creds)
        with open(NOMBRE_ARCHIVO, 'w', encoding='utf-8') as f:
            f.write(texto)
        q = f"name = '{NOMBRE_ARCHIVO}' and '{FOLDER_ID}' in parents and trashed = false"
        files = service.files().list(q=q, fields='files(id)', pageSize=1).execute().get('files', [])
        media = MediaFileUpload(NOMBRE_ARCHIVO, mimetype='text/plain')
        if files:
            service.files().update(fileId=files[0]['id'], media_body=media).execute()
            print(f'{NOMBRE_ARCHIVO} actualizado en Drive.')
        else:
            meta = {'name': NOMBRE_ARCHIVO, 'mimeType': 'text/plain', 'parents': [FOLDER_ID]}
            service.files().create(body=meta, media_body=media, fields='id').execute()
            print(f'{NOMBRE_ARCHIVO} creado en Drive.')
    except Exception as e:
        print('No se pudo subir:', e)


def main():
    print("Obteniendo precios...")
    precio_btc, precio_eth = obtener_precios_historicos()

    ahora = datetime.now(timezone.utc)
    datos_btc = []
    datos_eth = []

    for i in range(DIAS_A_REVISAR, 0, -1):
        dia = ahora - timedelta(days=i)
        timestamp_unix = int(dia.timestamp())
        fecha_str = dia.strftime("%Y-%m-%d")

        print(f"Revisando {fecha_str}...")

        hash_btc = bloque_bitcoin_por_fecha(timestamp_unix)
        conteo_btc = contar_ballenas_bitcoin(hash_btc, precio_btc)
        datos_btc.append((fecha_str, conteo_btc))

        if ETHERSCAN_API_KEY:
            bloque_eth = bloque_ethereum_por_fecha(timestamp_unix)
            conteo_eth = contar_ballenas_ethereum(bloque_eth, precio_eth)
            datos_eth.append((fecha_str, conteo_eth))

        time.sleep(0.5)  # ser considerado con las APIs gratuitas

    reporte = generar_reporte(datos_btc, datos_eth, precio_btc, precio_eth)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
