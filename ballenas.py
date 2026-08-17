"""
Agente de Ballenas (BTC y ETH) - Trading-nicabot
Revisa el bloque mas reciente de Bitcoin y Ethereum y marca transferencias
grandes (por encima de un umbral en dolares) - señal de posible movimiento
institucional o de "ballenas".

LIMITACION HONESTA: esto es una fotografia del bloque mas reciente al momento
de correr (aprox. cada 10 min de actividad real), NO vigilancia continua
24/7 como un servicio de pago. Tampoco identifica el nombre de las carteras
(eso requiere datos pagados) - solo el monto.

Requiere: ETHERSCAN_API_KEY (gratis, ver instrucciones), GCP_SA_KEY
"""

import json
import os
import re
import urllib.request
from datetime import datetime

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

NOMBRE_ARCHIVO = 'ballenas.txt'
GCP_SA_KEY = (os.environ.get('GCP_SA_KEY') or '').strip()
FOLDER_ID = (os.environ.get('DRIVE_FOLDER_ID') or '').strip() or '16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt'
ETHERSCAN_API_KEY = (os.environ.get('ETHERSCAN_API_KEY') or '').strip()

UMBRAL_USD = 1_000_000  # Solo mostrar transferencias mayores a esto


def peticion_json(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode('utf-8'))


def obtener_precios():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum&vs_currencies=usd"
    datos = peticion_json(url)
    return datos['bitcoin']['usd'], datos['ethereum']['usd']


def revisar_bitcoin(precio_btc):
    try:
        hash_ultimo_bloque = peticion_json("https://blockchain.info/q/latesthash").strip()
    except Exception:
        # el endpoint /q/latesthash devuelve texto plano, no JSON
        req = urllib.request.Request("https://blockchain.info/q/latesthash",
                                      headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            hash_ultimo_bloque = response.read().decode('utf-8').strip()

    bloque = peticion_json(f"https://blockchain.info/rawblock/{hash_ultimo_bloque}?format=json")
    transacciones_grandes = []

    for tx in bloque.get('tx', []):
        if not tx.get('out'):
            continue
        salida_mayor_sat = max((o.get('value', 0) for o in tx['out']), default=0)
        monto_btc = salida_mayor_sat / 1e8
        monto_usd = monto_btc * precio_btc
        if monto_usd >= UMBRAL_USD:
            transacciones_grandes.append(monto_usd)

    transacciones_grandes.sort(reverse=True)
    return transacciones_grandes, bloque.get('height')


def revisar_ethereum(precio_eth):
    if not ETHERSCAN_API_KEY:
        return None, None

    url_bloque_num = (
        f"https://api.etherscan.io/v2/api?chainid=1&module=proxy"
        f"&action=eth_blockNumber&apikey={ETHERSCAN_API_KEY}"
    )
    resultado = peticion_json(url_bloque_num)
    numero_bloque_hex = resultado.get('result')

    url_bloque = (
        f"https://api.etherscan.io/v2/api?chainid=1&module=proxy"
        f"&action=eth_getBlockByNumber&tag={numero_bloque_hex}&boolean=true"
        f"&apikey={ETHERSCAN_API_KEY}"
    )
    bloque = peticion_json(url_bloque)
    transacciones = bloque.get('result', {}).get('transactions', [])

    transacciones_grandes = []
    for tx in transacciones:
        valor_wei_hex = tx.get('value', '0x0')
        try:
            valor_wei = int(valor_wei_hex, 16)
        except ValueError:
            continue
        monto_eth = valor_wei / 1e18
        monto_usd = monto_eth * precio_eth
        if monto_usd >= UMBRAL_USD:
            transacciones_grandes.append(monto_usd)

    transacciones_grandes.sort(reverse=True)
    numero_bloque = int(numero_bloque_hex, 16)
    return transacciones_grandes, numero_bloque


def generar_reporte(precio_btc, precio_eth, btc_grandes, btc_bloque, eth_grandes, eth_bloque):
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    lineas = [f"Ballenas (BTC y ETH) - {fecha}", ""]
    lineas.append(
        f"Umbral: transferencias mayores a ${UMBRAL_USD:,.0f}. "
        "Solo se revisa el bloque mas reciente al momento de correr "
        "(no es vigilancia continua las 24 horas)."
    )
    lineas.append("")

    lineas.append(f"BITCOIN (bloque #{btc_bloque}):")
    if btc_grandes:
        lineas.append(f"  {len(btc_grandes)} transferencia(s) grande(s) detectada(s):")
        for monto in btc_grandes[:5]:
            lineas.append(f"  - ${monto:,.0f}")
    else:
        lineas.append("  Sin transferencias grandes en este bloque.")
    lineas.append("")

    lineas.append(f"ETHEREUM (bloque #{eth_bloque}):" if eth_bloque else "ETHEREUM:")
    if eth_grandes is None:
        lineas.append("  No disponible - falta configurar ETHERSCAN_API_KEY.")
    elif eth_grandes:
        lineas.append(f"  {len(eth_grandes)} transferencia(s) grande(s) detectada(s):")
        for monto in eth_grandes[:5]:
            lineas.append(f"  - ${monto:,.0f}")
    else:
        lineas.append("  Sin transferencias grandes en este bloque.")

    lineas.append("")
    lineas.append(
        "Nota: no se identifica el nombre de las carteras (exchange, ballena "
        "conocida, etc), solo el monto. Un solo bloque es una muestra pequeña, "
        "no una tendencia - usalo como dato adicional, no como señal por si sola."
    )

    return "\n".join(lineas)


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
        print('No se pudo subir el reporte a Drive:', e)


def main():
    print("Obteniendo precios...")
    precio_btc, precio_eth = obtener_precios()

    print("Revisando bloque de Bitcoin...")
    btc_grandes, btc_bloque = revisar_bitcoin(precio_btc)

    print("Revisando bloque de Ethereum...")
    try:
        eth_grandes, eth_bloque = revisar_ethereum(precio_eth)
    except Exception as e:
        print('No se pudo revisar Ethereum:', e)
        eth_grandes, eth_bloque = None, None

    reporte = generar_reporte(precio_btc, precio_eth, btc_grandes, btc_bloque, eth_grandes, eth_bloque)
    print("")
    print(reporte)
    subir_a_drive(reporte)


if __name__ == "__main__":
    main()
