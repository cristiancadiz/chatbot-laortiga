import os
import re
import html
import json
import hmac
import hashlib
import gc
import uuid
import time
from datetime import datetime
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from difflib import SequenceMatcher

import requests
import pytz
from dotenv import load_dotenv
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from werkzeug.middleware.proxy_fix import ProxyFix


APP_VERSION = "2026-08-31-V48-LAORTIGA-REGIONES-CHILE-FIX"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# ============================================================
# CONFIGURACION GENERAL
# ============================================================

TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")
NEGOCIO_NOMBRE = os.getenv("NEGOCIO_NOMBRE", "La Ortiga")
APP_BASE_URL = (
    os.getenv("APP_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or "https://chatbot-laortiga-hddw.onrender.com"
).rstrip("/")

EJECUTIVO_WHATSAPP = os.getenv("EJECUTIVO_WHATSAPP", "+56965879758")
DEFAULT_SHIPPING_PRICE = int(os.getenv("DEFAULT_SHIPPING_PRICE", "0"))
JUMPSELLER_SHIPPING_METHOD_NAME = os.getenv(
    "JUMPSELLER_SHIPPING_METHOD_NAME", "Despacho coordinado por WhatsApp"
)


def zona_local():
    return pytz.timezone(TIMEZONE)


def ahora_local():
    return datetime.now(zona_local())


def normalizar_texto(texto):
    texto = (texto or "").strip().lower()
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ü": "u", "ñ": "n"
    }
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    return texto


def clp(valor):
    try:
        return "$" + f"{int(round(float(valor))):,}".replace(",", ".")
    except Exception:
        return "$0"


# ============================================================
# OPENAI
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ============================================================
# TWILIO / WHATSAPP
# ============================================================

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886",
)

if TWILIO_WHATSAPP_FROM and not TWILIO_WHATSAPP_FROM.startswith("whatsapp:"):
    TWILIO_WHATSAPP_FROM = "whatsapp:" + TWILIO_WHATSAPP_FROM

twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    try:
        twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    except Exception as e:
        print("ERROR INICIALIZANDO TWILIO:", repr(e))


def telefono_sin_prefijo_twilio(telefono):
    """Convierte whatsapp:+569... a +569... para Jumpseller y otros servicios."""
    telefono = (telefono or "").strip()
    if telefono.startswith("whatsapp:"):
        telefono = telefono.split(":", 1)[1]
    if telefono and not telefono.startswith("+"):
        digitos = re.sub(r"\D", "", telefono)
        return "+" + digitos if digitos else ""
    return telefono


def enviar_mensaje_twilio(telefono, texto):
    """Envía mensajes proactivos, por ejemplo la confirmación de Mercado Pago."""
    if not twilio_client or not telefono:
        print("TWILIO ERROR: faltan credenciales o teléfono")
        return False

    destino = telefono.strip()
    if not destino.startswith("whatsapp:"):
        destino = "whatsapp:" + destino

    try:
        twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=destino,
            body=(texto or "")[:1600],
        )
        guardar_mensaje(destino, "assistant", texto)
        return True
    except Exception as e:
        print("TWILIO SEND ERROR:", repr(e))
        return False


# ============================================================
# GOOGLE SHEETS - LOG DE CONVERSACIONES Y VENTAS
# ============================================================

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Conversaciones")
GOOGLE_SHEET_SALES_TAB = os.getenv("GOOGLE_SHEET_SALES_TAB", "VentasBot")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEETS_LOCK = Lock()
SHEETS_READY = set()
SHEETS_DISABLED = False
SHEETS_LAST_ERROR = ""


def obtener_credentials_sheets():
    if not all([GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]):
        raise RuntimeError("Faltan credenciales Google Sheets")
    return Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SHEETS_SCOPES,
    )


def sheets_service():
    return build(
        "sheets", "v4", credentials=obtener_credentials_sheets(), cache_discovery=False
    )


def asegurar_pestana(nombre, encabezados):
    if not GOOGLE_SHEET_ID:
        return False
    clave = (GOOGLE_SHEET_ID, nombre)
    if clave in SHEETS_READY:
        return True

    with SHEETS_LOCK:
        if clave in SHEETS_READY:
            return True
        svc = sheets_service()
        meta = svc.spreadsheets().get(
            spreadsheetId=GOOGLE_SHEET_ID, fields="sheets.properties.title"
        ).execute()
        titulos = [s.get("properties", {}).get("title") for s in meta.get("sheets", [])]
        if nombre not in titulos:
            svc.spreadsheets().batchUpdate(
                spreadsheetId=GOOGLE_SHEET_ID,
                body={"requests": [{"addSheet": {"properties": {"title": nombre}}}]},
            ).execute()
        actual = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{nombre}'!A1:Z1",
        ).execute().get("values", [])
        if not actual:
            svc.spreadsheets().values().update(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"'{nombre}'!A1",
                valueInputOption="RAW",
                body={"values": [encabezados]},
            ).execute()
        SHEETS_READY.add(clave)
        return True


def append_sheet(nombre, encabezados, fila):
    global SHEETS_DISABLED, SHEETS_LAST_ERROR
    if not GOOGLE_SHEET_ID or SHEETS_DISABLED:
        return False
    try:
        asegurar_pestana(nombre, encabezados)
        svc = sheets_service()
        svc.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{nombre}'!A:Z",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [fila]},
        ).execute()
        return True
    except Exception as e:
        err = repr(e)
        # Si Google revocó/expiró el refresh token, no insistimos dos veces por
        # cada mensaje. El bot sigue funcionando con Jumpseller/Twilio y, al
        # actualizar GOOGLE_REFRESH_TOKEN en Render, el redeploy reactiva Sheets.
        if "invalid_grant" in err or "expired or revoked" in err.lower():
            SHEETS_DISABLED = True
            SHEETS_LAST_ERROR = err
            print("SHEETS DESACTIVADO TEMPORALMENTE: GOOGLE_REFRESH_TOKEN expirado o revocado")
        else:
            print("SHEETS APPEND ERROR:", err)
        return False


def guardar_mensaje(telefono, rol, mensaje):
    return append_sheet(
        GOOGLE_SHEET_TAB,
        ["FechaHora", "Canal", "Telefono", "Rol", "Mensaje"],
        [ahora_local().isoformat(), "whatsapp_twilio", telefono, rol, mensaje],
    )


def guardar_venta_evento(ref, telefono, estado, total, jumpseller_order_id="", payment_id="", detalle=""):
    return append_sheet(
        GOOGLE_SHEET_SALES_TAB,
        [
            "FechaHora", "Referencia", "Telefono", "Estado", "Total",
            "JumpsellerOrderID", "MercadoPagoPaymentID", "Detalle"
        ],
        [
            ahora_local().isoformat(), ref, telefono, estado, total,
            jumpseller_order_id, payment_id, detalle
        ],
    )


# ============================================================
# JUMPSELLER REST API
# ============================================================

JUMPSELLER_LOGIN = os.getenv("JUMPSELLER_LOGIN")
JUMPSELLER_AUTH_TOKEN = os.getenv("JUMPSELLER_AUTH_TOKEN")
JUMPSELLER_BASE = "https://api.jumpseller.com/v1"
PRODUCT_CACHE_SECONDS = int(os.getenv("PRODUCT_CACHE_SECONDS", "90"))
JS_CONNECT_TIMEOUT = float(os.getenv("JS_CONNECT_TIMEOUT", "4"))
JS_READ_TIMEOUT = float(os.getenv("JS_READ_TIMEOUT", "10"))
_PRODUCT_CACHE = {"ts": 0.0, "products": []}

# Reutiliza conexiones HTTP y evita abrir sockets nuevos en cada consulta.
JS_SESSION = requests.Session()


def jumpseller_auth():
    if not JUMPSELLER_LOGIN or not JUMPSELLER_AUTH_TOKEN:
        raise RuntimeError("Faltan JUMPSELLER_LOGIN/JUMPSELLER_AUTH_TOKEN")
    return (JUMPSELLER_LOGIN, JUMPSELLER_AUTH_TOKEN)


def js_request(method, path, **kwargs):
    url = f"{JUMPSELLER_BASE}{path}"
    kwargs.setdefault("timeout", (JS_CONNECT_TIMEOUT, JS_READ_TIMEOUT))
    r = JS_SESSION.request(method, url, auth=jumpseller_auth(), **kwargs)
    if not r.ok:
        raise RuntimeError(f"Jumpseller {method} {path}: {r.status_code} {r.text[:500]}")
    if not r.text.strip():
        return {}
    return r.json()


def _unwrap_product(item):
    return item.get("product", item) if isinstance(item, dict) else {}


def _producto_cache_liviano(p):
    """Conserva solo lo necesario para buscar/mostrar productos y reduce RAM en Render."""
    if not isinstance(p, dict):
        return {}
    out = {}
    for key in (
        "id", "name", "brand", "sku", "price", "price_with_taxes", "sale_price",
        "status", "available", "stock", "stock_quantity", "quantity", "stock_unlimited",
        "url", "storefront_url", "permalink"
    ):
        if key in p:
            out[key] = p.get(key)

    desc = str(p.get("description", "") or "")
    if desc:
        out["description"] = desc[:1600]

    # Solo una imagen para WhatsApp, no toda la galería.
    imgs = p.get("images") or []
    if imgs:
        out["images"] = imgs[:1]

    # Variantes resumidas: suficiente para stock/precio sin retener objetos grandes.
    vs = []
    for raw in (p.get("variants") or []):
        v = raw.get("variant", raw) if isinstance(raw, dict) else {}
        if not isinstance(v, dict):
            continue
        vv = {k: v.get(k) for k in ("id", "price", "stock", "stock_quantity", "quantity", "stock_unlimited", "weight") if k in v}
        if vv:
            vs.append({"variant": vv})
    if vs:
        out["variants"] = vs
    return out


def listar_productos(force=False):
    ahora = time.time()
    if not force and _PRODUCT_CACHE["products"] and ahora - _PRODUCT_CACHE["ts"] < PRODUCT_CACHE_SECONDS:
        return _PRODUCT_CACHE["products"]

    productos = []
    for pagina in range(1, 6):
        data = js_request("GET", "/products.json", params={"page": pagina, "limit": 100})
        items = data if isinstance(data, list) else data.get("products", [])
        lote = [_unwrap_product(x) for x in items]
        cantidad_lote = len(lote)
        productos.extend([_producto_cache_liviano(p) for p in lote if p])
        # Guardamos el tamaño ANTES de liberar lote. En V40 se eliminaba lote y
        # después se intentaba hacer len(lote), provocando UnboundLocalError.
        del data, items, lote
        if cantidad_lote < 100:
            break

    _PRODUCT_CACHE["ts"] = ahora
    _PRODUCT_CACHE["products"] = productos
    return productos


