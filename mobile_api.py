"""
Llama a Jaime Servicios — V6 APP CLEAN
Backend exclusivo para la App/PWA.

Eliminado del núcleo:
- WhatsApp / Twilio
- Gupshup
- Instagram / Meta Messaging
- webhooks de mensajería externa
- router de conversaciones por canales externos
- Portal Nexia histórico
- demos/planes basados en mensajes externos
- lógica heredada de La Ortiga por WhatsApp

Se conserva:
- App/PWA
- Supabase
- IA opcional
- solicitudes
- prestadores
- matching
- cotizaciones
- chat interno del servicio
- ubicación
- fotos
- reclamos
- auditoría
- Web Push
"""

import os

from dotenv import load_dotenv
from flask import Flask
from openai import OpenAI
from werkzeug.middleware.proxy_fix import ProxyFix

from mobile_api import register_mobile_app


APP_VERSION = "2026-09-27-LLAMA-A-JAIME-V6-APP-CLEAN"

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "").strip()
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_port=1,
)

# ============================================================
# CONFIGURACIÓN
# ============================================================

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_TIMEOUT = int(os.getenv("SUPABASE_TIMEOUT", "15"))

LLAMA_A_JAIME_EMPRESA_ID = (
    os.getenv("LLAMA_A_JAIME_EMPRESA_ID")
    or os.getenv("SUPABASE_EMPRESA_ID")
    or ""
).strip()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
IA_ACTIVA = os.getenv("IA_ACTIVA", "true").strip().lower() in {
    "1", "true", "yes", "si", "sí"
}

WEB_PUSH_VAPID_PUBLIC_KEY = os.getenv(
    "WEB_PUSH_VAPID_PUBLIC_KEY", ""
).strip()

WEB_PUSH_VAPID_PRIVATE_KEY = os.getenv(
    "WEB_PUSH_VAPID_PRIVATE_KEY", ""
).strip()

WEB_PUSH_CONTACT = os.getenv(
    "WEB_PUSH_CONTACT",
    "mailto:contacto@nexia-tech.com",
).strip()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MOBILE_APP_DIR = os.getenv(
    "MOBILE_APP_DIR",
    os.path.join(BASE_DIR, "mobile_app"),
).strip()


# ============================================================
# VALIDACIÓN DE CONFIGURACIÓN
# ============================================================

def validar_configuracion():
    faltantes = []

    if not app.secret_key:
        faltantes.append("SECRET_KEY")

    if not SUPABASE_URL:
        faltantes.append("SUPABASE_URL")

    if not SUPABASE_SERVICE_ROLE_KEY:
        faltantes.append("SUPABASE_SERVICE_ROLE_KEY")

    if not LLAMA_A_JAIME_EMPRESA_ID:
        faltantes.append("LLAMA_A_JAIME_EMPRESA_ID o SUPABASE_EMPRESA_ID")

    if faltantes:
        raise RuntimeError(
            "Faltan variables de entorno obligatorias: "
            + ", ".join(faltantes)
        )


validar_configuracion()


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


# ============================================================
# OPENAI — IA OPCIONAL
# ============================================================

openai_client = (
    OpenAI(api_key=OPENAI_API_KEY)
    if OPENAI_API_KEY and IA_ACTIVA
    else None
)


def generar_texto_ia(modelo, instrucciones, contexto):
    if not openai_client:
        raise RuntimeError("IA no configurada")

    response = openai_client.responses.create(
        model=modelo,
        instructions=instrucciones,
        input=contexto,
    )

    texto = (response.output_text or "").strip()

    return texto, {
        "api": "responses",
    }


# ============================================================
# APP / PWA LLAMA A JAIME
# ============================================================

register_mobile_app(
    app,
    settings={
        "app_dir": MOBILE_APP_DIR,
        "app_version": APP_VERSION,
        "empresa_id": LLAMA_A_JAIME_EMPRESA_ID,
        "supabase_url": SUPABASE_URL,
        "supabase_timeout": SUPABASE_TIMEOUT,
        "ai_model": OPENAI_MODEL,
        "ai_enabled": bool(openai_client),
        "vapid_public_key": WEB_PUSH_VAPID_PUBLIC_KEY,
        "vapid_private_key": WEB_PUSH_VAPID_PRIVATE_KEY,
        "vapid_contact": WEB_PUSH_CONTACT,
    },
    supabase_headers=supabase_headers,
    ai_generate=generar_texto_ia,

    # V6: ya no existe despacho legado por WhatsApp/La Ortiga.
    legacy_dispatch=None,
)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/")
def health():
    return {
        "ok": True,
        "app": "Llama a Jaime Servicios",
        "version": APP_VERSION,
        "architecture": "app-only",
        "database": "Supabase",
        "ai": bool(openai_client),
        "external_chat": False,
        "whatsapp": False,
        "instagram": False,
        "gupshup": False,
    }, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))

    print("APP_VERSION:", APP_VERSION)
    print("MODO: APP/PWA ONLY")
    print("IA:", bool(openai_client))
    print("WHATSAPP: OFF")
    print("INSTAGRAM: OFF")
    print("GUPSHUP: OFF")

    app.run(
        host="0.0.0.0",
        port=port,
    )
