import os
import time
import json
import re
import io
import urllib.parse
import hashlib
import hmac
import base64
import requests
import pandas as pd
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

# --- Secretos ---
KRAKEN_API_KEY = (os.environ.get("KRAKEN_API_KEY") or "").strip()
KRAKEN_API_SECRET = (os.environ.get("KRAKEN_API_SECRET") or "").strip()
GCP_SA_KEY = (os.environ.get("GCP_SA_KEY") or "").strip()
DRIVE_REFRESH_TOKEN = (os.environ.get("DRIVE_REFRESH_TOKEN") or "").strip()
DRIVE_CLIENT_ID = (os.environ.get("DRIVE_CLIENT_ID") or "").strip()
DRIVE_CLIENT_SECRET = (os.environ.get("DRIVE_CLIENT_SECRET") or "").strip()
FILE_ID = (os.environ.get("DRIVE_FILE_ID") or "").strip()
FOLDER_ID = (os.environ.get("DRIVE_FOLDER_ID") or "").strip() or "16LdgsWAskLkG6uSO1fo3Ex5emboVSbOt"

FIAT = {"USD", "EUR", "GBP", "JPY", "CAD", "CHF", "AUD", "USDC", "USDT"}
SUFIJOS_EARN = {"S", "M", "P", "B"}
MAPEO_SIMBOLOS = {
    "XBT": "BTC", "XXBT": "BTC",
    "XDG": "DOGE", "XXDG": "DOGE",
    "XETH": "ETH", "XXRP": "XRP", "XLTC": "LTC",
}
MIN_VALOR_USD = 0.50
NOMBRE_CSV = "portfolio.csv"