def producto_activo(p):
    if p.get("status") and str(p.get("status")).lower() not in {"available", "active", "enabled"}:
        return False
    if p.get("available") is False:
        return False
    return True


def _precio_float(valor):
    """Convierte un precio de Jumpseller a float. Devuelve 0 si no es un precio válido."""
    if valor in (None, ""):
        return 0.0
    try:
        if isinstance(valor, str):
            valor = valor.strip().replace("$", "").replace(" ", "")
            # Jumpseller normalmente entrega números con punto decimal. Si llega
            # un valor chileno con separador de miles, lo normalizamos también.
            if valor.count(",") == 1 and valor.count(".") >= 1:
                valor = valor.replace(".", "").replace(",", ".")
            elif valor.count(",") == 1 and "." not in valor:
                valor = valor.replace(",", ".")
        return float(valor)
    except Exception:
        return 0.0


def _precio_padre_positivo(p):
    """Obtiene solo un precio padre estrictamente mayor a cero."""
    for key in ("price", "price_with_taxes", "sale_price"):
        precio = _precio_float(p.get(key))
        if precio > 0:
            return precio
    return 0.0


def precio_variante(p, v):
    """Precio efectivo de una variante; nunca devuelve un precio negativo."""
    precio_v = _precio_float((v or {}).get("price"))
    if precio_v > 0:
        return precio_v
    # Si la variante no trae precio propio, puede heredar el precio del producto.
    # Pero un precio explícitamente 0 NO se considera vendible.
    if (v or {}).get("price") in (None, ""):
        return _precio_padre_positivo(p)
    return 0.0


def precio_producto(p):
    """Devuelve el precio de una opción realmente comprable, siempre > 0 o 0."""
    variantes = variantes_producto(p)
    if variantes:
        for v in variantes:
            st = stock_variante(v)
            precio = precio_variante(p, v)
            if precio > 0 and (st is None or st > 0):
                return precio
        return 0.0
    return _precio_padre_positivo(p)



def variantes_producto(p):
    """Normaliza la lista de variantes devuelta por Jumpseller."""
    out = []
    for raw in (p.get("variants") or []):
        if not isinstance(raw, dict):
            continue
        v = raw.get("variant", raw)
        if isinstance(v, dict) and v.get("id") is not None:
            out.append(v)
    return out


def stock_variante(v):
    if not isinstance(v, dict):
        return 0
    if v.get("stock_unlimited") is True:
        return None
    for key in ("stock", "stock_quantity", "quantity"):
        if v.get(key) is not None:
            try:
                return max(0, int(float(v.get(key))))
            except Exception:
                pass
    return None


def variante_para_compra(p, qty=1):
    """Devuelve solo una variante con precio > 0 y stock suficiente."""
    variantes = variantes_producto(p)
    if not variantes:
        return None

    qty = max(1, int(qty))
    for v in variantes:
        st = stock_variante(v)
        precio = precio_variante(p, v)
        if precio > 0 and (st is None or st >= qty):
            return v
    return None


def stock_producto(p):
    """Stock vendible: cuenta únicamente variantes que además tengan precio válido."""
    variantes = variantes_producto(p)
    if variantes:
        total = 0
        hubo_control_stock = False
        for v in variantes:
            precio = precio_variante(p, v)
            if precio <= 0:
                continue
            st = stock_variante(v)
            if st is None:
                return None
            hubo_control_stock = True
            total += max(0, st)
        if hubo_control_stock:
            return total
        return 0

    if _precio_padre_positivo(p) <= 0:
        return 0
    if p.get("stock_unlimited") is True:
        return None
    for key in ("stock", "stock_quantity", "quantity"):
        if p.get(key) is not None:
            try:
                return max(0, int(float(p.get(key))))
            except Exception:
                pass
    return None


def producto_vendible(p):
    """Filtro estricto: activo + precio > 0 + stock real disponible."""
    if not producto_activo(p):
        return False

    variantes = variantes_producto(p)
    if variantes:
        # Debe existir al menos una variante que pueda comprarse hoy.
        return variante_para_compra(p, 1) is not None

    precio = _precio_padre_positivo(p)
    if precio <= 0:
        return False
    stock = stock_producto(p)
    return stock is None or stock > 0


def url_producto(p):
    return p.get("url") or p.get("storefront_url") or p.get("permalink") or ""


def buscar_productos(query, limite=5):
    q = normalizar_texto(query)
    tokens = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) >= 2]
    productos = listar_productos()
    scored = []
    descartados_precio_stock = 0
    for p in productos:
        if not producto_vendible(p):
            descartados_precio_stock += 1
            continue
        texto = normalizar_texto(" ".join([
            str(p.get("name", "")), str(p.get("description", "")),
            str(p.get("brand", "")), str(p.get("sku", "")),
        ]))
        score = sum(3 if t in normalizar_texto(str(p.get("name", ""))) else 1 for t in tokens if t in texto)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("name", ""))))
    resultados = [p for _, p in scored[:limite]]
    print("JUMPSELLER BUSQUEDA:", query, "resultados_vendibles=", len(resultados), "descartados=", descartados_precio_stock)
    return resultados


def obtener_producto_por_id(product_id, force=True):
    try:
        data = js_request("GET", f"/products/{int(product_id)}.json")
        return _unwrap_product(data)
    except Exception:
        if force:
            for p in listar_productos(force=True):
                if str(p.get("id")) == str(product_id):
                    return p
        return None


def _extraer_clientes(data):
    items = data if isinstance(data, list) else data.get("customers", [])
    clientes = []
    for raw in items:
        c = raw.get("customer", raw) if isinstance(raw, dict) else {}
        if isinstance(c, dict) and c:
            clientes.append(c)
    return clientes


def buscar_cliente_por_email(email):
    objetivo = normalizar_texto(email)

    # Intento rápido usando el filtro de Jumpseller.
    try:
        data = js_request("GET", "/customers.json", params={"email": email, "limit": 100})
        for c in _extraer_clientes(data):
            if normalizar_texto(c.get("email")) == objetivo:
                return c
    except Exception as e:
        print("JUMPSELLER BUSQUEDA CLIENTE POR EMAIL FALLÓ:", repr(e))

    # Fallback robusto: recorre clientes hasta encontrar el correo.
    # Esto evita intentar crear un correo que Jumpseller ya tiene registrado.
    for pagina in range(1, 101):
        data = js_request("GET", "/customers.json", params={"page": pagina, "limit": 100})
        clientes = _extraer_clientes(data)
        for c in clientes:
            if normalizar_texto(c.get("email")) == objetivo:
                return c
        if len(clientes) < 100:
            break
    return None


def buscar_o_crear_cliente(email, nombre, telefono, direccion="", comuna=""):
    existente = buscar_cliente_por_email(email)
    if existente:
        print("JUMPSELLER CLIENTE EXISTENTE:", existente.get("id"), email)
        return existente

    customer = {
        "email": email,
        "fullname": nombre,
        "phone": telefono,
        "status": "approved",
    }
    if direccion:
        partes = nombre.strip().split(" ", 1)
        customer["shipping_address"] = {
            "name": partes[0] if partes else nombre,
            "surname": partes[1] if len(partes) > 1 else "",
            "address": direccion,
            "city": comuna or "Santiago",
            "municipality": comuna or "Santiago",
            "country": "CL",
        }

    try:
        data = js_request("POST", "/customers.json", json={"customer": customer})
        return data.get("customer", data)
    except RuntimeError as e:
        # Protección contra carrera/inconsistencia del filtro de Jumpseller:
        # si el correo ya existía, lo recuperamos y continuamos la compra.
        msg = str(e).lower()
        if "correo electrónico ya está registrado" in msg or "email" in msg and "registr" in msg:
            existente = buscar_cliente_por_email(email)
            if existente:
                print("JUMPSELLER CLIENTE RECUPERADO TRAS DUPLICADO:", existente.get("id"), email)
                return existente
        raise



def _unwrap_shipping_method(item):
    return item.get("shipping_method", item) if isinstance(item, dict) else {}


def _unwrap_geo_item(item, root_key):
    if not isinstance(item, dict):
        return {}
    return item.get(root_key, item) if isinstance(item.get(root_key, item), dict) else item


def _lista_desde_respuesta(data, key):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        val = data.get(key)
        if isinstance(val, list):
            return val
    return []


GEO_CACHE_SECONDS = int(os.getenv("GEO_CACHE_SECONDS", "86400"))
GEO_CACHE_MAX = int(os.getenv("GEO_CACHE_MAX", "120"))
_GEO_CACHE = {}


