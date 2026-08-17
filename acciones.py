"""
Portafolio de Acciones - Trading-nicabot
Reutiliza la MISMA autenticacion de Kraken que ya usa main.py (portfolio.csv
de cripto), pero filtra y guarda por separado tus acciones y ETFs
(NVIDIA, PayPal, Intel, etc) en un archivo aparte: acciones.csv

Los precios de las acciones se obtienen con yfinance (Yahoo Finance, gratis,
sin API key) porque el ticker publico de Kraken es solo para cripto.

IMPORTANTE: la primera vez que corras esto, revisa el log de "DEBUG - todos
los codigos en tu balance de Kraken" para confirmar los tickers exactos que
usa Kraken para tus acciones, y ajusta la lista TICKERS_ACCIONES si hace falta.
"""

import hashlib
import hmac
import base64
import json
import os
import re
import time
import urllib.parse
from datetime import datetime

import requests
import yfinance as yf
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

KRAKEN_API_KEY = (os.environ.get("KRAKEN_API_KEY") or "").strip()
KRAKEN_API_SECRET = (os.environ.get("KRAKEN_API_SECRET") or "").strip()
GCP_SA_KEY = (os.environ.get("GCP_SA_KEY") or "").strip()
FOLDER_ID = (os.environ.get("DRIVE_FOLDER_ID") or "").strip() or "16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt"

NOMBRE_CSV = "acciones.csv"

# Ajusta esta lista con los tickers reales que veas en el DEBUG la primera vez.
# Confirmados en tu cuenta de Kraken: NVIDIA, PayPal, Intel, Robinhood
# Ventures Fund, Cognex, iShares Silver Trust, Comcast.
TICKERS_ACCIONES = {"NVDA", "PYPL", "INTC", "RVI", "CGNX", "SLV", "CMCSA"}


def kraken_request(uri_path, data, api_key, api_secret):
    """Copiado tal cual de main.py para no romper la firma/autenticacion."""
    nonce = str(int(time.time() * 1000))
    data = {**data, "nonce": nonce}
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    headers = {"API-Key": api_key, "API-Sign": ""}

    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    signature = hmac.new(base64.b64decode(api_secret), message, hashlib.sha512)
    headers["API-Sign"] = base64.b64encode(signature.digest()).decode().strip()

    response = requests.post(
        "https://api.kraken.com" + uri_path,
        headers=headers,
        data=data,
        timeout=30,
    )
    return response.json()


def obtener_balances():
    res = kraken_request("/0/private/Balance", {}, KRAKEN_API_KEY, KRAKEN_API_SECRET)
    if res.get("error"):
        print("Error Kraken Balance:", res["error"])
        return None
    return res.get("result") or {}


def limpiar_codigo(codigo):
    """Quita sufijos de tokenizacion (.S, .F, etc) que a veces usa Kraken."""
    codigo = (codigo or "").upper().strip()
    if "." in codigo:
        codigo = codigo.split(".")[0]
    return codigo


def obtener_precio_accion(ticker):
    try:
        info = yf.Ticker(ticker)
        precio = info.fast_info.get("lastPrice")
        if precio:
            return float(precio)
        historial = info.history(period="1d")
        if not historial.empty:
            return float(historial["Close"].iloc[-1])
    except Exception as e:
        print(f"No se pudo obtener precio de {ticker}:", e)
    return None


def obtener_credenciales_drive():
    match = re.search(r"\{.*\}", GCP_SA_KEY, re.DOTALL)
    info = json.loads(match.group(0))
    return Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )


def resolver_file_id(service):
    query = f"name = '{NOMBRE_CSV}' and '{FOLDER_ID}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id)", pageSize=1).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def subir_a_drive(service, texto_csv):
    with open(NOMBRE_CSV, "w", encoding="utf-8") as f:
        f.write(texto_csv)

    file_id = resolver_file_id(service)
    media = MediaFileUpload(NOMBRE_CSV, mimetype="text/csv")
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
        print(f"{NOMBRE_CSV} actualizado en Drive.")
    else:
        metadata = {"name": NOMBRE_CSV, "mimeType": "text/csv", "parents": [FOLDER_ID]}
        service.files().create(body=metadata, media_body=media, fields="id").execute()
        print(f"{NOMBRE_CSV} creado en Drive.")


def main():
    if not KRAKEN_API_KEY or not KRAKEN_API_SECRET:
        print("Faltan KRAKEN_API_KEY / KRAKEN_API_SECRET.")
        return

    balances = obtener_balances()
    if balances is None:
        return

    print("--- DEBUG: todos los codigos en tu balance de Kraken ---")
    for codigo, cantidad in balances.items():
        print(f"  {codigo}: {cantidad}")
    print("--------------------------------------------------------")

    filas = []
    total_usd = 0.0
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M")

    for codigo, cantidad in balances.items():
        codigo_limpio = limpiar_codigo(codigo)
        if codigo_limpio not in TICKERS_ACCIONES:
            continue

        qty = float(cantidad)
        if qty <= 0:
            continue

        precio = obtener_precio_accion(codigo_limpio)
        if precio is None:
            print(f"Aviso: no se pudo valorar {codigo_limpio}, se omite del total.")
            continue

        valor_usd = qty * precio
        total_usd += valor_usd

        filas.append({
            "seccion": "balance",
            "tipo": "accion",
            "activo": codigo_limpio,
            "cantidad": qty,
            "precio": round(precio, 2),
            "valor_usd": round(valor_usd, 2),
            "fecha": "",
            "notas": "",
        })

    filas.append({
        "seccion": "total",
        "tipo": "",
        "activo": "TOTAL ACCIONES",
        "cantidad": "",
        "precio": "",
        "valor_usd": round(total_usd, 2),
        "fecha": ahora,
        "notas": f"Actualizado {ahora}",
    })

    columnas = ["seccion", "tipo", "activo", "cantidad", "precio", "valor_usd", "fecha", "notas"]
    lineas_csv = [",".join(columnas)]
    for fila in filas:
        valores = [str(fila[c]) for c in columnas]
        lineas_csv.append(",".join(valores))
    texto_csv = "\n".join(lineas_csv)

    print("")
    print(f"Total en acciones: ${total_usd:,.2f}")
    if not filas or (len(filas) == 1 and total_usd == 0):
        print(
            "AVISO: no se encontraron acciones. Revisa el DEBUG de arriba y "
            "ajusta TICKERS_ACCIONES con los codigos reales que uses Kraken."
        )

    creds = obtener_credenciales_drive()
    service = build("drive", "v3", credentials=creds)
    subir_a_drive(service, texto_csv)


if __name__ == "__main__":
    main()