def kraken_request(uri_path, data, api_key, api_secret, as_json=False):
    nonce = str(int(time.time() * 1000))
    data = {**data, "nonce": nonce if not as_json else int(nonce)}

    if as_json:
        postdata = json.dumps(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        headers = {
            "API-Key": api_key,
            "API-Sign": "",
            "Content-Type": "application/json",
        }
        body = postdata
    else:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        headers = {"API-Key": api_key, "API-Sign": ""}
        body = data

    message = uri_path.encode() + hashlib.sha256(encoded).digest()
    signature = hmac.new(base64.b64decode(api_secret), message, hashlib.sha512)
    headers["API-Sign"] = base64.b64encode(signature.digest()).decode().strip()

    response = requests.post(
        "https://api.kraken.com" + uri_path,
        headers=headers,
        data=body,
        timeout=30,
    )
    return response.json()


def limpiar_codigo_activo(codigo):
    codigo = (codigo or "").upper().strip()
    if codigo in MAPEO_SIMBOLOS:
        return MAPEO_SIMBOLOS[codigo]
    if codigo.startswith("X") and len(codigo) == 4:
        codigo = codigo[1:]
    elif codigo.startswith("Z") and len(codigo) == 4:
        codigo = codigo[1:]
    if codigo in MAPEO_SIMBOLOS:
        return MAPEO_SIMBOLOS[codigo]
    return "BTC" if codigo == "XBT" else codigo


def limpiar_par(par):
    par = (par or "").upper()
    for quote in ("USD", "USDT", "USDC", "EUR", "ZUSD"):
        if par.endswith(quote):
            return limpiar_codigo_activo(par[: -len(quote)])
    return limpiar_codigo_activo(par)


def obtener_balances():
    res = kraken_request("/0/private/Balance", {}, KRAKEN_API_KEY, KRAKEN_API_SECRET)
    if res.get("error"):
        print("Error Kraken Balance:", res["error"])
        return None
    return res.get("result") or {}


def obtener_earn():
    res = kraken_request(
        "/0/private/Earn/Allocations",
        {"converted_asset": "USD", "hide_zero_allocations": True},
        KRAKEN_API_KEY,
        KRAKEN_API_SECRET,
        as_json=True,
    )
    if res.get("error"):
        print("Error Kraken Earn:", res["error"])
        return {}

    posiciones = {}
    for item in res.get("result", {}).get("items", []):
        activo = limpiar_codigo_activo(item.get("native_asset", ""))
        total = item.get("amount_allocated", {}).get("total", {})
        cantidad = float(total.get("native", 0) or 0)
        valor_usd = float(total.get("converted", 0) or 0)
        if activo not in posiciones:
            posiciones[activo] = {"cantidad": 0.0, "valor_usd": 0.0}
        posiciones[activo]["cantidad"] += cantidad
        posiciones[activo]["valor_usd"] += valor_usd
    return posiciones


def obtener_historial_trades():
    """Trae TODO el historial de compra/venta desde que se abrio la cuenta,
    sin limite de dias. Kraken solo entrega las operaciones en tandas, asi
    que se van pidiendo tanda por tanda hasta que ya no queden mas."""
    todas = []
    offset = 0
    while True:
        res = kraken_request(
            "/0/private/TradesHistory",
            {"trades": True, "ofs": offset},
            KRAKEN_API_KEY,
            KRAKEN_API_SECRET,
        )
        if res.get("error"):
            print("Error historial trades:", res["error"])
            break

        resultado = res.get("result", {}) or {}
        trades = resultado.get("trades") or {}
        if not trades:
            break

        for info in trades.values():
            lado = info.get("type", "")
            ts = info.get("time", 0)
            fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
            todas.append({
                "seccion": "historial",
                "tipo": "compra" if lado == "buy" else "venta",
                "activo": limpiar_par(info.get("pair", "")),
                "cantidad": float(info.get("vol", 0) or 0),
                "precio": float(info.get("price", 0) or 0),
                "valor_usd": float(info.get("cost", 0) or 0),
                "fecha": fecha,
                "notas": "",
            })

        total_operaciones = resultado.get("count", 0)
        offset += len(trades)
        print("Historial: traidas " + str(offset) + " de " + str(total_operaciones) + " operaciones")

        if offset >= total_operaciones:
            break
        time.sleep(1.5)

    return todas


def obtener_ordenes_abiertas():
    # Igual que con Earn, Kraken ahora espera este endpoint en formato JSON.
    res = kraken_request(
        "/0/private/OpenOrders",
        {"trades": True},
        KRAKEN_API_KEY,
        KRAKEN_API_SECRET,
        as_json=True,
    )

    if res.get("error"):
        print("Error órdenes abiertas:", res["error"])
        return []

    abiertas = (res.get("result", {}).get("open") or {})
    print("DEBUG cantidad de ordenes abiertas encontradas: " + str(len(abiertas)))

    filas = []
    for orden_id, info in abiertas.items():
        descr = info.get("descr", {})
        lado = descr.get("type", "")
        par = descr.get("pair", "")
        ts = info.get("opentm", 0)
        fecha = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
        precio = float(descr.get("price", 0) or 0)

        print("DEBUG orden encontrada -> par: " + str(par) + " | tipo: " + str(lado) + " | precio: " + str(precio) + " | fecha: " + str(fecha))

        filas.append({
            "seccion": "orden_abierta",
            "tipo": "compra" if lado == "buy" else "venta",
            "activo": limpiar_par(par),
            "cantidad": float(info.get("vol", 0) or 0),
            "precio": precio,
            "valor_usd": float(info.get("cost", 0) or 0),
            "fecha": fecha,
            "notas": "Orden limite @ " + str(precio) if precio else "Orden abierta",
        })
    return filas


def obtener_credenciales_drive():
    if GCP_SA_KEY:
        match = re.search(r"\{.*\}", GCP_SA_KEY, re.DOTALL)
        if match:
            info = json.loads(match.group(0))
            return Credentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/drive"]
            )
    if DRIVE_REFRESH_TOKEN:
        return OAuthCredentials(
            token=None,
            refresh_token=DRIVE_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=DRIVE_CLIENT_ID,
            client_secret=DRIVE_CLIENT_SECRET,
        )
    raise ValueError("No hay credenciales válidas de Google Drive.")