def resolver_region_comuna_jumpseller(comuna, max_seconds=12.0, permitir_fuzzy=True):
    """Resuelve una comuna chilena contra la geografía de Jumpseller.

    V47 mantiene la búsqueda paralela de V45/V46 y agrega corrección tolerante
    de errores de escritura. Ej.: "vina del amr" puede resolverse como
    "Viña del Mar", pero textos arbitrarios no se aceptan como comunas.
    """
    objetivo = normalizar_texto(comuna)
    if not objetivo:
        return {"region_code": "", "region_name": "", "municipality_code": "", "municipality_name": comuna or "", "match_score": 0.0}

    ahora = time.time()
    cache_key = (objetivo, bool(permitir_fuzzy))
    cache = _GEO_CACHE.get(cache_key)
    if cache and ahora - cache.get("ts", 0) < GEO_CACHE_SECONDS:
        return dict(cache["geo"])

    vacio = {"region_code": "", "region_name": "", "municipality_code": "", "municipality_name": comuna, "match_score": 0.0}
    inicio = time.monotonic()

    try:
        data = js_request("GET", "/countries/CL/regions.json", timeout=(3, 6))
        regiones = _lista_desde_respuesta(data, "regions")
    except Exception as e:
        print("JUMPSELLER REGIONES ERROR:", repr(e))
        return vacio

    regiones_limpias = []
    for raw_region in regiones:
        r = _unwrap_geo_item(raw_region, "region")
        region_code = str(r.get("code") or r.get("id") or r.get("region_code") or "")
        region_name = str(r.get("name") or r.get("region") or "")
        if region_code:
            regiones_limpias.append((region_code, region_name))

    def consultar_region(info):
        region_code, region_name = info
        mejor = None
        mejor_score = 0.0
        try:
            url = f"{JUMPSELLER_BASE}/countries/CL/regions/{region_code}/municipalities.json"
            r = requests.get(url, auth=jumpseller_auth(), timeout=(2.5, 6))
            if not r.ok:
                return None
            mdata = r.json() if r.text.strip() else {}
            municipios = _lista_desde_respuesta(mdata, "municipalities")
            for raw_m in municipios:
                m = _unwrap_geo_item(raw_m, "municipality")
                m_name = str(m.get("name") or m.get("municipality") or m.get("label") or "")
                m_code = str(m.get("code") or m.get("id") or m.get("municipality_code") or "")
                m_norm = normalizar_texto(m_name)

                if m_norm == objetivo or (m_code and normalizar_texto(m_code) == objetivo):
                    return {
                        "region_code": region_code,
                        "region_name": region_name,
                        "municipality_code": m_code,
                        "municipality_name": m_name or comuna,
                        "match_score": 1.0,
                        "match_type": "exact",
                    }

                if permitir_fuzzy and m_norm:
                    score = SequenceMatcher(None, objetivo, m_norm).ratio()
                    # Refuerzo por mismas palabras aunque estén transpuestas/tecleadas mal.
                    toks_obj = set(re.findall(r"[a-z0-9]+", objetivo))
                    toks_m = set(re.findall(r"[a-z0-9]+", m_norm))
                    if toks_obj and toks_m:
                        overlap = len(toks_obj & toks_m) / max(len(toks_obj), len(toks_m))
                        score = max(score, overlap * 0.92)
                    if score > mejor_score:
                        mejor_score = score
                        mejor = {
                            "region_code": region_code,
                            "region_name": region_name,
                            "municipality_code": m_code,
                            "municipality_name": m_name or comuna,
                            "match_score": round(score, 4),
                            "match_type": "fuzzy",
                        }
        except Exception as e:
            print("JUMPSELLER MUNICIPIOS ERROR", region_code, repr(e))
        return mejor

    executor = ThreadPoolExecutor(max_workers=min(8, max(1, len(regiones_limpias))))
    futures = [executor.submit(consultar_region, info) for info in regiones_limpias]
    mejores = []
    try:
        restante = max(2.0, float(max_seconds) - (time.monotonic() - inicio))
        for future in as_completed(futures, timeout=restante):
            geo = future.result()
            if not geo:
                continue
            if geo.get("match_score", 0) >= 0.999:
                elegido = geo
                _GEO_CACHE[cache_key] = {"ts": ahora, "geo": elegido}
                if len(_GEO_CACHE) > GEO_CACHE_MAX:
                    oldest = min(_GEO_CACHE, key=lambda k: _GEO_CACHE[k].get("ts", 0))
                    _GEO_CACHE.pop(oldest, None)
                print("JUMPSELLER GEO RESUELTO:", comuna, "->", elegido.get("municipality_name"), "/", elegido.get("region_name"), elegido.get("region_code"), "exacto tiempo=", round(time.monotonic() - inicio, 2), "s")
                executor.shutdown(wait=False, cancel_futures=True)
                return elegido
            mejores.append(geo)
    except FuturesTimeoutError:
        print("JUMPSELLER GEO TIMEOUT: no alcanzó a resolver", comuna, "en", max_seconds, "s")
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    if permitir_fuzzy and mejores:
        mejores.sort(key=lambda x: float(x.get("match_score", 0)), reverse=True)
        mejor = mejores[0]
        score = float(mejor.get("match_score", 0))
        # 0.78 permite errores como "vina del amr" pero evita convertir frases
        # como "quiero pedir" o "despacho" en una comuna accidentalmente.
        if score >= 0.78:
            _GEO_CACHE[cache_key] = {"ts": ahora, "geo": mejor}
            if len(_GEO_CACHE) > GEO_CACHE_MAX:
                oldest = min(_GEO_CACHE, key=lambda k: _GEO_CACHE[k].get("ts", 0))
                _GEO_CACHE.pop(oldest, None)
            print("JUMPSELLER GEO CORREGIDO V47:", comuna, "->", mejor.get("municipality_name"), "/", mejor.get("region_name"), "score=", round(score, 3))
            return mejor
        print("JUMPSELLER GEO NO RECONOCIDO V47:", comuna, "mejor=", mejor.get("municipality_name"), "score=", round(score, 3))

    return vacio

def peso_carrito_jumpseller(carrito):
    total = 0.0
    for item in carrito:
        p = obtener_producto_por_id(item["product_id"])
        if not p:
            continue
        peso = p.get("weight")
        # Algunas respuestas pueden exponer el peso en la variante.
        if item.get("variant_id"):
            for v in variantes_producto(p):
                if str(v.get("id")) == str(item.get("variant_id")) and v.get("weight") not in (None, ""):
                    peso = v.get("weight")
                    break
        try:
            total += float(peso or 0) * int(item["qty"])
        except Exception:
            pass
    return total


def _valor_texto_geo(valor):
    """Convierte códigos/nombres geográficos de Jumpseller a texto comparable."""
    if valor is None:
        return ""
    if isinstance(valor, dict):
        for key in ("code", "id", "name", "label", "value", "slug"):
            if valor.get(key) not in (None, ""):
                return _valor_texto_geo(valor.get(key))
        return ""
    if isinstance(valor, (list, tuple)):
        for item in valor:
            txt = _valor_texto_geo(item)
            if txt:
                return txt
        return ""
    return str(valor)


def _valor_base_tabla(basedon, carrito):
    """Calcula la base de una Tabla de Tarifas aceptando variantes del nombre del campo."""
    b = re.sub(r"[^a-z0-9]+", "", normalizar_texto(_valor_texto_geo(basedon)))
    if b in {"price", "orderprice", "subtotal", "total", "amount"}:
        return float(total_carrito(carrito))
    if b in {"quantity", "qty", "items", "item", "numberofitems", "products", "productquantity"}:
        return float(sum(int(x.get("qty", 0)) for x in carrito))
    if b in {"weight", "peso"}:
        return float(peso_carrito_jumpseller(carrito))
    return None


def _numero_seguro(valor):
    if valor in (None, ""):
        return None
    try:
        if isinstance(valor, str):
            valor = valor.strip().replace("$", "").replace(" ", "")
            if valor.count(",") == 1 and valor.count(".") >= 1:
                valor = valor.replace(".", "").replace(",", ".")
            elif valor.count(",") == 1 and "." not in valor:
                valor = valor.replace(",", ".")
        return float(valor)
    except Exception:
        return None


def _precio_regla_tabla(values, base):
    """Obtiene el precio del rango configurado en la Tabla de Tarifas.

    V46 acepta tanto el formato value->{amount,price} como variantes planas
    que algunas respuestas de Jumpseller exponen para tablas de tarifas.
    """
    reglas = []
    for raw in values or []:
        v = raw.get("value", raw) if isinstance(raw, dict) else {}
        if not isinstance(v, dict):
            continue

        price = None
        for key in ("price", "cost", "rate", "shipping_price", "value_price"):
            price = _numero_seguro(v.get(key))
            if price is not None:
                break
        if price is None:
            continue

        amount = None
        for key in ("amount", "max", "to", "upper", "upper_limit", "limit", "max_value"):
            amount = _numero_seguro(v.get(key))
            if amount is not None:
                break

        # Una única tarifa sin límite explícito se trata como rango abierto.
        if amount is None:
            amount = float("inf")
        reglas.append((amount, max(0.0, price)))

    if not reglas:
        return None
    reglas.sort(key=lambda x: x[0])
    for amount, price in reglas:
        if base <= amount:
            return price
    return reglas[-1][1]


def _region_aliases_chile(nombre, codigo=""):
    """Genera alias tolerantes para regiones chilenas (nombre, número y romano)."""
    n = normalizar_texto(_valor_texto_geo(nombre))
    c = normalizar_texto(_valor_texto_geo(codigo))
    aliases = {x for x in (n, c) if x}
    if n:
        aliases.add(n.replace("region de ", "").replace("region del ", "").strip())
        aliases.add(n.replace("region ", "").strip())

    mapa = {
        "arica y parinacota": (15, "xv"),
        "tarapaca": (1, "i"),
        "antofagasta": (2, "ii"),
        "atacama": (3, "iii"),
        "coquimbo": (4, "iv"),
        "valparaiso": (5, "v"),
        "metropolitana de santiago": (13, "xiii"),
        "metropolitana": (13, "xiii"),
        "ohiggins": (6, "vi"),
        "libertador general bernardo ohiggins": (6, "vi"),
        "maule": (7, "vii"),
        "nuble": (16, "xvi"),
        "biobio": (8, "viii"),
        "la araucania": (9, "ix"),
        "araucania": (9, "ix"),
        "los rios": (14, "xiv"),
        "los lagos": (10, "x"),
        "aysen": (11, "xi"),
        "aysen del general carlos ibanez del campo": (11, "xi"),
        "magallanes y de la antartica chilena": (12, "xii"),
        "magallanes": (12, "xii"),
    }
    base = n.replace("region de ", "").replace("region del ", "").replace("region ", "").strip()
    for key, (num, romano) in mapa.items():
        if base == key or key in base or base in key:
            aliases.update({str(num), f"{num:02d}", romano, f"region {romano}", f"{romano} region"})
            break
    return {normalizar_texto(a) for a in aliases if a}


def _coincide_geo(valor, aliases):
    txt = normalizar_texto(_valor_texto_geo(valor))
    if not txt:
        return False
    if txt in aliases:
        return True
    limpio = re.sub(r"\b(region|de|del|la|el)\b", " ", txt)
    limpio = re.sub(r"\s+", " ", limpio).strip()
    for a in aliases:
        aa = re.sub(r"\b(region|de|del|la|el)\b", " ", a)
        aa = re.sub(r"\s+", " ", aa).strip()
        if limpio and aa and (limpio == aa or (len(limpio) >= 5 and limpio in aa) or (len(aa) >= 5 and aa in limpio)):
            return True
    return False


def _variantes_region_chile(valor):
    """Genera variantes equivalentes para comparar regiones chilenas de forma robusta."""
    n = normalizar_texto(valor)
    if not n:
        return set()
    out = {n}
    # Limpieza de prefijos/sufijos comunes
    limpio = re.sub(r"\b(region|region de|reg de|region del|region de la)\b", " " , n)
    limpio = re.sub(r"\s+", " ", limpio).strip()
    if limpio:
        out.add(limpio)

    aliases = {
        "arica y parinacota": {"15", "xv", "region xv", "region 15"},
        "tarapaca": {"01", "1", "i", "region i", "region 1"},
        "antofagasta": {"02", "2", "ii", "region ii", "region 2"},
        "atacama": {"03", "3", "iii", "region iii", "region 3"},
        "coquimbo": {"04", "4", "iv", "region iv", "region 4"},
        "valparaiso": {"05", "5", "v", "region v", "region 5"},
        "metropolitana de santiago": {"13", "rm", "region metropolitana", "santiago", "region 13"},
        "metropolitana": {"13", "rm", "region metropolitana", "santiago", "region 13"},
        "libertador general bernardo ohiggins": {"06", "6", "vi", "ohiggins", "region vi", "region 6"},
        "ohiggins": {"06", "6", "vi", "libertador general bernardo ohiggins", "region vi", "region 6"},
        "maule": {"07", "7", "vii", "region vii", "region 7"},
        "nuble": {"16", "xvi", "region xvi", "region 16"},
        "biobio": {"08", "8", "viii", "bio bio", "region viii", "region 8"},
        "la araucania": {"09", "9", "ix", "araucania", "region ix", "region 9"},
        "araucania": {"09", "9", "ix", "la araucania", "region ix", "region 9"},
        "los rios": {"14", "xiv", "region xiv", "region 14"},
        "los lagos": {"10", "x", "region x", "region 10"},
        "aysen del general carlos ibanez del campo": {"11", "xi", "aysen", "region xi", "region 11"},
        "aysen": {"11", "xi", "aysen del general carlos ibanez del campo", "region xi", "region 11"},
        "magallanes y de la antartica chilena": {"12", "xii", "magallanes", "region xii", "region 12"},
        "magallanes": {"12", "xii", "magallanes y de la antartica chilena", "region xii", "region 12"},
    }
    for canon, vals in aliases.items():
        canon_n = normalizar_texto(canon)
        vals_n = {normalizar_texto(x) for x in vals}
        if n == canon_n or n in vals_n or limpio == canon_n or limpio in vals_n:
            out.add(canon_n)
            out.update(vals_n)
    return out


