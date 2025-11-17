"""
Pequeño cliente CLI para MercadoLibre:
- Refresca el access_token usando refresh_token
- Obtiene un order_id y saca su shipping_id
- Descarga la etiqueta (ZPL) del envío

Requisitos:
1) Copia .env.example a .env y completa ML_CLIENT_ID, ML_CLIENT_SECRET y ML_REFRESH_TOKEN.
2) Opcional: ML_SELLER_ID si quieres filtrar en el futuro; no se usa en el flujo principal.
3) Ejecuta: python ml_api.py --order-id 2000010048750545 --save-label etiqueta.zpl

Seguro por defecto:
- Los secretos viven solo en .env (no lo subas a git).
- El script siempre pide un nuevo access_token (no guarda tokens en disco).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, Optional


ENV_PATH = ".env"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
ORDER_URL = "https://api.mercadolibre.com/orders/{order_id}"
LABEL_URL = (
    "https://api.mercadolibre.com/shipment_labels?shipment_ids={shipment_id}"
    "&response_type={response_type}"
)


def load_env(path: str = ENV_PATH) -> Dict[str, str]:
    """Parsea un .env simple KEY=VALUE."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No se encontró {path}. Copia .env.example a {path} y completa tus credenciales."
        )
    env: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            striped = line.strip()
            if not striped or striped.startswith("#"):
                continue
            if "=" not in striped:
                continue
            key, val = striped.split("=", 1)
            env[key.strip()] = val.strip().strip('"').strip("'")
    return env


def ensure_keys(env: Dict[str, str], keys: list[str]) -> None:
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise SystemExit(f"Faltan variables en .env: {', '.join(missing)}")


def http_request(
    method: str, url: str, headers: Optional[Dict[str, str]] = None, data: Optional[bytes] = None
) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} en {url} - cuerpo: {body}") from exc


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> Dict[str, str]:
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    raw = http_request("POST", TOKEN_URL, headers=headers, data=data)
    token_info = json.loads(raw.decode("utf-8"))
    if "access_token" not in token_info:
        raise RuntimeError(f"Respuesta inesperada al refrescar token: {token_info}")
    return token_info


def get_order(order_id: str, access_token: str) -> Dict:
    url = ORDER_URL.format(order_id=order_id)
    headers = {"Authorization": f"Bearer {access_token}"}
    raw = http_request("GET", url, headers=headers)
    return json.loads(raw.decode("utf-8"))


def extract_shipping_id(order: Dict) -> int:
    shipping = order.get("shipping") or {}
    shipping_id = shipping.get("id")
    if not shipping_id:
        raise RuntimeError("El order no tiene shipping_id disponible.")
    return shipping_id


def download_label(shipment_id: int, access_token: str, response_type: str = "zpl2") -> bytes:
    url = LABEL_URL.format(shipment_id=shipment_id, response_type=response_type)
    headers = {"Authorization": f"Bearer {access_token}"}
    return http_request("GET", url, headers=headers)


def is_zip(data: bytes) -> bool:
    return data.startswith(b"PK\x03\x04")


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cliente sencillo para etiquetas de MercadoLibre.")
    parser.add_argument(
        "--order-id",
        help="order_id de la venta (por ej. 2000010048750545). Obligatorio salvo --refresh-only.",
    )
    parser.add_argument(
        "--save-label",
        help="Ruta donde guardar la etiqueta ZPL. Si se omite, imprime por stdout.",
    )
    parser.add_argument(
        "--response-type",
        default="zpl2",
        choices=["zpl2", "pdf"],
        help="Formato de la etiqueta (default: zpl2).",
    )
    parser.add_argument(
        "--refresh-only",
        action="store_true",
        help="Solo refresca token y muestra tiempos; no consulta órdenes.",
    )
    parser.add_argument(
        "--save-to-downloads",
        action="store_true",
        help="Guarda el archivo en la carpeta Descargas del usuario.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)

    env = load_env()
    ensure_keys(env, ["ML_CLIENT_ID", "ML_CLIENT_SECRET", "ML_REFRESH_TOKEN"])

    token_info = refresh_access_token(
        env["ML_CLIENT_ID"], env["ML_CLIENT_SECRET"], env["ML_REFRESH_TOKEN"]
    )
    access_token = token_info["access_token"]
    expires_in = token_info.get("expires_in")

    print(f"Access token obtenido. Expira en ~{expires_in} segundos." if expires_in else "Token ok.")

    if args.refresh_only:
        return 0

    if not args.order_id:
        parser.error("--order-id es obligatorio a menos que uses --refresh-only.")

    order = get_order(args.order_id, access_token)
    shipping_id = extract_shipping_id(order)
    print(f"order_id: {args.order_id} -> shipping_id: {shipping_id}")

    label_bytes = download_label(shipping_id, access_token, response_type=args.response_type)

    if args.save_label:
        save_path = args.save_label
        if is_zip(label_bytes) and not save_path.lower().endswith(".zip"):
            save_path = f"{save_path}.zip"
            print(f"Respuesta es ZIP, guardando como {save_path}")
        target_path = save_path
        if args.save_to_downloads:
            downloads = Path.home() / "Downloads"
            downloads.mkdir(parents=True, exist_ok=True)
            target_path = os.path.join(downloads, os.path.basename(save_path))
        with open(target_path, "wb") as fh:
            fh.write(label_bytes)
        print(f"Etiqueta guardada en {target_path}")
    else:
        try:
            text = label_bytes.decode("utf-8")
            print(text)
        except UnicodeDecodeError:
            sys.stdout.buffer.write(label_bytes)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
