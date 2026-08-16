import time
import base64
import hashlib
import hmac
import urllib.parse
import requests
from typing import Dict, Any


class KrakenClient:
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.kraken.com"

    def _get_signature(self, urlpath: str, data: Dict[str, Any]) -> str:
        postdata = urllib.parse.urlencode(data)
        encoded = (str(data["nonce"]) + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()

        secret = base64.b64decode(self.api_secret)
        signature = hmac.new(secret, message, hashlib.sha512)
        return base64.b64encode(signature.digest()).decode()

    def _private_request(self, endpoint: str, data: Dict[str, Any] = None) -> Dict:
        if data is None:
            data = {}

        urlpath = f"/0/private/{endpoint}"
        data["nonce"] = str(int(time.time() * 1000))

        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._get_signature(urlpath, data),
        }

        response = requests.post(
            self.base_url + urlpath,
            headers=headers,
            data=data,
            timeout=30
        )
        result = response.json()

        if result.get("error"):
            raise Exception(f"Kraken API Error: {result['error']}")

        return result.get("result", {})

    def get_balance(self) -> Dict[str, str]:
        """Obtiene todos los saldos (incluye staking con sufijos .S, .F, .B, etc.)"""
        return self._private_request("Balance")

    def get_earn_allocations(self) -> Dict:
        """Obtiene las asignaciones actuales en Earn/Staking"""
        try:
            return self._private_request("Earn/Allocations")
        except Exception:
            return {}

    def get_full_portfolio(self) -> Dict[str, Any]:
        """
        Devuelve la cartera completa organizada.
        Incluye saldos normales + Earn/Staking.
        """
        balances = self.get_balance()
        earn = self.get_earn_allocations()

        portfolio = {
            "balances": {},
            "earn": earn,
            "raw_balances": balances,
        }

        for asset, amount in balances.items():
            amount_float = float(amount)
            if amount_float == 0:
                continue

            # Separar activos en staking/earn
            if asset.endswith((".S", ".F", ".B", ".M")):
                base_asset = asset.rsplit(".", 1)[0]
                key = f"{base_asset} (Earn/Staking)"
            else:
                key = asset

            portfolio["balances"][key] = amount_float

        return portfolio