def _texto_geo_recursivo(valor):
    """Aplana estructuras geográficas de Jumpseller para matching tolerante."""
    partes = []
    if valor is None:
        return ""
    if isinstance(valor, dict):
        for k, v in valor.items():
            if k in {"location", "country", "country_code", "region", "region_code", "state", "municipality", "municipality_code", "city", "commune", "comuna", "name", "label", "code", "id"}:
                partes.append(_texto_geo_recursivo(v))
    elif isinstance(valor, (list, tuple, set)):
        for v in valor:
            partes.append(_texto_geo_recursivo(v))
    else:
        partes.append(str(valor))
    return " ".join(x for x in partes if x).strip()


def _puntaje_ubicacion_tabla(locations, comuna, geo=None):
    """Puntúa país/región/comuna con tolerancia a formatos distintos de Jumpseller."""
    if not locations:
        return 1

    geo = geo or {}
    comuna_aliases = {
        normalizar_texto(comuna),
        normalizar_texto(geo.get("municipality_name")),
        normalizar_texto(geo.get("municipality_code")),
    }
    comuna_aliases.discard("")

    region_aliases = set()
    region_aliases.update(_region_aliases_chile(geo.get("region_name"), geo.get("region_code")))
    region_aliases.update(_variantes_region_chile(geo.get("region_name")))
    region_aliases.update(_variantes_region_chile(geo.get("region_code")))
    region_aliases.discard("")

    mejor = -1
    for raw in locations:
        # Algunas tiendas devuelven location como string/código, no dict.
        loc = raw.get("location", raw) if isinstance(raw, dict) else raw

        if isinstance(loc, dict):
            country = _valor_texto_geo(loc.get("country") or loc.get("country_code"))
            region = loc.get("region") or loc.get("region_code") or loc.get("state")
            municipality = (
                loc.get("municipality") or loc.get("municipality_code") or
                loc.get("city") or loc.get("commune") or loc.get("comuna")
            )

            country_norm = normalizar_texto(country)
            if country_norm and country_norm not in {"cl", "chile"}:
                continue

            municipio_ok = False
            if municipality not in (None, ""):
                municipio_ok = _coincide_geo(municipality, comuna_aliases)
                if not municipio_ok:
                    continue

            region_ok = False
            if region not in (None, ""):
                region_txt = normalizar_texto(_valor_texto_geo(region))
                loc_region_aliases = _variantes_region_chile(region_txt) | {region_txt}
                region_ok = bool(region_aliases & loc_region_aliases) or _coincide_geo(region, region_aliases)
                if region_aliases and not region_ok:
                    continue
                if not region_aliases and not municipio_ok:
                    continue

            score = 10 if country_norm else 0
            score += 20 if region not in (None, "") else 0
            score += 30 if municipality not in (None, "") else 0
            mejor = max(mejor, score or 1)
            continue

        # Fallback para strings/códigos simples dentro de locations.
        txt = normalizar_texto(_texto_geo_recursivo(loc))
        if not txt:
            continue
        if _coincide_geo(txt, comuna_aliases):
            mejor = max(mejor, 30)
            continue
        loc_aliases = _variantes_region_chile(txt) | {txt}
        if region_aliases and (region_aliases & loc_aliases or _coincide_geo(txt, region_aliases)):
            mejor = max(mejor, 20)
            continue
        if txt in {"cl", "chile"}:
            mejor = max(mejor, 10)

    return mejor

def _tablas_metodo(metodo):
    tablas = metodo.get("tables") or metodo.get("shipping_tables") or metodo.get("rates") or []
    if isinstance(tablas, dict):
        tablas = tablas.get("tables") or tablas.get("shipping_tables") or tablas.get("rates") or [tablas]
    return tablas if isinstance(tablas, list) else []


def _locations_tabla(table):
    locs = table.get("locations") or table.get("destinations") or table.get("zones") or []
    if isinstance(locs, dict):
        locs = locs.get("locations") or locs.get("destinations") or locs.get("zones") or [locs]
    return locs if isinstance(locs, list) else []


def _values_tabla(table):
    vals = table.get("values") or table.get("rates") or table.get("prices") or []
    if isinstance(vals, dict):
        vals = vals.get("values") or vals.get("rates") or vals.get("prices") or [vals]
    return vals if isinstance(vals, list) else []


def cotizar_despacho_jumpseller(carrito, comuna):
    """Cotiza despacho desde las Tablas de Tarifas activas de Jumpseller.

    V48 refuerza el matching regional de Chile y acepta códigos, nombres, romanos
    y estructuras variables de locations devueltas por Jumpseller.
    """
    inicio = time.monotonic()
    data = js_request("GET", "/shipping_methods.json", timeout=(3, 7))
    items = data if isinstance(data, list) else data.get("shipping_methods", [])
    metodos = [_unwrap_shipping_method(x) for x in items]

    tipos_tabla = {"tables", "table", "table rates", "table_rates", "tablerates"}
    metodos = [
        m for m in metodos
        if m and m.get("enabled") is not False
        and normalizar_texto(_valor_texto_geo(m.get("type"))) in tipos_tabla
    ]
    if not metodos:
        raise RuntimeError("No hay una Tabla de Tarifas activa en Jumpseller.")

    def construir_candidatos(geo):
        candidatos = []
        for metodo in metodos:
            for raw_table in _tablas_metodo(metodo):
                table = raw_table.get("table", raw_table) if isinstance(raw_table, dict) else {}
                if not isinstance(table, dict):
                    continue

                basedon = table.get("basedon")
                if basedon in (None, ""):
                    basedon = table.get("based_on") or table.get("basis") or table.get("type")
                base = _valor_base_tabla(basedon, carrito)
                if base is None:
                    continue

                score = _puntaje_ubicacion_tabla(_locations_tabla(table), comuna, geo)
                if score < 0:
                    continue

                price = _precio_regla_tabla(_values_tabla(table), base)
                if price is None:
                    continue

                candidatos.append({
                    "shipping_method_id": metodo.get("id"),
                    "shipping_method_name": metodo.get("name") or "Despacho",
                    "shipping_price": float(price),
                    "score": score,
                    "basedon": basedon,
                    "base": base,
                    "region_code": (geo or {}).get("region_code", ""),
                    "region_name": (geo or {}).get("region_name", ""),
                    "municipality_code": (geo or {}).get("municipality_code", ""),
                })
        return candidatos

    candidatos = construir_candidatos({})
    mejor_directo = max((x["score"] for x in candidatos), default=-1)

    # Para tablas regionales resolvemos la comuna contra la geografía oficial de Jumpseller.
    if mejor_directo < 30:
        hay_tablas_regionales = False
        for metodo in metodos:
            for raw_table in _tablas_metodo(metodo):
                table = raw_table.get("table", raw_table) if isinstance(raw_table, dict) else {}
                if not isinstance(table, dict):
                    continue
                for raw_loc in _locations_tabla(table):
                    loc = raw_loc.get("location", raw_loc) if isinstance(raw_loc, dict) else {}
                    if isinstance(loc, dict) and any(loc.get(k) not in (None, "") for k in ("region", "region_code", "state")):
                        hay_tablas_regionales = True
                        break
                if hay_tablas_regionales:
                    break
            if hay_tablas_regionales:
                break

        if hay_tablas_regionales:
            geo = resolver_region_comuna_jumpseller(comuna, max_seconds=12)
            if geo.get("region_code") or geo.get("region_name"):
                candidatos_geo = construir_candidatos(geo)
                if candidatos_geo:
                    candidatos = candidatos_geo

    if not candidatos:
        # Log pequeño pero útil: permite ver la forma REAL que devuelve la tienda
        # sin imprimir toda la respuesta ni credenciales.
        resumen = []
        for metodo in metodos[:5]:
            tablas = _tablas_metodo(metodo)
            resumen.append({
                "metodo": metodo.get("name"),
                "type": metodo.get("type"),
                "tablas": len(tablas),
                "table_keys": list((tablas[0].get("table", tablas[0]) if tablas and isinstance(tablas[0], dict) else {}).keys())[:12] if tablas else [],
                "locations_sample": (_locations_tabla(tablas[0].get("table", tablas[0]))[:3] if tablas and isinstance(tablas[0], dict) else []),
            })
        print("JUMPSELLER TARIFAS DEBUG V48:", resumen)

        # Solo usa fallback si el administrador configuró explícitamente un precio > 0.
        if DEFAULT_SHIPPING_PRICE > 0:
            print("JUMPSELLER TARIFA FALLBACK CONFIGURADA:", DEFAULT_SHIPPING_PRICE)
            return {
                "shipping_method_id": None,
                "shipping_method_name": JUMPSELLER_SHIPPING_METHOD_NAME,
                "shipping_price": float(DEFAULT_SHIPPING_PRICE),
                "score": 0,
                "basedon": "fallback",
                "base": 0,
                "region_code": "",
                "region_name": "",
                "municipality_code": "",
            }
        raise RuntimeError(f"No encontré una tarifa de despacho aplicable para la comuna {comuna}.")

    print("JUMPSELLER TARIFAS CANDIDATAS:", [
        {"metodo": x.get("shipping_method_name"), "precio": x.get("shipping_price"),
         "score": x.get("score"), "region": x.get("region_name") or x.get("region_code")}
        for x in candidatos
    ])

    max_score = max(x["score"] for x in candidatos)
    especificos = [x for x in candidatos if x["score"] == max_score]
    elegido = min(especificos, key=lambda x: x["shipping_price"])
    print("JUMPSELLER TARIFA ELEGIDA:", elegido, "tiempo=", round(time.monotonic() - inicio, 2), "s")

    del data, items, metodos, candidatos, especificos
    gc.collect()
    return elegido