def precio_desde_ticker(tickers, base, activo_clean):
    candidatos = [
        base + "ZUSD", base + "USD",
        activo_clean + "USD", activo_clean + "ZUSD",
        "X" + activo_clean + "ZUSD", "X" + activo_clean + "USD",
    ]
    if base.startswith("XX"):
        base_reducido = base[1:]
        candidatos += [base_reducido + "USD", base_reducido + "ZUSD"]
    for key in candidatos:
        if key in tickers:
            return float(tickers[key]["c"][0])
    return 0.0


def resolver_file_id(service):
    if FILE_ID:
        try:
            meta = service.files().get(
                fileId=FILE_ID, fields="id, name, trashed"
            ).execute()
            if not meta.get("trashed"):
                print("Usando DRIVE_FILE_ID:", FILE_ID)
                return FILE_ID
            print("DRIVE_FILE_ID está en papelera, buscando otro...")
        except Exception as e:
            print("DRIVE_FILE_ID no válido:", e)

    query_parts = [
        f"name = '{NOMBRE_CSV}'",
        "trashed = false",
    ]
    if FOLDER_ID:
        query_parts.append(f"'{FOLDER_ID}' in parents")

    query = " and ".join(query_parts)
    results = (
        service.files()
        .list(
            q=query,
            fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=5,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        file_id = files[0]["id"]
        print(f"Encontrado {NOMBRE_CSV} en carpeta. ID:", file_id)
        return file_id

    results = (
        service.files()
        .list(
            q=f"name = '{NOMBRE_CSV}' and trashed = false",
            fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=5,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        file_id = files[0]["id"]
        print(f"Encontrado {NOMBRE_CSV} por nombre. ID:", file_id)
        return file_id

    print("No existe portfolio.csv. Se creará uno nuevo.")
    metadata = {"name": NOMBRE_CSV, "mimeType": "text/csv"}
    if FOLDER_ID:
        metadata["parents"] = [FOLDER_ID]
    created = service.files().create(body=metadata, fields="id").execute()
    file_id = created["id"]
    print("Creado nuevo portfolio.csv. ID:", file_id)
    return file_id


def cargar_notas_previas(service, file_id):
    notas = {}
    if not file_id:
        return notas
    try:
        request = service.files().get_media(fileId=file_id)
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)
        content = buf.getvalue()
        if not content.strip():
            return notas
        buf.seek(0)
        df_old = pd.read_csv(buf)
        if "notas" not in df_old.columns:
            return notas
        for _, row in df_old.iterrows():
            if str(row.get("seccion", "")) != "balance":
                continue
            tipo = str(row.get("tipo", "")).strip()
            activo = str(row.get("activo", "")).strip().upper()
            nota = str(row.get("notas", "") or "").strip()
            if nota and nota.lower() != "nan":
                notas[(tipo, activo)] = nota
        print(f"Notas previas recuperadas: {len(notas)}")
    except Exception as e:
        print("No se pudieron cargar notas previas:", e)
    return notas


def notas_desde_historial(historial):
    notas = {}
    compras = [h for h in historial if h.get("tipo") == "compra" and h.get("valor_usd", 0) >= 50]
    por_activo = {}
    for c in compras:
        por_activo.setdefault(c["activo"], []).append(c)

    for act, lista in por_activo.items():
        lista = sorted(lista, key=lambda x: x.get("fecha", ""), reverse=True)
        partes = [
            f"${c['valor_usd']:.0f} @ {c['precio']:.4g} ({c['fecha']})"
            for c in lista[:3]
        ]
        if partes:
            notas[act] = "Compras: " + " | ".join(partes)
    return notas


def actualizar_portfolio():
    print("Iniciando actualización de cartera...")
    balances = obtener_balances()
    if balances is None:
        return

    tickers = requests.get(
        "https://api.kraken.com/0/public/Ticker", timeout=30
    ).json().get("result", {})

    historial = obtener_historial_trades()
    notas_hist = notas_desde_historial(historial)

    filas = []
    total_usd = 0.0
    cantidad_spot = {}
    ahora = time.strftime("%Y-%m-%d %H:%M")

    for activo, cantidad in balances.items():
        qty = float(cantidad)
        if qty <= 0:
            continue

        base = activo
        if "." in activo:
            codigo_base, sufijo = activo.split(".", 1)
            if sufijo.upper() in SUFIJOS_EARN:
                continue
            base = codigo_base

        activo_clean = limpiar_codigo_activo(base)

        if activo_clean in FIAT:
            tipo_fila = "efectivo"
            precio = 1.0
            valor_usd = qty
        else:
            tipo_fila = "spot"
            precio = precio_desde_ticker(tickers, base, activo_clean)
            valor_usd = qty * precio
            cantidad_spot[activo_clean] = cantidad_spot.get(activo_clean, 0.0) + qty

        if valor_usd < MIN_VALOR_USD and activo_clean not in FIAT:
            continue

        total_usd += valor_usd
        filas.append({
            "seccion": "balance",
            "tipo": tipo_fila,
            "activo": activo_clean,
            "cantidad": qty,
            "precio": precio,
            "valor_usd": round(valor_usd, 4),
            "fecha": "",
            "notas": "",
        })

    for activo_clean, datos in obtener_earn().items():
        cantidad_earn = datos["cantidad"]
        valor_usd_earn = datos["valor_usd"]
        precio_earn = (valor_usd_earn / cantidad_earn) if cantidad_earn else 0.0

        qty_spot = cantidad_spot.get(activo_clean, 0.0)
        if qty_spot > 0 and abs(cantidad_earn - qty_spot) <= max(qty_spot * 0.001, 1e-8):
            filas.append({
                "seccion": "balance",
                "tipo": "earn_flex_ya_incluido_en_spot",
                "activo": activo_clean,
                "cantidad": cantidad_earn,
                "precio": precio_earn,
                "valor_usd": 0.0,
                "fecha": "",
                "notas": "Flex ya incluido en spot",
            })
            continue

        if valor_usd_earn < MIN_VALOR_USD:
            continue

        total_usd += valor_usd_earn
        filas.append({
            "seccion": "balance",
            "tipo": "earn",
            "activo": activo_clean,
            "cantidad": cantidad_earn,
            "precio": precio_earn,
            "valor_usd": round(valor_usd_earn, 4),
            "fecha": "",
            "notas": "Earn / staking",
        })

    creds = obtener_credenciales_drive()
    service = build("drive", "v3", credentials=creds)

    file_id = resolver_file_id(service)
    notas_previas = cargar_notas_previas(service, file_id)

    for row in filas:
        if row["seccion"] != "balance":
            continue
        key = (row["tipo"], row["activo"])
        nota_prev = notas_previas.get(key, "")
        nota_hist = notas_hist.get(row["activo"], "") if row["tipo"] in ("spot", "earn") else ""

        if nota_prev:
            row["notas"] = nota_prev
        elif nota_hist and row["tipo"] == "spot":
            row["notas"] = nota_hist
        elif row["tipo"] == "efectivo" and row["valor_usd"] < 100:
            row["notas"] = "Cash libre bajo"
        elif not row["notas"] and row["tipo"] == "earn":
            row["notas"] = "Earn / staking"

    filas.append({
        "seccion": "total",
        "tipo": "",
        "activo": "TOTAL CARTERA",
        "cantidad": "",
        "precio": "",
        "valor_usd": round(total_usd, 2),
        "fecha": ahora,
        "notas": f"Actualizado {ahora}",
    })

    filas.extend(historial)
    filas.extend(obtener_ordenes_abiertas())

    df = pd.DataFrame(
        filas,
        columns=["seccion", "tipo", "activo", "cantidad", "precio", "valor_usd", "fecha", "notas"],
    )
    df.to_csv(NOMBRE_CSV, index=False)
    print(f"{NOMBRE_CSV} generado. Total: ${total_usd:,.2f} | {ahora}")

    media = MediaFileUpload(NOMBRE_CSV, mimetype="text/csv")
    updated = service.files().update(fileId=file_id, media_body=media).execute()
    print("Actualizado en Google Drive. ID:", updated.get("id"))


if __name__ == "__main__":
    actualizar_portfolio()