def crear_pedido_jumpseller(customer_id, carrito, tipo_entrega, direccion, comuna, tarifa_envio=None):
    products = []
    for item in carrito:
        linea = {
            "id": int(item["product_id"]),
            "qty": int(item["qty"]),
            "price": float(item["unit_price"]),
        }
        if item.get("variant_id"):
            linea["variant_id"] = int(item["variant_id"])
        products.append(linea)

    if tipo_entrega == "despacho":
        if not tarifa_envio:
            tarifa_envio = cotizar_despacho_jumpseller(carrito, comuna)
        envio = float(tarifa_envio["shipping_price"])
        metodo_nombre = tarifa_envio.get("shipping_method_name") or JUMPSELLER_SHIPPING_METHOD_NAME
    else:
        envio = 0.0
        metodo_nombre = "Retiro coordinado"

    order = {
        "status": "Pending Payment",
        # Enviamos el nombre + precio calculado desde la tabla. Según la API de
        # Jumpseller, shipping_price aplica cuando se entrega shipping_method_name.
        "shipping_method_name": metodo_nombre,
        "shipping_price": envio,
        "shipping_required": tipo_entrega == "despacho",
        "customer": {"id": int(customer_id)},
        "products": products,
    }
    data = js_request("POST", "/orders.json", json={"order": order})
    return data.get("order", data)


def actualizar_estado_pedido(order_id, status):
    data = js_request(
        "PUT", f"/orders/{int(order_id)}.json", json={"order": {"status": status}}
    )
    return data.get("order", data)


# ============================================================
# MERCADO PAGO
# ============================================================

MERCADOPAGO_ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
MERCADOPAGO_WEBHOOK_SECRET = os.getenv("MERCADOPAGO_WEBHOOK_SECRET")
MERCADOPAGO_NOTIFICATION_URL = os.getenv(
    "MERCADOPAGO_NOTIFICATION_URL", f"{APP_BASE_URL}/mercadopago/webhook"
)


def validar_firma_mercadopago(data_id):
    """
    Manifest oficial: id:<data.id>;request-id:<x-request-id>;ts:<ts>;
    Se omiten pares ausentes.
    """
    if not MERCADOPAGO_WEBHOOK_SECRET:
        print("MP WARNING: MERCADOPAGO_WEBHOOK_SECRET no configurado")
        return False

    x_signature = request.headers.get("x-signature", "")
    x_request_id = request.headers.get("x-request-id", "")
    partes = {}
    for pieza in x_signature.split(","):
        if "=" in pieza:
            k, v = pieza.strip().split("=", 1)
            partes[k] = v
    ts = partes.get("ts")
    v1 = partes.get("v1")
    if not ts or not v1:
        return False

    segmentos = []
    if data_id:
        segmentos.append(f"id:{data_id};")
    if x_request_id:
        segmentos.append(f"request-id:{x_request_id};")
    if ts:
        segmentos.append(f"ts:{ts};")
    manifest = "".join(segmentos)

    esperado = hmac.new(
        MERCADOPAGO_WEBHOOK_SECRET.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(esperado, v1)


def crear_preferencia_mp(checkout):
    if not MERCADOPAGO_ACCESS_TOKEN:
        raise RuntimeError("Falta MERCADOPAGO_ACCESS_TOKEN")

    items = []
    for item in checkout["carrito"]:
        items.append({
            "id": str(item["product_id"]),
            "title": item["name"][:120],
            "quantity": int(item["qty"]),
            "currency_id": "CLP",
            "unit_price": float(item["unit_price"]),
        })
    if checkout.get("shipping_price", 0) > 0:
        items.append({
            "id": "shipping",
            "title": "Despacho",
            "quantity": 1,
            "currency_id": "CLP",
            "unit_price": float(checkout["shipping_price"]),
        })

    payload = {
        "items": items,
        "payer": {
            "name": checkout["nombre"],
            "email": checkout["email"],
        },
        "external_reference": checkout["ref"],
        "notification_url": MERCADOPAGO_NOTIFICATION_URL,
        "back_urls": {
            "success": f"{APP_BASE_URL}/pago/exitoso",
            "pending": f"{APP_BASE_URL}/pago/pendiente",
            "failure": f"{APP_BASE_URL}/pago/fallido",
        },
        "auto_return": "approved",
        # False permite estados pendientes y evita restringir innecesariamente medios de pago.
        "binary_mode": False,
        "metadata": {
            "jumpseller_order_id": str(checkout["jumpseller_order_id"]),
            "telefono": checkout["telefono"],
        },
    }
    r = requests.post(
        "https://api.mercadopago.com/checkout/preferences",
        headers={
            "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=25,
    )
    if not r.ok:
        raise RuntimeError(f"Mercado Pago preference: {r.status_code} {r.text[:500]}")
    return r.json()


def obtener_pago_mp(payment_id):
    r = requests.get(
        f"https://api.mercadopago.com/v1/payments/{payment_id}",
        headers={"Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}"},
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"Mercado Pago payment: {r.status_code} {r.text[:500]}")
    return r.json()


# ============================================================
# ESTADO CONVERSACIONAL EN MEMORIA
# ============================================================

ESTADOS = {}
ESTADOS_LOCK = Lock()
PROCESADOS = {}
PROCESADOS_LOCK = Lock()
STATE_TTL_SECONDS = int(os.getenv("STATE_TTL_SECONDS", "7200"))
MAX_ESTADOS = int(os.getenv("MAX_ESTADOS", "250"))
PROCESADOS_TTL_SECONDS = int(os.getenv("PROCESADOS_TTL_SECONDS", "7200"))
MAX_PROCESADOS = int(os.getenv("MAX_PROCESADOS", "2500"))
_LAST_STATE_CLEANUP = 0.0


def estado_inicial(telefono):
    return {
        "telefono": telefono,
        "paso": "inicio",
        "carrito": [],
        "ultimos_productos": [],
        "producto_seleccionado": None,
        "media_respuesta": None,
        "resultados_media": [],
        "checkout": {},
        "historial": [],
        "_ultimo_acceso": time.time(),
    }


def _limpiar_estados_locked(ahora):
    global _LAST_STATE_CLEANUP
    if ahora - _LAST_STATE_CLEANUP < 300 and len(ESTADOS) <= MAX_ESTADOS:
        return
    _LAST_STATE_CLEANUP = ahora
    viejos = [k for k, e in ESTADOS.items() if ahora - float(e.get("_ultimo_acceso", ahora)) > STATE_TTL_SECONDS]
    for k in viejos:
        ESTADOS.pop(k, None)
    if len(ESTADOS) > MAX_ESTADOS:
        orden = sorted(ESTADOS.items(), key=lambda kv: float(kv[1].get("_ultimo_acceso", 0)))
        for k, _ in orden[:len(ESTADOS) - MAX_ESTADOS]:
            ESTADOS.pop(k, None)
    if viejos:
        gc.collect()


def get_estado(telefono):
    with ESTADOS_LOCK:
        ahora = time.time()
        _limpiar_estados_locked(ahora)
        if telefono not in ESTADOS:
            ESTADOS[telefono] = estado_inicial(telefono)
        ESTADOS[telefono]["_ultimo_acceso"] = ahora
        return ESTADOS[telefono]


def reset_estado(telefono, conservar_carrito=False):
    with ESTADOS_LOCK:
        carrito = ESTADOS.get(telefono, {}).get("carrito", []) if conservar_carrito else []
        ESTADOS[telefono] = estado_inicial(telefono)
        ESTADOS[telefono]["carrito"] = carrito
        return ESTADOS[telefono]


def marcar_procesado(message_id):
    if not message_id:
        return True
    ahora = time.time()
    with PROCESADOS_LOCK:
        viejos = [k for k, ts in PROCESADOS.items() if ahora - ts > PROCESADOS_TTL_SECONDS]
        for k in viejos:
            PROCESADOS.pop(k, None)
        if len(PROCESADOS) > MAX_PROCESADOS:
            for k, _ in sorted(PROCESADOS.items(), key=lambda kv: kv[1])[:len(PROCESADOS) - MAX_PROCESADOS]:
                PROCESADOS.pop(k, None)
        if message_id in PROCESADOS:
            return False
        PROCESADOS[message_id] = ahora
        return True


# ============================================================
# PRESENTACION Y CARRITO
# ============================================================


def mensaje_bienvenida():
    return (
        "🌿 ¡Hola! Soy el asistente virtual de La Ortiga.\n\n"
        "Puedo ayudarte a buscar productos, conocer precios y características, "
        "armar tu carrito y pagar directamente por Mercado Pago.\n\n"
        "¿Qué producto estás buscando?"
    )


def texto_producto(p, numero=None):
    nombre = p.get("name", "Producto")
    precio = precio_producto(p)
    stock = stock_producto(p)
    pref = f"{numero}. " if numero else ""
    stock_txt = "" if stock is None else (" · disponible" if stock > 0 else " · sin stock")
    return f"{pref}*{nombre}* — {clp(precio)}{stock_txt}"


def listar_resultados(productos):
    """Texto corto de cierre para una búsqueda. Las fichas visuales se envían aparte."""
    if not productos:
        return "No encontré productos con esa búsqueda. ¿Quieres probar con otro nombre o característica?"
    cantidad = min(len(productos), 5)
    return (
        f"🌿 Encontré {cantidad} opciones. Te las muestro con foto 👇\n\n"
        "Responde solo con el *número* del producto que te interesa (1 al " + str(cantidad) + ")."
    )


def preparar_resultados_media(estado, productos):
    """Prepara hasta 5 tarjetas de producto para enviarlas por WhatsApp con su foto."""
    tarjetas = []
    productos_validos = [p for p in (productos or []) if producto_vendible(p)][:5]
    for i, p in enumerate(productos_validos, 1):
        stock = stock_producto(p)
        disponibilidad = "✅ Disponible" if stock is None or stock > 0 else "❌ Sin stock"
        texto = f"*{i}. {p.get('name', 'Producto')}*\n💰 {clp(precio_producto(p))}\n{disponibilidad}"
        tarjetas.append({
            "texto": texto,
            "media": imagen_producto(p),
        })
    estado["resultados_media"] = tarjetas



def imagen_producto(p):
    """Obtiene la primera imagen pública del producto desde Jumpseller."""
    if not isinstance(p, dict):
        return None

    candidatos = []

    # Formato habitual de Jumpseller: images -> [{"image": {"url": ...}}, ...]
    for raw in (p.get("images") or []):
        if not isinstance(raw, dict):
            continue
        img = raw.get("image", raw)
        if isinstance(img, dict):
            for key in ("url", "image_url", "full_url", "src", "original_url"):
                if img.get(key):
                    candidatos.append(str(img.get(key)).strip())
        elif isinstance(img, str):
            candidatos.append(img.strip())

    # Fallbacks por si el producto viene con una imagen principal directa.
    for key in ("image_url", "image", "main_image", "featured_image"):
        val = p.get(key)
        if isinstance(val, str):
            candidatos.append(val.strip())
        elif isinstance(val, dict):
            for subkey in ("url", "image_url", "full_url", "src", "original_url"):
                if val.get(subkey):
                    candidatos.append(str(val.get(subkey)).strip())

    for url in candidatos:
        if url.startswith("//"):
            url = "https:" + url
        if url.startswith("http://") or url.startswith("https://"):
            return url
    return None


def detalle_producto(p):
    nombre = p.get("name", "Producto")
    precio = precio_producto(p)
    stock = stock_producto(p)
    descripcion = re.sub(r"<[^>]+>", " ", str(p.get("description", "") or ""))
    descripcion = html.unescape(descripcion)
    descripcion = re.sub(r"\s+", " ", descripcion).strip()
    # Ficha breve para WhatsApp: solo una síntesis útil del producto.
    if len(descripcion) > 280:
        descripcion = descripcion[:280].rsplit(" ", 1)[0] + "…"
    stock_txt = "Disponible" if stock is None else (f"Stock: {stock} unidades" if stock > 0 else "Sin stock")
    partes = [f"🌱 *{nombre}*", f"Precio: *{clp(precio)}*", stock_txt]
    if descripcion:
        partes += ["", descripcion]
    if stock == 0:
        partes += ["", "Este producto está sin stock por ahora. Si quieres, dime qué alternativa buscas y te muestro productos disponibles."]
    else:
        partes += ["", "¿Cuántas unidades quieres comprar? Responde solo con la cantidad, por ejemplo *1* o *2*."]
    return "\n".join(partes)

def total_carrito(carrito):
    return sum(float(i["unit_price"]) * int(i["qty"]) for i in carrito)


def texto_carrito(carrito):
    if not carrito:
        return "🛒 Tu carrito está vacío. Dime qué producto quieres buscar."
    lineas = ["🛒 *Tu carrito*", ""]
    for i, item in enumerate(carrito, 1):
        subtotal = item["unit_price"] * item["qty"]
        lineas.append(f"{i}. {item['name']} x{item['qty']} — {clp(subtotal)}")
    lineas += ["", f"*Subtotal: {clp(total_carrito(carrito))}*", "", "Escribe *PAGAR* para finalizar la compra."]
    return "\n".join(lineas)


def agregar_al_carrito(estado, p, qty=1):
    qty = max(1, min(int(qty), 20))
    stock = stock_producto(p)
    if stock is not None and stock < qty:
        return False, f"Solo quedan {stock} unidades disponibles."

    pid = str(p.get("id"))
    variante = variante_para_compra(p, qty)
    variant_id = int(variante["id"]) if variante and variante.get("id") is not None else None
    stock_v = stock_variante(variante) if variante else stock
    if stock_v is not None and stock_v < qty:
        return False, f"Solo quedan {stock_v} unidades disponibles."

    precio = precio_variante(p, variante) if variante else precio_producto(p)
    if precio <= 0:
        return False, "Este producto no tiene un precio válido disponible para compra."

    for item in estado["carrito"]:
        if str(item["product_id"]) == pid and item.get("variant_id") == variant_id:
            nueva = item["qty"] + qty
            if stock_v is not None and nueva > stock_v:
                return False, f"Solo quedan {stock_v} unidades disponibles."
            item["qty"] = nueva
            item["unit_price"] = precio
            item["variant_id"] = variant_id
            return True, None

    estado["carrito"].append({
        "product_id": int(p["id"]),
        "variant_id": variant_id,
        "name": p.get("name", "Producto"),
        "qty": qty,
        "unit_price": precio,
    })
    return True, None


def verificar_carrito_actual(carrito):
    actualizado = []
    errores = []
    for item in carrito:
        p = obtener_producto_por_id(item["product_id"])
        if not p:
            errores.append(f"{item['name']}: ya no está disponible")
            continue
        variante = None
        if item.get("variant_id"):
            for v in variantes_producto(p):
                if str(v.get("id")) == str(item.get("variant_id")):
                    variante = v
                    break
        if variante is None:
            variante = variante_para_compra(p, item["qty"])

        stock = stock_variante(variante) if variante else stock_producto(p)
        if stock is not None and stock < item["qty"]:
            errores.append(f"{item['name']}: quedan {stock} unidades")
            continue

        precio = precio_variante(p, variante) if variante else precio_producto(p)
        if precio <= 0:
            errores.append(f"{item['name']}: ya no tiene un precio válido")
            continue

        nuevo = dict(item)
        nuevo["name"] = p.get("name", item["name"])
        nuevo["unit_price"] = precio
        nuevo["variant_id"] = int(variante["id"]) if variante and variante.get("id") is not None else None
        actualizado.append(nuevo)
    return actualizado, errores


# ============================================================
# IA PARA PREGUNTAS DE PRODUCTO/TIENDA
# ============================================================


def respuesta_ia(pregunta, productos=None):
    if not openai_client:
        return None
    contexto = ""
    if productos:
        bloques = []
        for p in productos[:5]:
            descripcion = re.sub(r"<[^>]+>", " ", str(p.get("description", "")))
            bloques.append(
                f"Producto: {p.get('name')}\nPrecio: {clp(precio_producto(p))}\n"
                f"Stock: {stock_producto(p)}\nDescripcion: {descripcion[:700]}"
            )
        contexto = "\n\n".join(bloques)

    system = f"""
Eres el asistente comercial de {NEGOCIO_NOMBRE}, una tienda chilena orientada a productos y soluciones de economia circular y sustentabilidad.
Responde en español de Chile, breve, cercano y orientado a vender sin presionar.
Reglas:
- Usa solamente la información de productos entregada en CONTEXTO; no inventes stock, precios, marcas, beneficios ni especificaciones.
- Si falta una característica, dilo claramente.
- Puedes ayudar con productos, tienda, compra, carrito, pago y derivación a ejecutivo.
- Si el usuario quiere comprar, indícale que puedes agregar el producto al carrito por WhatsApp.
- Nunca menciones APIs, Jumpseller, Meta, OpenAI ni sistemas internos.
"""
    try:
        resp = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"CONTEXTO:\n{contexto}\n\nPREGUNTA:\n{pregunta}"},
            ],
        )
        return (resp.choices[0].message.content or "").strip() or None
    except Exception as e:
        print("OPENAI ERROR:", repr(e))
        return None


# ============================================================
# CHECKOUT
# ============================================================

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def iniciar_checkout(estado):
    if not estado["carrito"]:
        return "Tu carrito está vacío. Primero agrega un producto."
    estado["paso"] = "checkout_nombre"
    estado["checkout"] = {}
    return "Perfecto 🛒 Para preparar tu compra, ¿cuál es tu nombre y apellido?"


def preparar_pago(estado):
    carrito, errores = verificar_carrito_actual(estado["carrito"])
    if errores:
        estado["carrito"] = carrito
        estado["paso"] = "inicio"
        return False, "Antes de cobrar encontré estos cambios:\n- " + "\n- ".join(errores) + "\n\nRevisa tu carrito nuevamente."
    estado["carrito"] = carrito

    c = estado["checkout"]
    subtotal = total_carrito(carrito)
    tarifa_envio = None
    if c.get("tipo_entrega") == "despacho":
        # Recalculamos justo antes de cobrar por si el administrador cambió la
        # Tabla de Tarifas desde que el cliente confirmó sus datos.
        tarifa_envio = cotizar_despacho_jumpseller(carrito, c.get("comuna", ""))
        envio = float(tarifa_envio["shipping_price"])
        c["shipping_price"] = envio
        c["shipping_method_id"] = tarifa_envio.get("shipping_method_id")
        c["shipping_method_name"] = tarifa_envio.get("shipping_method_name")
        c["region_code"] = tarifa_envio.get("region_code", "")
        c["region_name"] = tarifa_envio.get("region_name", "")
    else:
        envio = 0.0
    total = subtotal + envio
    ref = "LO_" + uuid.uuid4().hex[:24]

    customer = buscar_o_crear_cliente(
        c["email"], c["nombre"], telefono_sin_prefijo_twilio(estado["telefono"]), c.get("direccion", ""), c.get("comuna", "")
    )
    order = crear_pedido_jumpseller(
        customer["id"], carrito, c["tipo_entrega"], c.get("direccion", ""), c.get("comuna", ""), tarifa_envio=tarifa_envio
    )

    checkout = {
        "ref": ref,
        "telefono": estado["telefono"],
        "nombre": c["nombre"],
        "email": c["email"],
        "direccion": c.get("direccion", ""),
        "comuna": c.get("comuna", ""),
        "tipo_entrega": c["tipo_entrega"],
        "shipping_price": envio,
        "shipping_method_id": c.get("shipping_method_id"),
        "shipping_method_name": c.get("shipping_method_name", ""),
        "region_code": c.get("region_code", ""),
        "region_name": c.get("region_name", ""),
        "subtotal": subtotal,
        "total": total,
        "carrito": carrito,
        "jumpseller_order_id": order["id"],
    }

    try:
        pref = crear_preferencia_mp(checkout)
    except Exception:
        try:
            actualizar_estado_pedido(order["id"], "Canceled")
        except Exception as e2:
            print("NO SE PUDO CANCELAR ORDER TRAS ERROR MP:", repr(e2))
        raise

    guardar_venta_evento(
        ref, estado["telefono"], "PENDIENTE_PAGO", total,
        jumpseller_order_id=str(order["id"]),
        detalle=json.dumps(checkout, ensure_ascii=False)[:45000],
    )

    estado["paso"] = "esperando_pago"
    estado["checkout"]["ref"] = ref
    estado["checkout"]["jumpseller_order_id"] = order["id"]

    return True, (
        "💳 *Tu compra está lista para pagar*\n\n"
        f"Subtotal: {clp(subtotal)}\n"
        + (f"Despacho: {clp(envio)}\n" if envio else "")
        + f"*Total: {clp(total)}*\n\n"
        "👇 *PAGAR AHORA*\n"
        f"{pref.get('init_point')}\n\n"
        "Cuando Mercado Pago confirme el pago, te avisaré por este mismo WhatsApp ✅"
    )


# ============================================================
# INTENCIONES / TOLERANCIA A ERRORES DE ESCRITURA (V47)
# ============================================================


def _similitud(a, b):
    return SequenceMatcher(None, normalizar_texto(a), normalizar_texto(b)).ratio()


def _es_intencion_nueva_compra(texto_norm):
    """Detecta incluso errores comunes como 'queiro epdir'."""
    frases = (
        "quiero pedir", "quiero comprar", "quiero hacer una compra",
        "hacer pedido", "hacer un pedido", "comprar", "nuevo pedido",
    )
    if any(f in texto_norm for f in frases):
        return True
    if 5 <= len(texto_norm) <= 40:
        return max((_similitud(texto_norm, f) for f in frases), default=0.0) >= 0.72
    return False


def _salir_checkout_a_compra(estado):
    estado["producto_seleccionado"] = None
    estado["paso"] = "inicio"
    estado["checkout"] = {}
    return "¡Claro! 🌿 Dime qué producto quieres pedir y reviso precio y stock. Tu carrito actual sigue guardado."


# ============================================================
# LOGICA PRINCIPAL DEL BOT
# ============================================================


def procesar_texto(telefono, texto):
    estado = get_estado(telefono)
    txt = (texto or "").strip()
    n = normalizar_texto(txt)

    estado["historial"].append({"role": "user", "content": txt})
    estado["historial"] = estado["historial"][-10:]

    if n in {"menu", "inicio", "reiniciar", "empezar de nuevo"}:
        estado = reset_estado(telefono)
        return mensaje_bienvenida()

    if n in {"hola", "holi", "holaa", "buenas", "buenos dias", "buenas tardes", "buenas noches"}:
        return mensaje_bienvenida()

    if _es_intencion_nueva_compra(n):
        return _salir_checkout_a_compra(estado)

    if any(x in n for x in ["ejecutivo", "humano", "persona", "atencion al cliente"]):
        return f"Claro. Puedes hablar con un ejecutivo de La Ortiga al {EJECUTIVO_WHATSAPP}."

    if n in {"carrito", "ver carrito", "mi carrito"}:
        return texto_carrito(estado["carrito"])

    if n in {"vaciar carrito", "borrar carrito"}:
        estado["carrito"] = []
        estado["paso"] = "inicio"
        return "🛒 Listo, vacié tu carrito. ¿Qué producto quieres buscar?"

    if n in {"pagar", "finalizar", "finalizar compra", "comprar carrito"}:
        return iniciar_checkout(estado)

    # ---------- CHECKOUT GUIADO ----------
    paso = estado.get("paso")
    if paso == "checkout_nombre":
        if len(txt) < 3:
            return "Necesito tu nombre y apellido para continuar."
        estado["checkout"]["nombre"] = txt[:120]
        estado["paso"] = "checkout_email"
        return "Gracias. ¿Cuál es tu correo electrónico?"

    if paso == "checkout_email":
        if not EMAIL_RE.match(txt):
            return "Ese correo no parece válido. Escríbelo nuevamente, por favor."
        estado["checkout"]["email"] = txt.lower()
        estado["paso"] = "checkout_entrega"
        return "¿Prefieres *DESPACHO* o *RETIRO*?"

    if paso == "checkout_entrega":
        if "desp" in n or "envio" in n:
            estado["checkout"]["tipo_entrega"] = "despacho"
            estado["paso"] = "checkout_direccion"
            return "Perfecto. ¿Cuál es la dirección de despacho?"
        if "reti" in n:
            estado["checkout"]["tipo_entrega"] = "retiro"
            estado["checkout"]["direccion"] = ""
            estado["checkout"]["comuna"] = ""
            estado["paso"] = "checkout_confirmar"
            return resumen_confirmacion(estado)
        return "Indícame *DESPACHO* o *RETIRO*, por favor."

    if paso == "checkout_direccion":
        if len(txt) < 5:
            return "Indícame una dirección más completa, por favor."
        estado["checkout"]["direccion"] = txt[:250]
        estado["paso"] = "checkout_comuna"
        return "¿En qué comuna es el despacho?"

    if paso == "checkout_comuna":
        # Salidas explícitas: el usuario nunca debe quedar atrapado en este paso.
        if _es_intencion_nueva_compra(n):
            return _salir_checkout_a_compra(estado)
        if n in {"cancelar", "volver", "salir"}:
            estado["paso"] = "inicio"
            estado["checkout"] = {}
            return "Listo, cancelé el checkout. Tu carrito sigue guardado. Escribe *CARRITO* para verlo o dime qué producto buscas."
        if "reti" in n:
            estado["checkout"]["tipo_entrega"] = "retiro"
            estado["checkout"]["direccion"] = ""
            estado["checkout"]["comuna"] = ""
            estado["checkout"]["shipping_price"] = 0.0
            estado["paso"] = "checkout_confirmar"
            return resumen_confirmacion(estado)
        if "desp" in n or n == "envio":
            return "Ya elegiste *DESPACHO* 👍 Ahora necesito la *comuna* de entrega. Por ejemplo: *Viña del Mar*."

        # Antes de cotizar validamos y corregimos la comuna contra Jumpseller.
        geo = resolver_region_comuna_jumpseller(txt[:100], max_seconds=12, permitir_fuzzy=True)
        if not geo.get("municipality_name") or not geo.get("region_code"):
            estado["paso"] = "checkout_comuna"
            return (
                "No logré reconocer esa comuna 🤔. Escríbela nuevamente, por ejemplo *Viña del Mar*. "
                "También puedes escribir *RETIRO*, *CANCELAR* o *MENÚ*."
            )

        comuna_original = txt[:100]
        comuna_corregida = geo.get("municipality_name") or comuna_original
        estado["checkout"]["comuna"] = comuna_corregida
        if normalizar_texto(comuna_corregida) != normalizar_texto(comuna_original):
            print("COMUNA CORREGIDA V47:", comuna_original, "->", comuna_corregida, "score=", geo.get("match_score"))

        try:
            tarifa = cotizar_despacho_jumpseller(estado["carrito"], comuna_corregida)
            estado["checkout"]["shipping_price"] = float(tarifa["shipping_price"])
            estado["checkout"]["shipping_method_id"] = tarifa.get("shipping_method_id")
            estado["checkout"]["shipping_method_name"] = tarifa.get("shipping_method_name")
            estado["checkout"]["region_code"] = tarifa.get("region_code") or geo.get("region_code", "")
            estado["checkout"]["region_name"] = tarifa.get("region_name") or geo.get("region_name", "")
        except Exception as e:
            print("JUMPSELLER COTIZACION DESPACHO ERROR V47:", repr(e))
            estado["paso"] = "checkout_comuna"
            return (
                f"Reconocí la comuna como *{comuna_corregida}*, pero no encontré una tarifa de despacho configurada para ella. "
                "Puedes intentar otra comuna, escribir *RETIRO* o *CANCELAR*."
            )

        estado["paso"] = "checkout_confirmar"
        if normalizar_texto(comuna_corregida) != normalizar_texto(comuna_original):
            return f"Entendí *{comuna_corregida}* 👍\n\n" + resumen_confirmacion(estado)
        return resumen_confirmacion(estado)

    if paso == "checkout_confirmar":
        if n in {"confirmar", "confirmo", "si", "sí", "ok", "pagar"}:
            try:
                ok, mensaje = preparar_pago(estado)
                return mensaje
            except Exception as e:
                print("CHECKOUT ERROR:", repr(e))
                estado["paso"] = "inicio"
                return "Tuve un problema preparando el pago. Tu carrito sigue guardado; intenta nuevamente en un momento."
        if n in {"cancelar", "no", "volver"}:
            estado["paso"] = "inicio"
            return "No hay problema. Tu carrito sigue guardado."
        return "Si los datos están correctos escribe *CONFIRMAR*. Si no, escribe *CANCELAR*."

    if paso == "esperando_pago":
        if n in {"estado pago", "pague", "pagué", "ya pague", "ya pagué"}:
            return "Estoy esperando la confirmación automática de Mercado Pago. Apenas se apruebe, te aviso por aquí ✅"
        return "Tu pago está pendiente. Si quieres revisar productos mientras tanto, escribe *MENÚ*."

    # ---------- SELECCION DE RESULTADOS ----------
    # Cuando el cliente está viendo la ficha de un producto, los números representan
    # CANTIDAD del producto seleccionado, no el índice de la búsqueda anterior.
    if estado.get("paso") == "cantidad_producto":
        p = estado.get("producto_seleccionado")

        if re.fullmatch(r"\d{1,2}", n):
            qty = int(n)
            if not p:
                estado["paso"] = "inicio"
                return "Dime qué producto quieres buscar."
            ok, error = agregar_al_carrito(estado, p, qty)
            if not ok:
                return error
            estado["paso"] = "inicio"
            estado["producto_seleccionado"] = None
            return f"✅ Agregué {qty} x {p.get('name')} al carrito.\n\n{texto_carrito(estado['carrito'])}"

        m_qty = re.fullmatch(r"(?:comprar|agregar|llevo|quiero)\s+(\d{1,2})", n)
        if m_qty:
            qty = int(m_qty.group(1))
            if not p:
                estado["paso"] = "inicio"
                return "Dime qué producto quieres buscar."
            ok, error = agregar_al_carrito(estado, p, qty)
            if not ok:
                return error
            estado["paso"] = "inicio"
            estado["producto_seleccionado"] = None
            return f"✅ Agregué {qty} x {p.get('name')} al carrito.\n\n{texto_carrito(estado['carrito'])}"

        # Si escribe el nombre de otro producto mientras estaba en la ficha, entendemos
        # que inició una búsqueda nueva y soltamos la selección anterior.
        if len(n) >= 2 and n not in {"si", "sí", "no", "cancelar", "volver"}:
            estado["paso"] = "inicio"
            estado["producto_seleccionado"] = None

    # Un número solo después de una búsqueda abre la ficha del resultado correspondiente.
    if re.fullmatch(r"\d{1,2}", n) and estado.get("ultimos_productos"):
        idx = int(n) - 1
        if 0 <= idx < len(estado["ultimos_productos"]):
            p = estado["ultimos_productos"][idx]
            estado["producto_seleccionado"] = p
            estado["media_respuesta"] = imagen_producto(p)
            estado["paso"] = "cantidad_producto"
            return detalle_producto(p)
        return "Ese número no corresponde a los productos que te mostré."

    m = re.match(r"^(?:comprar|agregar|quiero|llevo)\s+(\d{1,2})(?:\s+x?(\d{1,2}))?$", n)
    if m and estado.get("ultimos_productos"):
        idx = int(m.group(1)) - 1
        qty = int(m.group(2) or 1)
        if 0 <= idx < len(estado["ultimos_productos"]):
            p = estado["ultimos_productos"][idx]
            ok, error = agregar_al_carrito(estado, p, qty)
            if not ok:
                return error
            estado["paso"] = "inicio"
            estado["producto_seleccionado"] = None
            return f"✅ Agregué {qty} x {p.get('name')} al carrito.\n\n{texto_carrito(estado['carrito'])}"
        return "Ese número no corresponde a los productos que te mostré."

    if n in {"productos", "catalogo", "catalogo de productos", "ver productos", "que venden", "qué venden"}:
        try:
            productos = [p for p in listar_productos() if producto_vendible(p)][:5]
            estado["ultimos_productos"] = productos
            estado["producto_seleccionado"] = None
            estado["paso"] = "inicio"
            preparar_resultados_media(estado, productos)
            return listar_resultados(productos)
        except Exception as e:
            print("JUMPSELLER LIST ERROR:", repr(e))
            return "No pude consultar el catálogo en este momento. Intenta nuevamente en unos minutos."

    # Busca producto usando el texto completo, retirando algunas palabras de intención.
    consulta = re.sub(
        r"\b(quiero|busco|necesito|comprar|precio|cuanto|cuesta|tienen|tienes|producto|de|un|una|el|la)\b",
        " ", n,
    )
    consulta = re.sub(r"\s+", " ", consulta).strip()

    if len(consulta) >= 2:
        try:
            productos = buscar_productos(consulta, limite=5)
        except Exception as e:
            print("JUMPSELLER SEARCH ERROR:", repr(e))
            productos = []

        if productos:
            estado["ultimos_productos"] = productos[:5]
            estado["producto_seleccionado"] = None
            estado["paso"] = "inicio"
            preparar_resultados_media(estado, productos[:5])
            # Si la pregunta busca detalle/beneficios, usa IA con datos reales.
            if any(k in n for k in ["caracteristica", "sirve", "uso", "beneficio", "detalle", "como funciona", "para que"]):
                ai = respuesta_ia(txt, productos)
                if ai:
                    return ai + "\n\nSi quieres comprarlo, escribe *comprar 1*."
            return listar_resultados(estado["ultimos_productos"])

    # Pregunta general de tienda: IA, sin inventar catálogo.
    ai = respuesta_ia(txt, [])
    if ai:
        return ai
    return "Puedo ayudarte a buscar productos, revisar tu carrito o pagar una compra. ¿Qué estás buscando?"


def resumen_confirmacion(estado):
    c = estado["checkout"]
    envio = float(c.get("shipping_price", 0) or 0) if c.get("tipo_entrega") == "despacho" else 0
    subtotal = total_carrito(estado["carrito"])
    lineas = [
        "🧾 *Confirma tu compra*", "",
        texto_carrito(estado["carrito"]).replace("\n\nEscribe *PAGAR* para finalizar la compra.", ""),
        "",
        f"Nombre: {c.get('nombre')}",
        f"Correo: {c.get('email')}",
        f"Entrega: {c.get('tipo_entrega', '').capitalize()}",
    ]
    if c.get("tipo_entrega") == "despacho":
        lineas += [
            f"Dirección: {c.get('direccion')}", f"Comuna: {c.get('comuna')}",
            f"Método de despacho: {c.get('shipping_method_name') or 'Tabla de Tarifas Jumpseller'}",
            f"Despacho: {clp(envio)}",
        ]
    lineas += ["", f"*Total a pagar: {clp(subtotal + envio)}*", "", "Si todo está correcto escribe *CONFIRMAR*."]
    return "\n".join(lineas)


# ============================================================
# WHATSAPP / TWILIO WEBHOOK
# ============================================================

@app.route("/whatsapp/webhook", methods=["POST"])
@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    try:
        telefono = (request.form.get("From", "") or "").strip()
        texto = (request.form.get("Body", "") or "").strip()
        message_id = (request.form.get("MessageSid", "") or "").strip()

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono)
        print("Body:", texto)
        print("MessageSid:", message_id)
        print("=" * 60)

        twiml = MessagingResponse()

        if not telefono:
            twiml.message("No pude identificar tu número de WhatsApp.")
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if not marcar_procesado(message_id):
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if not texto:
            respuesta = "Por ahora puedo ayudarte por mensajes de texto 😊."
        else:
            guardar_mensaje(telefono, "user", texto)
            respuesta = procesar_texto(telefono, texto)

        estado_actual = get_estado(telefono)
        tarjetas = estado_actual.pop("resultados_media", []) or []
        media_url = estado_actual.pop("media_respuesta", None)

        # Guardamos en Sheets también un resumen de las tarjetas sugeridas.
        if tarjetas:
            resumen_tarjetas = "\n".join(t.get("texto", "") for t in tarjetas if t.get("texto"))
            guardar_mensaje(telefono, "assistant", respuesta + ("\n\n" + resumen_tarjetas if resumen_tarjetas else ""))
        else:
            guardar_mensaje(telefono, "assistant", respuesta)

        # Resultado de búsqueda: enviamos hasta 5 productos como mensajes separados,
        # cada uno con foto, nombre, precio y disponibilidad. Después pedimos elegir.
        if tarjetas:
            for tarjeta in tarjetas[:5]:
                m = twiml.message(tarjeta.get("texto", "Producto"))
                foto = tarjeta.get("media")
                if foto:
                    try:
                        m.media(foto)
                        print("TWILIO MEDIA SUGERENCIA:", foto)
                    except Exception as e:
                        print("TWILIO MEDIA SUGERENCIA ERROR:", repr(e))
            twiml.message(respuesta)
        else:
            mensaje_twilio = twiml.message(respuesta)
            if media_url:
                try:
                    mensaje_twilio.media(media_url)
                    print("TWILIO MEDIA PRODUCTO:", media_url)
                except Exception as e:
                    print("TWILIO MEDIA ERROR:", repr(e))

        return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

    except Exception as e:
        print("TWILIO WEBHOOK ERROR:", repr(e))
        twiml = MessagingResponse()
        twiml.message("Disculpa 🙏 Tuve un problema procesando el mensaje. Intenta nuevamente.")
        return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}


# ============================================================
# MERCADO PAGO WEBHOOK
# ============================================================


def extraer_pending_desde_sheet(ref):
    """Busca de abajo hacia arriba una fila PENDIENTE_PAGO por referencia."""
    if not GOOGLE_SHEET_ID:
        return None
    try:
        asegurar_pestana(
            GOOGLE_SHEET_SALES_TAB,
            ["FechaHora", "Referencia", "Telefono", "Estado", "Total", "JumpsellerOrderID", "MercadoPagoPaymentID", "Detalle"],
        )
        svc = sheets_service()
        rows = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_SALES_TAB}'!A:H",
        ).execute().get("values", [])
        for row in reversed(rows[1:]):
            if len(row) >= 8 and row[1] == ref and row[3] == "PENDIENTE_PAGO":
                try:
                    detalle = json.loads(row[7])
                except Exception:
                    detalle = {}
                return {
                    "ref": row[1], "telefono": row[2], "total": float(row[4]),
                    "jumpseller_order_id": row[5], "detalle": detalle,
                }
    except Exception as e:
        print("SEARCH PENDING SHEET ERROR:", repr(e))
    return None


def venta_ya_pagada(ref):
    if not GOOGLE_SHEET_ID:
        return False
    try:
        svc = sheets_service()
        rows = svc.spreadsheets().values().get(
            spreadsheetId=GOOGLE_SHEET_ID,
            range=f"'{GOOGLE_SHEET_SALES_TAB}'!B:D",
        ).execute().get("values", [])
        return any(len(r) >= 3 and r[0] == ref and r[2] == "PAGADO" for r in rows[1:])
    except Exception:
        return False


@app.route("/mercadopago/webhook", methods=["POST", "GET"])
def mercadopago_webhook():
    data_id = request.args.get("data.id") or request.args.get("data_id")
    payload = request.get_json(silent=True) or {}
    if not data_id:
        data_id = str((payload.get("data") or {}).get("id") or "")

    tipo = request.args.get("type") or payload.get("type")
    if tipo and tipo != "payment":
        return "OK", 200
    if not data_id:
        return "OK", 200

    if not validar_firma_mercadopago(data_id):
        print("MP WEBHOOK FIRMA INVALIDA", data_id)
        return "Invalid signature", 401

    try:
        pago = obtener_pago_mp(data_id)
        if pago.get("status") != "approved":
            return "OK", 200

        ref = str(pago.get("external_reference") or "")
        if not ref.startswith("LO_"):
            return "OK", 200
        if venta_ya_pagada(ref):
            return "OK", 200

        pending = extraer_pending_desde_sheet(ref)
        if not pending:
            print("MP: referencia no encontrada", ref)
            return "OK", 200

        monto = float(pago.get("transaction_amount") or 0)
        moneda = pago.get("currency_id")
        if abs(monto - float(pending["total"])) > 0.01 or moneda != "CLP":
            guardar_venta_evento(
                ref, pending["telefono"], "MONTO_INVALIDO", monto,
                pending["jumpseller_order_id"], str(data_id),
                f"Esperado={pending['total']} moneda={moneda}",
            )
            return "OK", 200

        order_id = pending["jumpseller_order_id"]
        actualizar_estado_pedido(order_id, "Paid")
        guardar_venta_evento(
            ref, pending["telefono"], "PAGADO", monto,
            order_id, str(data_id), "Pago aprobado y pedido marcado Paid",
        )

        enviar_mensaje_twilio(
            pending["telefono"],
            "✅ *¡Pago confirmado!*\n\n"
            f"Recibimos tu pago por {clp(monto)}.\n"
            f"Pedido La Ortiga: #{order_id}\n\n"
            "Tu compra quedó confirmada. Te contactaremos por este mismo WhatsApp con la información de entrega 🌿"
        )

        # Limpia carrito/checkout si el proceso sigue vivo en esta instancia.
        if pending["telefono"] in ESTADOS:
            reset_estado(pending["telefono"])

        return "OK", 200
    except Exception as e:
        print("MP WEBHOOK ERROR:", repr(e))
        # 500 hace que Mercado Pago pueda reintentar una notificación transitoria.
        return "Error", 500


@app.route("/pago/exitoso")
def pago_exitoso():
    return "<h2>Pago recibido</h2><p>Estamos confirmando tu compra. Vuelve a WhatsApp para recibir la confirmación.</p>", 200


@app.route("/pago/pendiente")
def pago_pendiente():
    return "<h2>Pago pendiente</h2><p>Cuando Mercado Pago confirme el pago, te avisaremos por WhatsApp.</p>", 200


@app.route("/pago/fallido")
def pago_fallido():
    return "<h2>Pago no completado</h2><p>Vuelve a WhatsApp e intenta nuevamente.</p>", 200


@app.route("/")
def health():
    return {
        "ok": True,
        "app": "La Ortiga WhatsApp Commerce Bot",
        "version": APP_VERSION,
        "whatsapp": "Twilio WhatsApp",
        "catalog": "Jumpseller REST API",
        "payments": "Mercado Pago Checkout Pro",
        "calendar": "disabled",
    }, 200


if __name__ == "__main__":
    print("APP_VERSION:", APP_VERSION)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
