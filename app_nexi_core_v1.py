import os
import re
import html
import json
import hmac
import hashlib
import base64
from datetime import datetime, timedelta
from threading import Lock
from contextvars import ContextVar
from urllib.parse import urljoin, urlparse, urldefrag, urlencode, quote
from html.parser import HTMLParser

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, request, redirect
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from werkzeug.middleware.proxy_fix import ProxyFix
from cryptography.fernet import Fernet, InvalidToken
import base64


APP_VERSION = "2026-09-13-NEXI-V3.4.15-JUMPSELLER-TIME-FIX"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

DEFAULT_ASISTENTE_NOMBRE = os.getenv("ESTILISTA_NOMBRE", "Cleo")
DEFAULT_NEGOCIO_NOMBRE = os.getenv("NEGOCIO_NOMBRE", "Estilista Diego")
TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")

# ============================================================
# ESTADOS AUTOMÁTICOS DE CONVERSACIÓN
# ============================================================
# ONLINE:
#   actividad reciente dentro de este número de minutos.
# EN ESPERA:
#   sin actividad reciente, pero todavía dentro del rango de espera.
# TERMINADA:
#   supera el rango de espera definido.
#
# Se pueden cambiar desde Render Environment sin tocar el código.
CONVERSACION_ONLINE_MINUTOS = int(os.getenv("CONVERSACION_ONLINE_MINUTOS", "15"))
CONVERSACION_ESPERA_HORAS = int(os.getenv("CONVERSACION_ESPERA_HORAS", "24"))
MODO_EJECUTIVO_TIMEOUT_MINUTOS = int(os.getenv("MODO_EJECUTIVO_TIMEOUT_MINUTOS", "30"))
CORE_HANDOFF_TIMEOUT_MINUTOS = int(os.getenv("CORE_HANDOFF_TIMEOUT_MINUTOS", "10"))
# Empresa CLIENTE Nexia: protección pública de datos de contacto.
NEXIA_CLIENTE_EMPRESA_ID = os.getenv(
    "NEXIA_CLIENTE_EMPRESA_ID",
    "1675736f-e605-405a-b7bb-eed29e013dd1",
).strip()
DEFAULT_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DEFAULT_DIRECCION_ATENCION = os.getenv("DIRECCION_ATENCION", "3 Poniente 382, Viña del Mar")
DEFAULT_TELEFONO_EJECUTIVO = os.getenv("TELEFONO_EJECUTIVO", "+56966461436")

# Twilio WhatsApp: recepción y respuestas manuales desde Portal Nexia.
# Las credenciales deben guardarse SOLO en Render > Environment.
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_FROM = os.getenv(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+56971906724",
).strip()

twilio_client = (
    TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN
    else None
)

DEFAULT_HORA_APERTURA = int(os.getenv("HORA_APERTURA", "10"))
DEFAULT_HORA_CIERRE = int(os.getenv("HORA_CIERRE", "19"))
DEFAULT_DURACION_RESERVA = int(os.getenv("DURACION_RESERVA", "60"))

# Gupshup: canal paralelo al Twilio actual.
# La API key debe guardarse en Render > Environment, nunca dentro del código.
GUPSHUP_API_KEY = os.getenv("GUPSHUP_API_KEY")
GUPSHUP_SOURCE = os.getenv("GUPSHUP_SOURCE", "56978316272")
GUPSHUP_APP_NAME = os.getenv("GUPSHUP_APP_NAME", "NexiaTech")
GUPSHUP_API_URL = os.getenv("GUPSHUP_API_URL", "https://api.gupshup.io/wa/api/v1/msg")

# Instagram Messaging API (Meta).
# El access token y el app secret deben guardarse SOLO en Render > Environment.
# Nunca los pongas en portal.html ni los publiques en GitHub.
INSTAGRAM_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
INSTAGRAM_VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "NEXIA_IG_WEBHOOK_2026")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET")
INSTAGRAM_USER_ID = os.getenv("INSTAGRAM_USER_ID", "17841476077966070")
INSTAGRAM_EMPRESA_ID = os.getenv("INSTAGRAM_EMPRESA_ID", "").strip()
INSTAGRAM_GRAPH_VERSION = os.getenv("INSTAGRAM_GRAPH_VERSION", "v26.0")
INSTAGRAM_API_BASE = os.getenv("INSTAGRAM_API_BASE", "https://graph.instagram.com").rstrip("/")

# Supabase: historial para Portal Nexia.
# IMPORTANTE: SUPABASE_SERVICE_ROLE_KEY va SOLO en Render > Environment.
# Nunca debe ponerse en portal.html ni exponerse en el navegador.
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://nappdpkjtdzwtiuvrrhk.supabase.co").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "").strip()
# Usuario maestro Nexia. Este correo siempre se trata como superadmin global.
SUPERADMIN_EMAIL = os.getenv("SUPERADMIN_EMAIL", "contacto@nexia-tech.com").strip().lower()
DEFAULT_EMPRESA_ID = os.getenv("SUPABASE_EMPRESA_ID", "97be347a-51d6-467d-be49-839a254a4ad0")
# Router superior WhatsApp: permite que un solo número atienda Diego, demos y nuevos negocios.
DIEGO_EMPRESA_ID = os.getenv("DIEGO_EMPRESA_ID", DEFAULT_EMPRESA_ID).strip()
NEXIA_ROUTER_SUPERIOR_ACTIVO = os.getenv("NEXIA_ROUTER_SUPERIOR_ACTIVO", "true").strip().lower() in {"1", "true", "yes", "si", "sí"}
NEXIA_ROUTER_CONTEXTO_HORAS = int(os.getenv("NEXIA_ROUTER_CONTEXTO_HORAS", "24"))
NEXIA_DEMO_URL = os.getenv("NEXIA_DEMO_URL", "https://nexia-tech.com").strip()
# Empresa administrativa/superadmin histórica. Se mantiene separada del cliente Nexia.
ADMIN_EMPRESA_ID = os.getenv(
    "ADMIN_EMPRESA_ID",
    "0a9de921-9386-441c-b5aa-f432d3f44fe5",
).strip()
SUPABASE_TIMEOUT = int(os.getenv("SUPABASE_TIMEOUT", "15"))

PORTAL_ORIGIN = os.getenv("PORTAL_ORIGIN", "https://nexia-tech.com").rstrip("/")

# ============================================================
# NEXIA V2.0 - PLANES + MERCADO PAGO
# ============================================================
# Acepta nombres de variables usados habitualmente en despliegues previos.
MERCADOPAGO_ACCESS_TOKEN = (
    os.getenv("MERCADOPAGO_ACCESS_TOKEN")
    or os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
    or os.getenv("MP_ACCESS_TOKEN")
    or ""
).strip()
MERCADOPAGO_API_BASE = os.getenv("MERCADOPAGO_API_BASE", "https://api.mercadopago.com").rstrip("/")
PUBLIC_BACKEND_URL = (
    os.getenv("PUBLIC_BACKEND_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or "https://chatbot-laortiga-hddw.onrender.com"
).rstrip("/")
MERCADOPAGO_WEBHOOK_URL = os.getenv(
    "MERCADOPAGO_WEBHOOK_URL", f"{PUBLIC_BACKEND_URL}/webhooks/mercadopago"
).strip()

NEXIA_PLAN_DURACION_DIAS_DEFAULT = int(os.getenv("NEXIA_PLAN_DURACION_DIAS", "30"))

NEXIA_PLANES = {
    "nexia_500": {
        "codigo": "nexia_500",
        "nombre": "Nexia 500",
        "mensajes": 500,
        "duracion_dias": NEXIA_PLAN_DURACION_DIAS_DEFAULT,
        "precio": 15990,
        "precio_antes": 19990,
        "moneda": "CLP",
    },
    "nexia_1000": {
        "codigo": "nexia_1000",
        "nombre": "Nexia 1000",
        "mensajes": 1000,
        "duracion_dias": NEXIA_PLAN_DURACION_DIAS_DEFAULT,
        "precio": 29990,
        "precio_antes": 39990,
        "moneda": "CLP",
    },
}

# ============================================================
# RESEND - NOTIFICACIONES DE DERIVACIÓN A EJECUTIVO
# ============================================================
# La API Key debe guardarse SOLO en Render > Environment.
# Nunca debe escribirse dentro del código ni publicarse en GitHub.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_API_URL = os.getenv("RESEND_API_URL", "https://api.resend.com/emails").strip()
RESEND_FROM_EMAIL = os.getenv(
    "RESEND_FROM_EMAIL",
    "Nexia Tech <notificaciones@nexia-tech.com>",
).strip()

# Correo general de respaldo.
# Cada empresa puede definir su propio correo_ejecutivo en configuracion_bot.
EJECUTIVO_EMAIL = os.getenv("EJECUTIVO_EMAIL", "").strip()


# 0=lunes ... 5=sábado. Domingo cerrado.
DIAS_ATENCION = {0, 1, 2, 3, 4, 5}

# ============================================================
# NEXIA CORE - CAPA MULTICLIENTE / MULTISERVICIO
# ============================================================
TENANT_CTX = ContextVar("TENANT_CTX", default=None)
TENANT_CACHE = {}
TENANT_CACHE_LOCK = Lock()
TENANT_CACHE_TTL = int(os.getenv("TENANT_CACHE_TTL", "60"))

def tenant_default():
    return {"empresa_id": DEFAULT_EMPRESA_ID,"empresa_nombre": DEFAULT_NEGOCIO_NOMBRE,"tipo_negocio":"reservas","descripcion_empresa":"","asistente_nombre":DEFAULT_ASISTENTE_NOMBRE,"direccion":DEFAULT_DIRECCION_ATENCION,"telefono_ejecutivo":DEFAULT_TELEFONO_EJECUTIVO,"correo_ejecutivo":EJECUTIVO_EMAIL,"timezone":TIMEZONE,"calendar_id":DEFAULT_CALENDAR_ID,"hora_apertura":DEFAULT_HORA_APERTURA,"hora_cierre":DEFAULT_HORA_CIERRE,"duracion_reserva":DEFAULT_DURACION_RESERVA,"dias_atencion":[0,1,2,3,4,5],"prompt_extra":"","modulos":{"ia":True,"reservas":True,"handoff_humano":True,"whatsapp":True,"instagram":True},"servicios":None,"canal":None,"provider":None,"canal_config":{}}

def tenant_actual(): return TENANT_CTX.get() or tenant_default()
def set_tenant(data): TENANT_CTX.set(data or tenant_default())
def empresa_actual_id(): return str(tenant_actual().get("empresa_id") or DEFAULT_EMPRESA_ID)
def cfg(nombre, default=None):
    v=tenant_actual().get(nombre); return default if v is None else v
def cfg_int(nombre, default):
    try:return int(cfg(nombre,default))
    except:return int(default)
def cfg_modulo(nombre, default=True): return bool((cfg("modulos",{}) or {}).get(nombre,default))
def timezone_actual(): return str(cfg("timezone",TIMEZONE) or TIMEZONE)
def servicios_actuales(): return cfg("servicios") or SERVICIOS_DEFAULT
def servicio_por_numero_actual(): return {int(s["numero"]):c for c,s in servicios_actuales().items() if s.get("numero") is not None}

def backend_headers():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:return None
    return {"apikey":SUPABASE_SERVICE_ROLE_KEY,"Authorization":f"Bearer {SUPABASE_SERVICE_ROLE_KEY}","Content-Type":"application/json"}


# ============================================================
# NEXIA SAAS - PLANES POR MENSAJES + VIGENCIA
# ============================================================
# Demo: 50 mensajes totales o 24 horas, lo que ocurra primero.
# Nexia 500 / 1000: mensajes del plan o 30 días, lo que ocurra primero.
#
# Las respuestas manuales de un ejecutivo enviadas desde Portal Nexia NO
# consumen demo porque este control solo se ejecuta en los webhooks del bot.
DEMO_LIMITE_MENSAJES_DEFAULT = int(os.getenv("DEMO_LIMITE_MENSAJES", os.getenv("DEMO_LIMITE_RESPUESTAS", "50")))
DEMO_DURACION_HORAS_DEFAULT = int(os.getenv("DEMO_DURACION_HORAS", "24"))
DEMO_OVERRIDE_ACTIVO = os.getenv("DEMO_OVERRIDE_ACTIVO", "true").strip().lower() in {"1", "true", "yes", "si", "sí"}


def _parse_iso(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        return dt
    except Exception:
        return None


def _normalizar_identificador_demo(valor, canal="whatsapp"):
    valor = str(valor or "").strip()
    if (canal or "whatsapp").lower() == "whatsapp":
        return re.sub(r"\D", "", normalizar_telefono(valor))
    return re.sub(r"\D", "", valor) or valor


def activar_demo_por_contacto(identificador_cliente, canal="whatsapp"):
    """Si el contacto pertenece a una demo, cambia el tenant a su empresa.

    Esto permite que un mismo número/app de Nexia sea compartido por varias
    empresas en demostración sin mezclar configuración, historial ni consumo.
    """
    if not DEMO_OVERRIDE_ACTIVO:
        return False
    headers = backend_headers()
    identificador = _normalizar_identificador_demo(identificador_cliente, canal)
    if not headers or not identificador:
        return False
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/demo_accesos",
            headers=headers,
            params={
                "select": "empresa_id,canal,identificador_cliente,activo,inicio,fin",
                "canal": f"eq.{(canal or 'whatsapp').lower()}",
                "identificador_cliente": f"eq.{identificador}",
                "activo": "eq.true",
                "order": "created_at.desc",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        if not rows:
            return False
        empresa_id = str(rows[0].get("empresa_id") or "").strip()
        if not empresa_id:
            return False
        activar_por_empresa(empresa_id, canal=canal, provider="demo")
        row=dict(rows[0]); row["empresa_id"]=empresa_id; row["identificador_cliente"]=identificador
        print("NEXIA DEMO TENANT:", empresa_id, canal, identificador)
        return row
    except Exception as e:
        print("NEXIA DEMO RESOLVE ERROR:", repr(e))
        return False


def estado_suscripcion_empresa(empresa_id=None):
    """Obtiene estado de plan para Portal/diagnóstico.

    Si una empresa aún no tiene fila en suscripciones_empresa se considera
    cliente normal/legacy para no interrumpir a Diego ni a clientes existentes.
    """
    headers = backend_headers()
    empresa_id = str(empresa_id or empresa_actual_id() or "").strip()
    if not headers or not empresa_id:
        return {"tipo_plan": "legacy", "estado": "activo", "controlado": False}
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/suscripciones_empresa",
            headers=headers,
            params={"select": "*", "empresa_id": f"eq.{empresa_id}", "limit": "1"},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        if not rows:
            return {"empresa_id": empresa_id, "tipo_plan": "legacy", "estado": "activo", "controlado": False}
        out = dict(rows[0])
        out["controlado"] = True
        limite = int(out.get("limite_mensajes") or out.get("limite_respuestas") or 0)
        usadas = int(out.get("mensajes_usados") or out.get("respuestas_usadas") or 0)
        restantes = max(0, limite - usadas) if limite else None
        out["mensajes_restantes"] = restantes

        tipo = str(out.get("tipo_plan") or "").lower()
        estado = str(out.get("estado") or "activo").lower()
        vigente = estado == "activo"
        motivo_fin = None

        if tipo == "demo":
            fin = _parse_iso(out.get("demo_fin"))
            if fin and datetime.now(pytz.UTC) >= fin.astimezone(pytz.UTC):
                vigente = False
                motivo_fin = "tiempo"
            elif restantes is not None and restantes <= 0:
                vigente = False
                motivo_fin = "limite"

        elif tipo in {"nexia_500", "nexia_1000"}:
            fin = _parse_iso(out.get("periodo_fin"))
            if fin and datetime.now(pytz.UTC) >= fin.astimezone(pytz.UTC):
                vigente = False
                motivo_fin = "tiempo"
            elif restantes is not None and restantes <= 0:
                vigente = False
                motivo_fin = "limite"

        out["vigente"] = vigente
        out["motivo_fin"] = motivo_fin
        out["estado_efectivo"] = "activo" if vigente else "finalizado"
        return out
    except Exception as e:
        print("NEXIA PLAN STATUS ERROR:", repr(e))
        # Fail-open para no cortar producción por una caída puntual de Supabase.
        return {"empresa_id": empresa_id, "tipo_plan": "legacy", "estado": "activo", "controlado": False, "error": str(e)[:200]}


def consumir_mensaje_demo_atomico():
    """Reserva 1 respuesta de demo mediante RPC atómica de Supabase.

    Para planes pagados/legacy devuelve permitido=True sin descontar.
    Requiere ejecutar el SQL V67 incluido junto con este app.py.
    """
    headers = backend_headers()
    empresa_id = str(empresa_actual_id() or "").strip()
    if not headers or not empresa_id:
        return {"permitido": True, "tipo_plan": "legacy", "controlado": False}
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/rpc/consumir_mensaje_nexia",
            headers=headers,
            json={"p_empresa_id": empresa_id},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json() if r.content else []
        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            data = {}
        if "permitido" not in data:
            data["permitido"] = True
        return data
    except Exception as e:
        print("NEXIA DEMO CONSUMO ERROR:", repr(e))
        # Fail-open: una falla del contador no debe botar un cliente productivo.
        return {"permitido": True, "tipo_plan": "legacy", "controlado": False, "error": str(e)[:200]}



def consumir_mensaje_entrante_demo():
    """Cuenta un mensaje recibido del usuario dentro de una demo."""
    return consumir_mensaje_demo_atomico()


def preparar_mensaje_saliente_demo(mensaje):
    """Cuenta un mensaje enviado por bot en demos y planes por mensajes."""
    control = consumir_mensaje_demo_atomico()
    if bool(control.get("permitido", True)):
        tipo = str(control.get("tipo_plan") or "").lower()
        restantes = control.get("mensajes_restantes")
        try:
            restantes = int(restantes)
        except Exception:
            restantes = None
        aviso = None
        if tipo == "demo":
            if restantes == 20:
                aviso = "\n\nℹ️ Tu prueba de Nexia tiene 20 mensajes disponibles."
            elif restantes == 5:
                aviso = "\n\n⚠️ Te quedan 5 mensajes en tu prueba de Nexia."
            elif restantes == 1:
                aviso = "\n\n⚠️ Te queda 1 mensaje en tu prueba de Nexia."
        elif tipo in {"nexia_500", "nexia_1000"}:
            if restantes == 50:
                aviso = "\n\nℹ️ Tu plan Nexia tiene 50 mensajes disponibles."
            elif restantes == 10:
                aviso = "\n\n⚠️ Te quedan 10 mensajes en tu plan Nexia."
            elif restantes == 1:
                aviso = "\n\n⚠️ Te queda 1 mensaje en tu plan Nexia."
        if aviso:
            mensaje = str(mensaje or "").rstrip() + aviso
        return mensaje
    return mensaje_plan_finalizado(control)


def mensaje_plan_finalizado(control=None):
    control = control or {}
    tipo = str(control.get("tipo_plan") or "").strip().lower()
    motivo = str(control.get("motivo") or "").strip().lower()
    if tipo in {"nexia_500", "nexia_1000"}:
        if motivo == "tiempo":
            return (
                "Tu período mensual de Nexia ya finalizó. "
                "Puedes renovar tu plan desde Portal Nexia para continuar."
            )
        return (
            "Has utilizado todos los mensajes disponibles de tu plan Nexia. "
            "Puedes renovar tu plan desde Portal Nexia para continuar."
        )
    return mensaje_demo_finalizada(motivo)


def mensaje_demo_finalizada(motivo=None):
    motivo = str(motivo or "").strip().lower()
    if motivo == "tiempo":
        detalle = "Tu período de prueba de 24 horas ya finalizó."
    elif motivo == "limite":
        detalle = "Ya utilizaste las 50 mensajes gratuitos incluidas en tu prueba."
    else:
        detalle = "Tu prueba gratuita de Nexia ya finalizó."
    return (
        f"{detalle}\n\n"
        "Gracias por probar Nexia 💙. Si quieres seguir usando tu asistente, "
        "puedes activar un plan desde tu Portal Nexia o solicitar ayuda a un ejecutivo."
    )


def activar_demo_empresa(empresa_id, identificador_cliente=None, canal="whatsapp"):
    """Activa/reinicia una demo por 24h y 50 mensajes totales para una empresa."""
    headers = backend_headers()
    empresa_id = str(empresa_id or "").strip()
    if not headers or not empresa_id:
        raise RuntimeError("Supabase/empresa no configurados")

    ahora = datetime.now(pytz.UTC)
    fin = ahora + timedelta(hours=DEMO_DURACION_HORAS_DEFAULT)
    payload = {
        "empresa_id": empresa_id,
        "tipo_plan": "demo",
        "estado": "activo",
        "demo_inicio": ahora.isoformat(),
        "demo_fin": fin.isoformat(),
        "limite_mensajes": DEMO_LIMITE_MENSAJES_DEFAULT,
        "mensajes_usados": 0,
        "updated_at": ahora.isoformat(),
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/suscripciones_empresa",
        headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
        params={"on_conflict": "empresa_id"},
        json=payload,
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()

    identificador = _normalizar_identificador_demo(identificador_cliente, canal)
    if identificador:
        acceso = {
            "empresa_id": empresa_id,
            "canal": (canal or "whatsapp").lower(),
            "identificador_cliente": identificador,
            "activo": True,
            "inicio": ahora.isoformat(),
            "fin": fin.isoformat(),
            "updated_at": ahora.isoformat(),
        }
        ar = requests.post(
            f"{SUPABASE_URL}/rest/v1/demo_accesos",
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "canal,identificador_cliente"},
            json=acceso,
            timeout=SUPABASE_TIMEOUT,
        )
        ar.raise_for_status()

    return {
        "empresa_id": empresa_id,
        "tipo_plan": "demo",
        "estado": "activo",
        "demo_inicio": ahora.isoformat(),
        "demo_fin": fin.isoformat(),
        "limite_mensajes": DEMO_LIMITE_MENSAJES_DEFAULT,
        "mensajes_usados": 0,
        "mensajes_restantes": DEMO_LIMITE_MENSAJES_DEFAULT,
        "identificador_cliente": identificador or None,
        "canal": (canal or "whatsapp").lower(),
    }

def secret_from_env(env_name, fallback=None):
    if env_name:
        v=os.getenv(str(env_name),"")
        if v:return v.strip()
    return fallback

def _cache_get(key):
    import time
    with TENANT_CACHE_LOCK:
        item=TENANT_CACHE.get(key)
        if not item:return None
        if time.time()-item["ts"]>TENANT_CACHE_TTL:
            TENANT_CACHE.pop(key,None);return None
        return item["data"]
def _cache_set(key,data):
    import time
    with TENANT_CACHE_LOCK:TENANT_CACHE[key]={"ts":time.time(),"data":data}

def cargar_empresa_config(empresa_id,canal=None,provider=None,canal_config=None):
    empresa_id=str(empresa_id or "").strip()
    if not empresa_id:return tenant_default()
    key=f"empresa:{empresa_id}";base=_cache_get(key);headers=backend_headers()
    if base is None and headers:
        er=requests.get(f"{SUPABASE_URL}/rest/v1/empresas",headers=headers,params={"select":"id,nombre,activo","id":f"eq.{empresa_id}","limit":"1"},timeout=SUPABASE_TIMEOUT);er.raise_for_status(); empresas=er.json() if er.content else []
        if not empresas:raise RuntimeError("Empresa no encontrada")
        cr=requests.get(f"{SUPABASE_URL}/rest/v1/configuracion_bot",headers=headers,params={"select":"*","empresa_id":f"eq.{empresa_id}","limit":"1"},timeout=SUPABASE_TIMEOUT);cr.raise_for_status(); rows=cr.json() if cr.content else []; conf=rows[0] if rows else {}
        sr=requests.get(f"{SUPABASE_URL}/rest/v1/servicios",headers=headers,params={"select":"*","empresa_id":f"eq.{empresa_id}","activo":"eq.true","order":"orden.asc"},timeout=SUPABASE_TIMEOUT);sr.raise_for_status(); rows=sr.json() if sr.content else []
        servicios={}
        for row in rows:
            codigo=str(row.get("codigo") or "").strip()
            if codigo:servicios[codigo]={"numero":row.get("numero"),"nombre":row.get("nombre") or codigo,"precio":int(row.get("precio") or 0),"precio_texto":row.get("precio_texto") or "","detalle":row.get("detalle") or "","categoria":row.get("categoria") or "Servicios","aliases":row.get("aliases") or [],"duracion_minutos":row.get("duracion_minutos")}
        base=tenant_default();base.update({"empresa_id":empresa_id,"empresa_nombre":empresas[0].get("nombre") or DEFAULT_NEGOCIO_NOMBRE,"tipo_negocio":conf.get("tipo_negocio") or "reservas","descripcion_empresa":conf.get("descripcion_empresa") or "","asistente_nombre":("Cleo" if empresa_id == DIEGO_EMPRESA_ID else (conf.get("asistente_nombre") or DEFAULT_ASISTENTE_NOMBRE)),"direccion":conf.get("direccion") or DEFAULT_DIRECCION_ATENCION,"telefono_ejecutivo":conf.get("telefono_ejecutivo") or DEFAULT_TELEFONO_EJECUTIVO,"correo_ejecutivo":conf.get("correo_ejecutivo") or EJECUTIVO_EMAIL,"timezone":conf.get("timezone") or TIMEZONE,"calendar_id":conf.get("calendar_id") or DEFAULT_CALENDAR_ID,"hora_apertura":conf.get("hora_apertura") if conf.get("hora_apertura") is not None else DEFAULT_HORA_APERTURA,"hora_cierre":conf.get("hora_cierre") if conf.get("hora_cierre") is not None else DEFAULT_HORA_CIERRE,"duracion_reserva":conf.get("duracion_reserva") if conf.get("duracion_reserva") is not None else DEFAULT_DURACION_RESERVA,"dias_atencion":conf.get("dias_atencion") or [0,1,2,3,4,5],"prompt_extra":conf.get("prompt_extra") or "","modulos":conf.get("modulos") or tenant_default()["modulos"],"servicios":servicios or None});_cache_set(key,base)
    if base is None:base=tenant_default();base["empresa_id"]=empresa_id
    out=dict(base);out["canal"]=canal;out["provider"]=provider;out["canal_config"]=dict(canal_config or {});return out

def resolver_tenant(canal,provider,identificador_externo):
    canal=str(canal or "").lower().strip()
    provider=str(provider or "").lower().strip()
    externo=str(identificador_externo or "").strip()

    if canal=="whatsapp":
        externo=re.sub(r"\D","",externo)

    headers=backend_headers()

    # Sin backend configurado, conservamos compatibilidad legacy.
    if not headers:
        if canal=="instagram" and INSTAGRAM_EMPRESA_ID:
            return cargar_empresa_config(
                INSTAGRAM_EMPRESA_ID,
                canal="instagram",
                provider=provider or "meta",
                canal_config={
                    "canal":"instagram",
                    "provider":provider or "meta",
                    "identificador_externo":externo or INSTAGRAM_USER_ID,
                    "sender":externo or INSTAGRAM_USER_ID,
                },
            )
        out=tenant_default()
        out["canal"]=canal
        out["provider"]=provider
        return out

    key=f"route:{canal}:{provider}:{externo}"
    row=_cache_get(key)

    if row is None:
        r=requests.get(
            f"{SUPABASE_URL}/rest/v1/canales_empresa",
            headers=headers,
            params={
                "select":"*",
                "canal":f"eq.{canal}",
                "provider":f"eq.{provider}",
                "identificador_externo":f"eq.{externo}",
                "activo":"eq.true",
                "limit":"1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows=r.json() if r.content else []

        if not rows:
            # IMPORTANTE:
            # Instagram ya no cae silenciosamente al tenant DEFAULT/Nexia.
            # El Instagram legacy puede asociarse explícitamente con
            # INSTAGRAM_EMPRESA_ID en Render mientras se crea su fila
            # definitiva en canales_empresa.
            if canal=="instagram" and provider=="meta" and INSTAGRAM_EMPRESA_ID:
                print(
                    "INSTAGRAM TENANT FALLBACK EXPLICITO:",
                    externo,
                    "->",
                    INSTAGRAM_EMPRESA_ID,
                )
                return cargar_empresa_config(
                    INSTAGRAM_EMPRESA_ID,
                    canal="instagram",
                    provider="meta",
                    canal_config={
                        "canal":"instagram",
                        "provider":"meta",
                        "identificador_externo":externo,
                        "sender":externo or INSTAGRAM_USER_ID,
                    },
                )

            if canal=="instagram":
                raise RuntimeError(
                    "Instagram sin empresa asociada. Crea la ruta en canales_empresa "
                    "o configura INSTAGRAM_EMPRESA_ID en Render."
                )

            out=tenant_default()
            out["canal"]=canal
            out["provider"]=provider
            return out

        row=rows[0]
        _cache_set(key,row)

    return cargar_empresa_config(
        row["empresa_id"],
        canal=canal,
        provider=provider,
        canal_config=row,
    )

def activar_por_canal(canal,provider,identificador_externo):set_tenant(resolver_tenant(canal,provider,identificador_externo))
def activar_por_empresa(empresa_id,canal=None,provider=None,canal_config=None):set_tenant(cargar_empresa_config(empresa_id,canal=canal,provider=provider,canal_config=canal_config))


def zona_local():
    return pytz.timezone(timezone_actual())


def ahora_local():
    return datetime.now(zona_local())


def normalizar_texto(texto):
    texto = (texto or "").strip().lower()
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n",
    }
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    return texto


def es_empresa_nexia():
    """True únicamente para la empresa cliente Nexia, no para Administración General."""
    return str(empresa_actual_id() or "").strip() == NEXIA_CLIENTE_EMPRESA_ID


def mensaje_horario_no_publicado():
    return (
        "La atención de Nexia se gestiona directamente por este chat 😊. "
        "Déjame tu consulta y te ayudo por aquí."
    )


def proteger_respuesta_publica_nexia(texto):
    """
    Filtro FINAL de salida para Nexia (WhatsApp e Instagram).

    Impide publicar:
    - dirección física;
    - teléfono/celular/número del ejecutivo;
    - horario de atención.

    Esta capa se ejecuta justo antes de guardar/enviar la respuesta, además de las
    reglas del prompt. Así, aunque OpenAI o una configuración antigua agreguen
    esos datos, no salen al cliente.
    """
    if not es_empresa_nexia():
        return str(texto or "")

    original = str(texto or "")
    if not original:
        return original

    # Datos exactos que pudieran seguir configurados en Supabase/Render.
    direccion_cfg = str(cfg("direccion", "") or "").strip()
    telefono_cfg = str(cfg("telefono_ejecutivo", "") or "").strip()
    sensibles_exactos = [
        direccion_cfg,
        telefono_cfg,
        str(DEFAULT_DIRECCION_ATENCION or "").strip(),
        str(DEFAULT_TELEFONO_EJECUTIVO or "").strip(),
    ]

    # Etiquetas públicas que no deben aparecer para Nexia.
    patron_etiqueta = re.compile(
        r"(?i)\b(?:"
        r"horario(?:\s+de\s+atenci[oó]n)?|"
        r"direcci[oó]n(?:\s+f[ií]sica)?|"
        r"tel[eé]fono(?:\s+de\s+ejecutivo)?|"
        r"n[uú]mero\s+de\s+tel[eé]fono|"
        r"celular|ubicaci[oó]n"
        r")\s*:"
    )
    patron_telefono_cl = re.compile(r"(?<!\d)(?:\+?56[ .-]*)?9(?:[ .-]*\d){8}(?!\d)")
    patron_hora = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b")

    lineas_limpias = []
    for linea in original.splitlines():
        limpia = linea
        normal = normalizar_texto(limpia)

        # Si contiene una dirección/teléfono exactos, corta la línea antes de ese dato.
        posiciones = []
        for valor in sensibles_exactos:
            if not valor:
                continue
            pos = limpia.lower().find(valor.lower())
            if pos >= 0:
                posiciones.append(pos)

        # Si aparece una etiqueta sensible, conserva solo el texto útil que hubiera antes.
        m = patron_etiqueta.search(limpia)
        if m:
            posiciones.append(m.start())

        # Horarios redactados sin etiqueta, p. ej. "Atendemos lunes a sábado 09:00–18:00".
        if patron_hora.search(limpia) and any(
            k in normal
            for k in (
                "horario", "atendemos", "atencion de", "atencion entre",
                "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
            )
        ):
            posiciones.append(0)

        # Teléfono chileno redactado sin etiqueta.
        mt = patron_telefono_cl.search(limpia)
        if mt:
            posiciones.append(mt.start())

        if posiciones:
            limpia = limpia[:min(posiciones)].rstrip(" -–—|,.;:")

        # Segunda pasada: jamás dejar valores exactos residuales.
        for valor in sensibles_exactos:
            if valor:
                limpia = re.sub(re.escape(valor), "", limpia, flags=re.IGNORECASE)
        limpia = patron_telefono_cl.sub("", limpia)
        limpia = limpia.strip()

        if limpia:
            lineas_limpias.append(limpia)

    salida = "\n".join(lineas_limpias).strip()
    if not salida:
        salida = mensaje_horario_no_publicado()
    return salida


def normalizar_telefono(valor):
    valor = (valor or "").strip()
    return valor[len("whatsapp:"):] if valor.startswith("whatsapp:") else valor


# ============================================================
# NEXI V1.8 - ROUTER SUPERIOR DE CONVERSACIONES WHATSAPP
# ============================================================
# El teléfono identifica a la persona; este router guarda con qué negocio está
# hablando en ese momento. Así un mismo número receptor puede servir a Diego,
# demos y clientes Nexia sin mezclar contexto, datos ni herramientas.

ROUTER_MENU_COMMANDS = {
    "menu", "menu principal", "inicio nexia", "cambiar negocio",
    "cambiar de negocio", "cambiar empresa", "recepcion", "recepción",
}
ROUTER_DIEGO_COMMANDS = {"diego", "diego estilista", "hablar con diego"}
ROUTER_DEMO_COMMANDS = {"mi demo", "demo", "mi prueba", "prueba", "probar mi demo", "probar demo", "probar mi prueba", "probar mi asistente", "mi asistente"}
NEXIA_PRUEBA_URL = os.getenv("NEXIA_PRUEBA_URL", "https://nexia-tech.com/prueba.html").strip()

# V2.3: el menú de recepción de WhatsApp se construye dinámicamente con
# empresas pagadas/activas. Twilio permite hasta 10 elementos por list-picker.
ROUTER_LIST_PAGE_SIZE = 9
ROUTER_TWILIO_CONTENT_CACHE = {}
ROUTER_TWILIO_CONTENT_CACHE_LOCK = Lock()


def _router_identificador(valor):
    return re.sub(r"\D", "", normalizar_telefono(str(valor or "")))


def _router_headers(prefer=None):
    h = backend_headers()
    if not h:
        return None
    h = dict(h)
    if prefer:
        h["Prefer"] = prefer
    return h


def _router_tabla_no_disponible(resp):
    return bool(resp is not None and getattr(resp, "status_code", None) in {404})


def router_demo_access_sin_activar(telefono):
    """Busca una PRUEBA realmente activa del usuario sin cambiar el tenant global.

    Una empresa que ya se convirtió a plan pagado deja de aparecer como "Mi prueba"
    aunque su antigua fila en demo_accesos siga existiendo.
    """
    headers = _router_headers()
    ident = _router_identificador(telefono)
    if not headers or not ident:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/demo_accesos",
            headers=headers,
            params={
                "select": "empresa_id,canal,identificador_cliente,activo,inicio,fin",
                "canal": "eq.whatsapp",
                "identificador_cliente": f"eq.{ident}",
                "activo": "eq.true",
                "order": "created_at.desc",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        if not rows:
            return None
        row = dict(rows[0])
        fin = _parse_iso(row.get("fin"))
        if fin and datetime.now(pytz.UTC) >= fin.astimezone(pytz.UTC):
            return None

        plan = estado_suscripcion_empresa(row.get("empresa_id"))
        if str(plan.get("tipo_plan") or "").strip().lower() != "demo":
            return None
        if str(plan.get("estado") or "").strip().lower() != "activo":
            return None
        return row
    except Exception as e:
        print("NEXI ROUTER DEMO LOOKUP ERROR:", repr(e))
        return None



def router_asistente_propio(telefono):
    """
    Devuelve el asistente asociado al WhatsApp del creador/cliente.
    - Si conserva una demo activa, devuelve esa misma demo.
    - Si la demo se convirtió en Nexia 500/1000, reutiliza el mismo empresa_id.
    - Nunca crea otra empresa ni otra prueba.
    """
    # Camino 1: demo activa real.
    demo = router_demo_access_sin_activar(telefono)
    if demo:
        empresa_id = str(demo.get("empresa_id") or "").strip()
        if empresa_id:
            return {
                "empresa_id": empresa_id,
                "motor": "core",
                "origen": "demo",
                "demo_access": demo,
            }

    headers = _router_headers()
    ident = _router_identificador(telefono)
    if not headers or not ident:
        return None

    try:
        # Una demo convertida a plan pagado deja demo_accesos inactivo,
        # pero la fila histórica conserva la relación WhatsApp -> empresa.
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/demo_accesos",
            headers=headers,
            params={
                "select": "empresa_id,canal,identificador_cliente,activo,inicio,fin,updated_at,created_at",
                "canal": "eq.whatsapp",
                "identificador_cliente": f"eq.{ident}",
                "order": "updated_at.desc.nullslast,created_at.desc",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        if not rows:
            return None

        acceso = dict(rows[0])
        empresa_id = str(acceso.get("empresa_id") or "").strip()
        if not empresa_id:
            return None

        plan = estado_suscripcion_empresa(empresa_id)
        tipo = str(plan.get("tipo_plan") or "").strip().lower()
        estado = str(plan.get("estado") or "").strip().lower()
        vigente = plan.get("vigente")

        if tipo in {"nexia_500", "nexia_1000"} and estado == "activo" and vigente is not False:
            return {
                "empresa_id": empresa_id,
                "motor": "core",
                "origen": "pagado",
                "plan": plan,
            }

        return None
    except Exception as e:
        print("NEXI ROUTER MI ASISTENTE LOOKUP ERROR:", repr(e))
        return None


def router_contexto_obtener(identificador, canal="whatsapp"):
    headers = _router_headers()
    canal = str(canal or "whatsapp").strip().lower()
    ident = _router_identificador(identificador) if canal == "whatsapp" else str(identificador or "").strip()
    if not headers or not ident:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/nexi_router_sesiones",
            headers=headers,
            params={
                "select": "*",
                "canal": f"eq.{canal}",
                "identificador_cliente": f"eq.{ident}",
                "activo": "eq.true",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        if _router_tabla_no_disponible(r):
            return None
        r.raise_for_status()
        rows = r.json() if r.content else []
        if not rows:
            return None
        row = dict(rows[0])
        updated = _parse_iso(row.get("updated_at"))
        if updated and NEXIA_ROUTER_CONTEXTO_HORAS > 0:
            edad_horas = (datetime.now(pytz.UTC) - updated.astimezone(pytz.UTC)).total_seconds() / 3600
            if edad_horas >= NEXIA_ROUTER_CONTEXTO_HORAS:
                router_contexto_borrar(identificador, canal=canal)
                return None
        return row
    except Exception as e:
        print("NEXI ROUTER CONTEXTO GET ERROR:", canal, repr(e))
        return None

def router_contexto_guardar(identificador, empresa_id, motor="core", origen="menu", codigo=None, canal="whatsapp"):
    headers = _router_headers("resolution=merge-duplicates,return=representation")
    canal = str(canal or "whatsapp").strip().lower()
    ident = _router_identificador(identificador) if canal == "whatsapp" else str(identificador or "").strip()
    empresa_id = str(empresa_id or "").strip()
    if not headers or not ident or not empresa_id:
        return None
    ahora = datetime.now(pytz.UTC).isoformat()
    payload = {
        "canal": canal,
        "identificador_cliente": ident,
        "empresa_id": empresa_id,
        "motor": str(motor or "core").lower(),
        "origen": str(origen or "menu"),
        "codigo": str(codigo or "").strip() or None,
        "activo": True,
        "updated_at": ahora,
    }
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/nexi_router_sesiones",
            headers=headers,
            params={"on_conflict": "canal,identificador_cliente"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        if _router_tabla_no_disponible(r):
            return payload
        r.raise_for_status()
        rows = r.json() if r.content else []
        return rows[0] if rows else payload
    except Exception as e:
        print("NEXI ROUTER CONTEXTO SAVE ERROR:", canal, repr(e))
        return payload

def router_contexto_borrar(identificador, canal="whatsapp"):
    headers = _router_headers("return=minimal")
    canal = str(canal or "whatsapp").strip().lower()
    ident = _router_identificador(identificador) if canal == "whatsapp" else str(identificador or "").strip()
    if not headers or not ident:
        return False
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/nexi_router_sesiones",
            headers=headers,
            params={"canal": f"eq.{canal}", "identificador_cliente": f"eq.{ident}"},
            json={"activo": False, "updated_at": datetime.now(pytz.UTC).isoformat()},
            timeout=SUPABASE_TIMEOUT,
        )
        if _router_tabla_no_disponible(r):
            return False
        r.raise_for_status()
        return True
    except Exception as e:
        print("NEXI ROUTER CONTEXTO CLEAR ERROR:", canal, repr(e))
        return False

def router_destino_por_codigo(codigo):
    """Resuelve links/botones de clientes: wa.me/...?...text=NEXI%20CODIGO."""
    headers = _router_headers()
    codigo = re.sub(r"[^A-Z0-9_-]", "", str(codigo or "").upper())
    if not headers or not codigo:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/nexi_router_destinos",
            headers=headers,
            params={
                "select": "empresa_id,codigo,nombre_publico,motor,activo",
                "codigo": f"eq.{codigo}",
                "activo": "eq.true",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        if _router_tabla_no_disponible(r):
            return None
        r.raise_for_status()
        rows = r.json() if r.content else []
        return dict(rows[0]) if rows else None
    except Exception as e:
        print("NEXI ROUTER DESTINO ERROR:", repr(e))
        return None


def router_codigo_desde_texto(texto):
    raw = str(texto or "").strip()
    m = re.fullmatch(r"(?i)(?:NEXI|NEGOCIO)\s+([A-Z0-9_-]{3,40})", raw)
    return m.group(1).upper() if m else None


def _router_item_demo(nombre):
    """Título Twilio <=24 caracteres con estado visual de demo."""
    nombre = str(nombre or "Negocio Nexia").strip() or "Negocio Nexia"
    prefijo = "⚪ "
    sufijo = " / Demo"
    max_nombre = max(1, 24 - len(prefijo) - len(sufijo))
    return f"{prefijo}{nombre[:max_nombre].rstrip()}{sufijo}"


def _router_item_produccion(nombre):
    """Título Twilio <=24 caracteres con estado visual de producción."""
    nombre = str(nombre or "Negocio Nexia").strip() or "Negocio Nexia"
    prefijo = "🟢 "
    max_nombre = max(1, 24 - len(prefijo))
    return f"{prefijo}{nombre[:max_nombre].rstrip()}"


def _router_descripcion_demo(datos):
    """Descripción pública corta sin datos privados del creador."""
    datos = dict(datos or {})
    candidatos = (
        datos.get("rubro"),
        datos.get("productos_servicios"),
        datos.get("objetivo"),
    )
    for valor in candidatos:
        if isinstance(valor, (list, tuple)):
            valor = ", ".join(str(x) for x in valor if str(x).strip())
        valor = str(valor or "").strip()
        if valor:
            return valor[:72]
    return "Asistente en prueba de Nexia"


def router_demos_publicas_activas():
    """
    Devuelve TODAS las demos activas/no vencidas para el menú global.

    Fuente de verdad:
      suscripciones_empresa(tipo_plan='demo', estado='activo')

    No depende de demo_accesos: una demo creada desde la web puede existir
    antes de que conozcamos el WhatsApp del creador.
    """
    headers = _router_headers()
    if not headers:
        return []

    try:
        rs = requests.get(
            f"{SUPABASE_URL}/rest/v1/suscripciones_empresa",
            headers=headers,
            params={
                "select": "*",
                "tipo_plan": "eq.demo",
                "estado": "eq.activo",
                "order": "created_at.desc",
                "limit": "500",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        rs.raise_for_status()
        subs = rs.json() if rs.content else []

        ahora = datetime.now(pytz.UTC)
        activas = {}
        for s in subs:
            eid = str(s.get("empresa_id") or "").strip()
            if not eid:
                continue
            fin = _parse_iso(s.get("demo_fin"))
            if fin and ahora >= fin.astimezone(pytz.UTC):
                continue
            limite = int(s.get("limite_mensajes") or DEMO_LIMITE_MENSAJES_DEFAULT)
            usados = int(s.get("mensajes_usados") or 0)
            if limite and usados >= limite:
                continue
            activas[eid] = dict(s)

        ids = list(activas.keys())
        if not ids:
            return []

        re_ = requests.get(
            f"{SUPABASE_URL}/rest/v1/empresas",
            headers=headers,
            params={
                "select": "id,nombre,activo",
                "id": f"in.({','.join(ids)})",
                "activo": "eq.true",
                "limit": "500",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        re_.raise_for_status()
        empresas = re_.json() if re_.content else []
        empresa_por_id = {str(e.get("id") or ""): e for e in empresas}

        rp = requests.get(
            f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",
            headers=headers,
            params={
                "select": "empresa_id,datos",
                "empresa_id": f"in.({','.join(ids)})",
                "limit": "500",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        rp.raise_for_status()
        perfiles = rp.json() if rp.content else []
        perfil_por_id = {
            str(x.get("empresa_id") or ""): (x.get("datos") or {})
            for x in perfiles
        }

        acceso_por_empresa = {}
        try:
            ra = requests.get(
                f"{SUPABASE_URL}/rest/v1/demo_accesos",
                headers=headers,
                params={
                    "select": "empresa_id,canal,identificador_cliente,activo,inicio,fin",
                    "empresa_id": f"in.({','.join(ids)})",
                    "activo": "eq.true",
                    "limit": "500",
                },
                timeout=SUPABASE_TIMEOUT,
            )
            if ra.ok:
                for row in (ra.json() if ra.content else []):
                    eid = str(row.get("empresa_id") or "").strip()
                    if eid and eid not in acceso_por_empresa:
                        acceso_por_empresa[eid] = dict(row)
        except Exception as e:
            print("NEXI ROUTER DEMO ACCESS OPTIONAL WARN:", repr(e))

        salida = []
        for eid in ids:
            emp = empresa_por_id.get(eid)
            if not emp:
                continue
            datos = perfil_por_id.get(eid) or {}
            nombre = str(emp.get("nombre") or datos.get("nombre_negocio") or "Negocio Nexia").strip()
            rubro = str(datos.get("rubro") or "").strip()
            asistente = str(datos.get("nombre_asistente") or "").strip()
            description = rubro or (f"Probar el asistente {asistente}" if asistente else "Asistente en prueba de Nexia")

            demo_access = acceso_por_empresa.get(eid) or {
                "empresa_id": eid,
                "canal": "publico",
                "identificador_cliente": "",
                "activo": True,
                "inicio": activas[eid].get("demo_inicio"),
                "fin": activas[eid].get("demo_fin"),
            }

            salida.append({
                "empresa_id": eid,
                "nombre": nombre,
                "description": description[:72],
                "demo_access": demo_access,
            })

        return salida
    except Exception as e:
        print("NEXI ROUTER DEMOS PUBLICAS ERROR:", repr(e))
        return []


def router_demo_publica_por_empresa(empresa_id):
    """Valida que una demo concreta siga visible/activa."""
    empresa_id = str(empresa_id or "").strip()
    if not empresa_id:
        return None
    for demo in router_demos_publicas_activas():
        if str(demo.get("empresa_id") or "") == empresa_id:
            return demo
    return None


def router_empresas_pagadas_activas():
    """Devuelve empresas pagadas y activas visibles en recepción. Diego se agrega aparte."""
    headers = _router_headers()
    if not headers:
        return []
    try:
        rp = requests.get(
            f"{SUPABASE_URL}/rest/v1/suscripciones_empresa",
            headers=headers,
            params={
                "select": "*",
                "estado": "eq.activo",
                "tipo_plan": "in.(nexia_500,nexia_1000)",
                "order": "updated_at.asc",
                "limit": "500",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        rp.raise_for_status()
        planes = rp.json() if rp.content else []
        ids = []
        ahora = datetime.now(pytz.UTC)
        for p in planes:
            fin = _parse_iso(p.get("periodo_fin"))
            if fin and ahora >= fin.astimezone(pytz.UTC):
                continue
            limite = int(p.get("limite_mensajes") or 0)
            usados = int(p.get("mensajes_usados") or 0)
            if limite and usados >= limite:
                continue
            eid = str(p.get("empresa_id") or "").strip()
            if eid and eid != str(DIEGO_EMPRESA_ID or "").strip() and eid not in ids:
                ids.append(eid)
        if not ids:
            return []

        re_ = requests.get(
            f"{SUPABASE_URL}/rest/v1/empresas",
            headers=headers,
            params={
                "select": "id,nombre,activo",
                "id": f"in.({','.join(ids)})",
                "activo": "eq.true",
                "limit": "500",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        re_.raise_for_status()
        empresas = re_.json() if re_.content else []
        por_id = {str(e.get("id") or ""): e for e in empresas}
        out = []
        for eid in ids:
            e = por_id.get(eid)
            if not e:
                continue
            nombre = str(e.get("nombre") or "Negocio Nexia").strip() or "Negocio Nexia"
            out.append({"empresa_id": eid, "nombre": nombre, "motor": "core", "origen": "pagado"})
        return out
    except Exception as e:
        print("NEXI ROUTER EMPRESAS PAGADAS ERROR:", repr(e))
        return []


def router_opciones_menu(telefono, pagina=0, canal="whatsapp"):
    """Genera el menú global: acceso propio, Diego, empresas pagadas y demos públicas."""
    pagadas = router_empresas_pagadas_activas()
    demos = router_demos_publicas_activas()
    pagina = max(0, int(pagina or 0))

    todas = []

    propio = router_asistente_propio(telefono) if str(canal or 'whatsapp').lower() == 'whatsapp' else None
    if propio:
        empresa_id_propio = str(propio.get("empresa_id") or "").strip()
        nombre_propio = "Mi asistente"
        try:
            cfg_propia = cargar_empresa_config(empresa_id_propio, canal=canal, provider="router")
            nombre_propio = str(cfg_propia.get("empresa_nombre") or "Mi asistente").strip() or "Mi asistente"
        except Exception:
            pass

        origen_propio = str(propio.get("origen") or "demo").lower()
        todas.append({
            "id": "nexi:mi_asistente",
            "item": ("🟢 Mi asistente" if origen_propio == "pagado" else "⚪ Mi asistente"),
            "description": (
                f"Producción · {nombre_propio}"
                if origen_propio == "pagado"
                else f"Demo · {nombre_propio}"
            )[:72],
            "empresa_id": empresa_id_propio,
            "motor": "core",
            "origen": origen_propio,
            "demo_access": propio.get("demo_access"),
        })
    else:
        # Usuario nuevo: el mismo menú de WhatsApp lo lleva al onboarding.
        todas.append({
            "id": "nexi:nueva_prueba",
            "item": "Probar Nexia gratis",
            "description": "Crea tu asistente y pruébalo gratis",
            "motor": "core",
            "origen": "nueva_prueba",
        })

    todas.append({
        "id": "nexi:diego",
        "item": _router_item_produccion("Diego Estilista"),
        "description": "Producción · Peluquería y estilismo",
        "empresa_id": str(DIEGO_EMPRESA_ID or ""),
        "motor": "legacy",
        "origen": "diego",
    })

    for e in pagadas:
        nombre = str(e.get("nombre") or "Negocio Nexia").strip()

        # V3.4.12: ocultar solo estas dos entradas del menú público.
        # No se eliminan de Supabase ni del Portal.
        if normalizar_texto(nombre) == "administracion general":
            continue
        if nombre == "Nexia":
            continue

        todas.append({
            "id": f"nexi:empresa:{e['empresa_id']}",
            "item": _router_item_produccion(nombre),
            "description": (
                "Producción · Soluciones de automatización"
                if nombre.strip().upper() == "NEXIA"
                else f"Producción · Conversar con {nombre}"
            )[:72],
            "empresa_id": e["empresa_id"],
            "motor": "core",
            "origen": "pagado",
        })

    # Las demos activas son visibles para cualquier usuario del número compartido.
    # Se identifican claramente con "/ Demo".
    for demo in demos:
        eid = str(demo.get("empresa_id") or "").strip()
        if not eid:
            continue
        if propio and eid == str(propio.get("empresa_id") or "").strip():
            continue
        nombre = str(demo.get("nombre") or "Negocio Nexia").strip()
        todas.append({
            "id": f"nexi:prueba:{eid}",
            "item": _router_item_demo(nombre),
            "description": ("Demo · " + str(demo.get("description") or "Asistente en prueba de Nexia"))[:72],
            "empresa_id": eid,
            "motor": "core",
            "origen": "demo",
            "demo_access": demo.get("demo_access") or {"empresa_id": eid},
        })

    # Página: 9 opciones + "Ver más" cuando aún quedan; última página hasta 10.
    inicio = pagina * ROUTER_LIST_PAGE_SIZE
    if inicio >= len(todas) and pagina > 0:
        pagina = 0
        inicio = 0
    restantes = todas[inicio:]
    if len(restantes) > 10:
        opciones = restantes[:ROUTER_LIST_PAGE_SIZE]
        opciones.append({
            "id": f"nexi:pagina:{pagina + 1}",
            "item": "Ver más negocios",
            "description": "Mostrar más empresas activas",
            "pagina": pagina + 1,
            "origen": "pagina",
        })
        return opciones
    return restantes[:10]


def router_menu_superior(telefono, pagina=0, canal="whatsapp"):
    """Fallback textual del menú. Twilio usa list-picker cuando puede."""
    opciones = router_opciones_menu(telefono, pagina=pagina, canal=canal)
    lineas = ["Hola 👋 Bienvenido a Nexia.", "¿Con quién quieres hablar?", ""]
    for i, op in enumerate(opciones, 1):
        lineas.append(f"{i}. {op.get('item') or 'Opción'}")
    lineas += ["", "Toca una opción del menú. También puedes escribir *MENU* cuando quieras cambiar de negocio."]
    return "\n".join(lineas)



def router_opciones_instagram(identificador):
    """
    Instagram no depende del list-picker de Twilio.
    Devuelve todas las opciones visibles en una sola lista numerada.
    """
    out = []
    pagina = 0
    vistos = set()
    while pagina < 20:
        page = router_opciones_menu(identificador, pagina=pagina, canal="instagram")
        if not page:
            break
        hay_mas = False
        for op in page:
            op_id = str(op.get("id") or "")
            if op_id.startswith("nexi:pagina:"):
                hay_mas = True
                continue
            if op_id and op_id not in vistos:
                vistos.add(op_id)
                out.append(op)
        if not hay_mas:
            break
        pagina += 1
    return out


def router_menu_instagram(identificador):
    opciones = router_opciones_instagram(identificador)
    lineas = [
        "Hola 👋 Bienvenido a Nexia.",
        "¿Con quién quieres hablar?",
        "",
    ]
    for i, op in enumerate(opciones, 1):
        item = str(op.get("item") or f"Opción {i}")
        desc = str(op.get("description") or "").strip()
        lineas.append(f"{i}. {item}" + (f" — {desc}" if desc else ""))
    lineas += [
        "",
        "Responde con el número de la opción.",
        "También puedes escribir *MENU* cuando quieras cambiar de negocio.",
    ]
    return "\n".join(lineas)


def router_instagram_resolver(identificador, texto):
    """
    Misma recepción lógica que WhatsApp, adaptada a texto de Instagram.
    """
    raw = str(texto or "").strip()
    norm = normalizar_texto(raw)

    if norm in {normalizar_texto(x) for x in ROUTER_MENU_COMMANDS} or norm == "menu":
        router_contexto_borrar(identificador, canal="instagram")
        return {"accion":"menu","respuesta":router_menu_instagram(identificador)}

    # Si no hay contexto y escribe un número, lo resolvemos contra la misma
    # lista de negocios/demos visible en WhatsApp.
    if re.fullmatch(r"\d{1,3}", norm):
        idx = int(norm) - 1
        opciones = router_opciones_instagram(identificador)
        if 0 <= idx < len(opciones):
            op_id = str(opciones[idx].get("id") or "")
            return router_superior_resolver(
                identificador,
                op_id,
                canal="instagram",
            )

    return router_superior_resolver(
        identificador,
        raw,
        canal="instagram",
    )


def _router_twilio_credenciales():
    sid = str(TWILIO_ACCOUNT_SID or "").strip()
    token = str(TWILIO_AUTH_TOKEN or "").strip()
    sender = str(TWILIO_WHATSAPP_FROM or "").strip()
    if not sid or not token or not sender:
        raise RuntimeError("Falta configuración Twilio para el menú interactivo")
    from_value = sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}"
    return sid, token, from_value



def _twilio_item_limit(texto, max_units=24):
    """
    Twilio valida el título del item con límite de 24 unidades UTF-16.
    Un emoji como 🟢 ocupa 2 unidades aunque Python len() cuente 1.
    """
    texto = str(texto or "").strip()
    out = []
    units = 0
    for ch in texto:
        ch_units = len(ch.encode("utf-16-le")) // 2
        if units + ch_units > max_units:
            break
        out.append(ch)
        units += ch_units
    return "".join(out).strip()


def _router_twilio_content_sid(opciones):
    """
    Crea/reutiliza un list-picker REAL para el menú superior.

    V3.4.10:
    - Los ítems son estáticos dentro del ContentSid.
    - No usa variables dentro de item/id/description.
    - Evita el fallback a texto provocado por plantillas dinámicas incompatibles.
    """
    opciones = list(opciones or [])[:10]
    if not opciones:
        raise RuntimeError("No hay opciones para crear el menú interactivo")

    firma_src = json.dumps(
        [
            {
                "item": _twilio_item_limit(op.get("item") or "", 24),
                "id": str(op.get("id") or "")[:200],
                "description": str(op.get("description") or "Seleccionar")[:72],
            }
            for op in opciones
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    firma = hashlib.sha1(firma_src.encode("utf-8")).hexdigest()[:16]
    cache_key = f"menu:{firma}"

    with ROUTER_TWILIO_CONTENT_CACHE_LOCK:
        sid_cache = ROUTER_TWILIO_CONTENT_CACHE.get(cache_key)
        if sid_cache:
            return sid_cache

    account_sid, auth_token, _ = _router_twilio_credenciales()

    items = []
    for i, op in enumerate(opciones, 1):
        item = _twilio_item_limit(op.get("item") or f"Opción {i}", 24)
        item_id = str(op.get("id") or f"nexi:opcion:{i}")[:200].strip()
        description = str(op.get("description") or "Seleccionar")[:72].strip()
        items.append({
            "item": item,
            "id": item_id,
            "description": description,
        })

    payload = {
        "friendly_name": f"nexia_router_menu_{firma}",
        "language": "es",
        "types": {
            "twilio/list-picker": {
                "body": "Hola 👋 Bienvenido a Nexia.\n¿Con quién quieres hablar?",
                "button": "Ver opciones",
                "items": items,
            }
        },
    }

    r = requests.post(
        "https://content.twilio.com/v1/Content",
        auth=(account_sid, auth_token),
        json=payload,
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(
            f"Twilio Content API menú HTTP {r.status_code}: {r.text[:500]}"
        )

    data = r.json() if r.content else {}
    content_sid = str(data.get("sid") or "").strip()
    if not content_sid:
        raise RuntimeError("Twilio no devolvió ContentSid para el menú")

    with ROUTER_TWILIO_CONTENT_CACHE_LOCK:
        ROUTER_TWILIO_CONTENT_CACHE[cache_key] = content_sid

    print("NEXI ROUTER CONTENT SID OK:", content_sid, "opciones=", len(items))
    return content_sid


def enviar_twilio_menu_interactivo(destino, pagina=0):
    """Envía la recepción Nexia como menú desplegable/list-picker de WhatsApp."""
    opciones = router_opciones_menu(destino, pagina=pagina)
    if not opciones:
        return False

    try:
        account_sid, auth_token, from_value = _router_twilio_credenciales()
        content_sid = _router_twilio_content_sid(opciones)

        to_digits = _router_identificador(destino)
        to_value = f"whatsapp:+{to_digits}"

        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "To": to_value,
                "From": from_value,
                "ContentSid": content_sid,
            },
            timeout=20,
        )

        if not r.ok:
            raise RuntimeError(
                f"Twilio Messages API menú HTTP {r.status_code}: {r.text[:500]}"
            )

        data = r.json() if r.content else {}
        print(
            "NEXI ROUTER LISTA TWILIO OK:",
            "pagina=", pagina,
            "opciones=", len(opciones),
            "message_sid=", data.get("sid"),
            "content_sid=", content_sid,
        )
        return True

    except Exception as e:
        print("NEXI ROUTER LISTA TWILIO ERROR:", repr(e))
        return False



# V2.3.1: listas interactivas reutilizables para servicios y horas de agenda.
AGENDA_TWILIO_CONTENT_CACHE = {}
AGENDA_TWILIO_CONTENT_CACHE_LOCK = Lock()
AGENDA_LIST_PAGE_SIZE = 9  # 9 opciones + "Ver más" cuando corresponda.


def _agenda_twilio_content_sid(tipo, cantidad):
    """Crea/reutiliza un list-picker Twilio para servicios u horas."""
    tipo = str(tipo or "").strip().lower()
    if tipo not in {"servicios", "horas"}:
        raise ValueError("Tipo de lista de agenda no soportado")
    cantidad = max(1, min(10, int(cantidad)))
    cache_key = (tipo, cantidad)
    with AGENDA_TWILIO_CONTENT_CACHE_LOCK:
        sid_cache = AGENDA_TWILIO_CONTENT_CACHE.get(cache_key)
        if sid_cache:
            return sid_cache

    account_sid, auth_token, _ = _router_twilio_credenciales()
    variables = {}
    items = []
    for i in range(1, cantidad + 1):
        variables[f"i{i}"] = f"Opción {i}"
        variables[f"id{i}"] = f"agenda-opcion-{i}"
        variables[f"d{i}"] = "Seleccionar"
        items.append({
            "item": f"{{{{i{i}}}}}",
            "id": f"{{{{id{i}}}}}",
            "description": f"{{{{d{i}}}}}",
        })

    if tipo == "servicios":
        body = "Claro 😊 ¿Qué servicio quieres agendar?"
        button = "Ver servicios"
        friendly = f"nexia_agenda_servicios_{cantidad}"
    else:
        body = "Tengo estas horas disponibles 👇 Selecciona la que prefieras."
        button = "Ver horas"
        friendly = f"nexia_agenda_horas_{cantidad}"

    payload = {
        "friendly_name": friendly,
        "language": "es",
        "variables": variables,
        "types": {
            "twilio/list-picker": {
                "body": body,
                "button": button,
                "items": items,
            }
        },
    }
    r = requests.post(
        "https://content.twilio.com/v1/Content",
        auth=(account_sid, auth_token),
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json() if r.content else {}
    content_sid = str(data.get("sid") or "").strip()
    if not content_sid:
        raise RuntimeError("Twilio no devolvió ContentSid para agenda")
    with AGENDA_TWILIO_CONTENT_CACHE_LOCK:
        AGENDA_TWILIO_CONTENT_CACHE[cache_key] = content_sid
    return content_sid


def _agenda_enviar_lista_twilio(destino, tipo, opciones):
    """Envía una lista interactiva de agenda; cada opción lleva item/id/description."""
    if not opciones:
        return False
    try:
        account_sid, auth_token, from_value = _router_twilio_credenciales()
        content_sid = _agenda_twilio_content_sid(tipo, len(opciones))
        variables = {}
        for i, op in enumerate(opciones, 1):
            variables[f"i{i}"] = str(op.get("item") or f"Opción {i}")[:24]
            variables[f"id{i}"] = str(op.get("id") or f"agenda:opcion:{i}")[:200]
            variables[f"d{i}"] = str(op.get("description") or "Seleccionar")[:72]
        to_digits = _router_identificador(destino)
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "To": f"whatsapp:+{to_digits}",
                "From": from_value,
                "ContentSid": content_sid,
                "ContentVariables": json.dumps(variables, ensure_ascii=False),
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json() if r.content else {}
        print("NEXI AGENDA LISTA TWILIO OK:", tipo, len(opciones), data.get("sid"))
        return True
    except Exception as e:
        print("NEXI AGENDA LISTA TWILIO ERROR:", tipo, repr(e))
        return False


def _agenda_servicios_opciones(pagina=0):
    servicios = sorted(
        servicios_actuales().items(),
        key=lambda kv: (int(kv[1].get("numero") or 9999), str(kv[1].get("nombre") or "")),
    )
    pagina = max(0, int(pagina or 0))
    inicio = pagina * AGENDA_LIST_PAGE_SIZE
    chunk = servicios[inicio: inicio + AGENDA_LIST_PAGE_SIZE]
    opciones = []
    for codigo, s in chunk:
        numero = int(s.get("numero") or (inicio + len(opciones) + 1))
        nombre = str(s.get("nombre") or codigo)
        precio = str(s.get("precio_texto") or "Valor por confirmar")
        opciones.append({
            "item": nombre,
            "id": f"agenda:servicio_num:{numero}",
            "description": precio,
        })
    if inicio + AGENDA_LIST_PAGE_SIZE < len(servicios):
        opciones.append({
            "item": "Ver más servicios",
            "id": f"agenda:servicios_pagina:{pagina + 1}",
            "description": "Mostrar más opciones",
        })
    elif pagina > 0:
        opciones.append({
            "item": "Volver al inicio",
            "id": "agenda:servicios_pagina:0",
            "description": "Ver primeros servicios",
        })
    return opciones[:10]


def _agenda_horas_opciones(estado, pagina=0):
    ofrecidas = list((estado or {}).get("horas_ofrecidas") or [])
    pagina = max(0, int(pagina or 0))
    inicio = pagina * AGENDA_LIST_PAGE_SIZE
    chunk = ofrecidas[inicio: inicio + AGENDA_LIST_PAGE_SIZE]
    opciones = []
    for offset, iso in enumerate(chunk):
        idx_global = inicio + offset + 1
        try:
            slot = datetime.fromisoformat(iso)
            titulo = slot.astimezone(zona_local()).strftime("%H:%M")
            descripcion = formatear_fecha(slot)
        except Exception:
            titulo = f"Hora {idx_global}"
            descripcion = str(iso)
        opciones.append({
            "item": titulo,
            "id": f"agenda:hora_num:{idx_global}",
            "description": descripcion,
        })
    if inicio + AGENDA_LIST_PAGE_SIZE < len(ofrecidas):
        opciones.append({
            "item": "Ver más horas",
            "id": f"agenda:horas_pagina:{pagina + 1}",
            "description": "Mostrar más horarios",
        })
    elif pagina > 0:
        opciones.append({
            "item": "Volver al inicio",
            "id": "agenda:horas_pagina:0",
            "description": "Ver primeras horas",
        })
    return opciones[:10]


def enviar_twilio_agenda_interactiva(destino, estado, pagina_servicios=0, pagina_horas=0):
    """Según el paso actual, reemplaza el listado textual por un list-picker clickeable."""
    paso = str((estado or {}).get("paso") or "inicio")
    if paso == "servicio":
        return _agenda_enviar_lista_twilio(destino, "servicios", _agenda_servicios_opciones(pagina_servicios))
    if paso == "seleccionar_hora":
        return _agenda_enviar_lista_twilio(destino, "horas", _agenda_horas_opciones(estado, pagina_horas))
    return False


def agenda_payload_a_texto(payload):
    """
    Convierte una selección interactiva en el número que ya entiende procesar_agenda().

    Twilio puede entregar el ListId/ButtonPayload escapado, por ejemplo:
    agenda\:hora\_num:1
    agenda\:servicio\_num:2

    Para la lógica interna quitamos esos backslashes antes de interpretar el id.
    """
    original = str(payload or "").strip()
    raw = original.replace("\\", "").strip().lower()
    m = re.fullmatch(r"agenda:(?:servicio_num|hora_num):(\d{1,3})", raw)
    return m.group(1) if m else original

def router_payload_interactivo(request_form):
    """Extrae el id de quick-reply/list-picker que Twilio envía al webhook."""
    # Quick reply / botones
    payload = str(request_form.get("ButtonPayload") or "").strip()
    if payload:
        if payload.replace("\\", "").lower().startswith(("agenda:", "pago:")):
            return payload.replace("\\", "")
        return payload

    # Twilio list-picker envía la selección en ListId.
    list_id = str(request_form.get("ListId") or "").strip()
    if list_id:
        if list_id.replace("\\", "").lower().startswith(("agenda:", "pago:")):
            return list_id.replace("\\", "")
        return list_id

    # Compatibilidad con respuestas ricas normalizadas.
    interactive = str(request_form.get("InteractiveData") or "").strip()
    if interactive:
        try:
            data = json.loads(interactive)
            for key in ("id", "payload", "button_payload", "buttonPayload", "list_id", "listId"):
                val = data.get(key) if isinstance(data, dict) else None
                if val:
                    return str(val)
        except Exception:
            pass
    return ""



# ============================================================
# V2.4 - PAGO INTERACTIVO EN WHATSAPP
# ============================================================

PAGO_TWILIO_CONTENT_CACHE = {}
PAGO_TWILIO_CONTENT_CACHE_LOCK = Lock()


def _pago_twilio_content_sid(cantidad):
    cantidad = max(1, min(10, int(cantidad)))
    with PAGO_TWILIO_CONTENT_CACHE_LOCK:
        sid_cache = PAGO_TWILIO_CONTENT_CACHE.get(cantidad)
        if sid_cache:
            return sid_cache

    account_sid, auth_token, _ = _router_twilio_credenciales()
    variables = {}
    items = []
    for i in range(1, cantidad + 1):
        variables[f"i{i}"] = f"Plan {i}"
        variables[f"id{i}"] = f"pago:opcion:{i}"
        variables[f"d{i}"] = "Seleccionar plan"
        items.append({
            "item": f"{{{{i{i}}}}}",
            "id": f"{{{{id{i}}}}}",
            "description": f"{{{{d{i}}}}}",
        })

    payload = {
        "friendly_name": f"nexia_pago_planes_{cantidad}",
        "language": "es",
        "variables": variables,
        "types": {
            "twilio/list-picker": {
                "body": "Tu prueba o bolsa de mensajes finalizó. Elige un plan para seguir usando Nexia 👇",
                "button": "Ver planes",
                "items": items,
            }
        },
    }
    r = requests.post(
        "https://content.twilio.com/v1/Content",
        auth=(account_sid, auth_token),
        json=payload,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json() if r.content else {}
    content_sid = str(data.get("sid") or "").strip()
    if not content_sid:
        raise RuntimeError("Twilio no devolvió ContentSid para planes")
    with PAGO_TWILIO_CONTENT_CACHE_LOCK:
        PAGO_TWILIO_CONTENT_CACHE[cantidad] = content_sid
    return content_sid


def _pago_opciones_whatsapp(empresa_id):
    empresa_id = str(empresa_id or "").strip()
    opciones = []
    for codigo in ("nexia_500", "nexia_1000"):
        plan = NEXIA_PLANES.get(codigo)
        if not plan:
            continue
        precio = f"${int(plan['precio']):,}".replace(",", ".")
        opciones.append({
            "item": f"{plan['nombre']} · {precio}",
            "id": f"pago:plan:{codigo}:{empresa_id}",
            "description": f"{int(plan['mensajes'])} mensajes · pago único",
        })
    return opciones


def enviar_twilio_pago_interactivo(destino, empresa_id):
    opciones = _pago_opciones_whatsapp(empresa_id)
    if not opciones:
        return False
    try:
        account_sid, auth_token, from_value = _router_twilio_credenciales()
        content_sid = _pago_twilio_content_sid(len(opciones))
        variables = {}
        for i, op in enumerate(opciones, 1):
            variables[f"i{i}"] = str(op["item"])[:24]
            variables[f"id{i}"] = str(op["id"])[:200]
            variables[f"d{i}"] = str(op["description"])[:72]
        to_digits = _router_identificador(destino)
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
            auth=(account_sid, auth_token),
            data={
                "To": f"whatsapp:+{to_digits}",
                "From": from_value,
                "ContentSid": content_sid,
                "ContentVariables": json.dumps(variables, ensure_ascii=False),
            },
            timeout=20,
        )
        r.raise_for_status()
        data = r.json() if r.content else {}
        print("NEXI PAGO LISTA TWILIO OK:", empresa_id, data.get("sid"))
        return True
    except Exception as e:
        print("NEXI PAGO LISTA TWILIO ERROR:", empresa_id, repr(e))
        return False



def _resolver_seleccion_pago_whatsapp(request_form, telefono):
    """
    Reconoce la selección de plan aunque Twilio entregue el ID en ButtonPayload
    o solamente el texto visible en ButtonText/Body.
    Devuelve (codigo_plan, empresa_id) o (None, None).
    """
    payload = str(
        request_form.get("ButtonPayload")
        or request_form.get("ListId")
        or ""
    ).strip()
    button_text = str(
        request_form.get("ButtonText")
        or request_form.get("ListTitle")
        or ""
    ).strip()
    body = str(request_form.get("Body") or "").strip()

    # Algunos logs/copias muestran caracteres escapados con backslash.
    # Normalizamos sólo para reconocer el identificador interno.
    payload_match = payload.replace("\\", "")
    body_match = body.replace("\\", "")

    # Camino ideal: payload/list-id con plan + empresa.
    m = re.fullmatch(
        r"pago:plan:(nexia_500|nexia_1000):([0-9a-fA-F-]{36})",
        payload_match,
        flags=re.IGNORECASE,
    )
    if not m:
        # Fallback: algunos clientes reflejan el id en Body.
        m = re.fullmatch(
            r"pago:plan:(nexia_500|nexia_1000):([0-9a-fA-F-]{36})",
            body_match,
            flags=re.IGNORECASE,
        )
    if m:
        return m.group(1).lower(), m.group(2)

    # Fallback para clientes/list-picker que devuelven en Body el bloque completo
    # del mensaje interactivo + la opción seleccionada, en vez de sólo el título.
    visible = normalizar_texto(button_text or body)

    tiene_500 = bool(re.search(r"\bnexia\s*500\b", visible))
    tiene_1000 = bool(re.search(r"\bnexia\s*1000\b", visible))

    codigo = None
    if tiene_500 and not tiene_1000:
        codigo = "nexia_500"
    elif tiene_1000 and not tiene_500:
        codigo = "nexia_1000"
    else:
        # Si por algún motivo llegaron ambas opciones o ninguna, no adivinamos.
        return None, None

    # Primero usa el contexto activo del Router.
    try:
        actual = router_contexto_obtener(telefono) or {}
        empresa_id = str(actual.get("empresa_id") or "").strip()
        if empresa_id:
            return codigo, empresa_id
    except Exception:
        pass

    # Si no existe contexto, intenta recuperar la prueba asociada a ese WhatsApp.
    try:
        demo = router_demo_access_sin_activar(telefono) or {}
        empresa_id = str(demo.get("empresa_id") or "").strip()
        if empresa_id:
            return codigo, empresa_id
    except Exception:
        pass

    return codigo, None


def _pago_empresa_autorizada_whatsapp(telefono, empresa_id):
    empresa_id = str(empresa_id or "").strip()
    if not empresa_id:
        return False
    try:
        actual = router_contexto_obtener(telefono) or {}
        if str(actual.get("empresa_id") or "").strip() == empresa_id:
            return True
    except Exception:
        pass
    try:
        ident = _normalizar_identificador_demo(telefono, "whatsapp")
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/demo_accesos",
            headers=backend_headers(),
            params={
                "select": "empresa_id",
                "empresa_id": f"eq.{empresa_id}",
                "canal": "eq.whatsapp",
                "identificador_cliente": f"eq.{ident}",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        if r.ok and (r.json() if r.content else []):
            return True
    except Exception as e:
        print("NEXI PAGO AUTH DEMO WARN:", repr(e))
    return False


def _crear_checkout_whatsapp(empresa_id, codigo):
    empresa_id = str(empresa_id or "").strip()
    codigo = str(codigo or "").strip().lower()
    plan = NEXIA_PLANES.get(codigo)
    if not empresa_id or not plan:
        raise RuntimeError("Empresa o plan inválido")
    headers_mp = _mp_headers()
    if not headers_mp:
        raise RuntimeError("Mercado Pago no está configurado")

    perfil = _core_profile(empresa_id) or {}
    datos = perfil.get("datos") or {}
    email = str(datos.get("email_contacto") or "").strip().lower()
    demo_token = str(_core_token_por_empresa(empresa_id) or "").strip()

    external_reference = f"NEXIA:{empresa_id}:{codigo}:{uuid.uuid4().hex[:12]}"
    suffix = f"&demo_token={demo_token}" if demo_token else ""

    payload = {
        "items": [{
            "id": codigo,
            "title": f"{plan['nombre']} - {plan['mensajes']} mensajes",
            "quantity": 1,
            "currency_id": plan["moneda"],
            "unit_price": int(plan["precio"]),
        }],
        "external_reference": external_reference,
        "metadata": {
            "empresa_id": empresa_id,
            "plan_codigo": codigo,
            "mensajes": int(plan["mensajes"]),
            "origen": "whatsapp",
        },
        "back_urls": {
            "success": f"{PORTAL_ORIGIN}/portal.html?pago=success{suffix}",
            "pending": f"{PORTAL_ORIGIN}/portal.html?pago=pending{suffix}",
            "failure": f"{PORTAL_ORIGIN}/portal.html?pago=failure{suffix}",
        },
        "auto_return": "approved",
        "notification_url": MERCADOPAGO_WEBHOOK_URL,
        "statement_descriptor": "NEXIA",
    }
    if email:
        payload["payer"] = {"email": email}

    r = requests.post(
        f"{MERCADOPAGO_API_BASE}/checkout/preferences",
        headers=headers_mp,
        json=payload,
        timeout=20,
    )
    if not r.ok:
        print("MERCADOPAGO WHATSAPP PREFERENCE ERROR:", r.status_code, r.text[:1200])
        raise RuntimeError("No fue posible iniciar el pago")
    pref = r.json() if r.content else {}
    checkout_url = str(pref.get("init_point") or pref.get("sandbox_init_point") or "").strip()
    if not checkout_url:
        raise RuntimeError("Mercado Pago no devolvió URL de pago")

    registro = {
        "empresa_id": empresa_id,
        "plan_codigo": codigo,
        "plan_nombre": plan["nombre"],
        "mensajes": int(plan["mensajes"]),
        "monto": int(plan["precio"]),
        "moneda": plan["moneda"],
        "preference_id": str(pref.get("id") or ""),
        "external_reference": external_reference,
        "status": "created",
        "email_cliente": email or None,
        "metadata": {"origen": "whatsapp"},
        "updated_at": datetime.now(pytz.UTC).isoformat(),
    }
    rr = requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers={**backend_headers(), "Prefer": "return=representation"},
        json=registro,
        timeout=SUPABASE_TIMEOUT,
    )
    rr.raise_for_status()

    return {"checkout_url": checkout_url, "plan": plan, "external_reference": external_reference}


def _mensaje_checkout_whatsapp(checkout):
    plan = checkout["plan"]
    precio = f"${int(plan['precio']):,}".replace(",", ".")
    return (
        f"💳 *{plan['nombre']}*\n"
        f"• {int(plan['mensajes'])} mensajes\n"
        f"• {precio} CLP\n\n"
        "Paga de forma segura en Mercado Pago desde este enlace:\n"
        f"{checkout['checkout_url']}\n\n"
        "Cuando Mercado Pago confirme el pago, Nexia activará el plan automáticamente."
    )


def _es_fin_plan_para_pago(respuesta):
    t = normalizar_texto(respuesta)
    return (
        "prueba gratuita de nexia ya finalizo" in t
        or "periodo de prueba de 24 horas ya finalizo" in t
        or "mensajes gratuitos incluidas en tu prueba" in t
        or "utilizado todos los mensajes disponibles de tu plan nexia" in t
    )

def router_bienvenida_contexto(route):
    empresa_id = str((route or {}).get("empresa_id") or "").strip()
    motor = str((route or {}).get("motor") or "core").lower()
    origen = str((route or {}).get("origen") or "").lower()
    telefono = str((route or {}).get("telefono") or "")
    if not empresa_id:
        return router_menu_superior("")
    activar_por_empresa(empresa_id, canal="whatsapp", provider="router")
    if motor == "legacy":
        reset_estado(telefono)
        return mensaje_bienvenida()

    empresa = str(cfg("empresa_nombre", "este negocio") or "este negocio").strip()
    asistente = str(cfg("asistente_nombre", "asistente virtual") or "asistente virtual").strip()

    if origen == "demo":
        base = (
            f"✅ Entraste a *{empresa} / Demo*\n"
            f"Estás conversando con {asistente}, su asistente virtual. "
            "¿En qué te puedo ayudar?\n\n"
            "_Escribe MENU cuando quieras salir o cambiar de negocio._"
        )

        # Solo el creador de la demo ve instrucciones administrativas.
        propia = router_demo_access_sin_activar(telefono)
        if propia and str(propia.get("empresa_id") or "") == empresa_id:
            base += (
                "\n\n📲 Puedes compartir este mismo número con tus clientes y pedirles "
                f"que seleccionen *{empresa} / Demo* en el menú. "
                "Desde el *Portal Nexia* podrás revisar las conversaciones y tomar la atención cuando quieras."
            )
        return base

    return (
        f"Listo 🙌 Estás conversando con {empresa}.\n"
        f"Soy {asistente}, su asistente virtual. ¿En qué te puedo ayudar?\n\n"
        "_Escribe MENU cuando quieras salir o cambiar de negocio._"
    )


def router_vincular_demo_a_whatsapp(empresa_id, telefono):
    """
    Vincula el WhatsApp que abrió una demo mediante NEXI <codigo>.
    No crea otra demo; registra teléfono -> mismo empresa_id.
    """
    empresa_id = str(empresa_id or "").strip()
    ident = _router_identificador(telefono)
    headers = _router_headers()
    if not empresa_id or not ident or not headers:
        return False
    try:
        plan = estado_suscripcion_empresa(empresa_id)
        if str(plan.get("tipo_plan") or "").lower() != "demo":
            return False
        if str(plan.get("estado") or "").lower() != "activo":
            return False
        if plan.get("vigente") is False:
            return False

        ahora = datetime.now(pytz.UTC).isoformat()
        acceso = {
            "empresa_id": empresa_id,
            "canal": "whatsapp",
            "identificador_cliente": ident,
            "activo": True,
            "inicio": plan.get("demo_inicio") or ahora,
            "fin": plan.get("demo_fin"),
            "updated_at": ahora,
        }
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/demo_accesos",
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "canal,identificador_cliente"},
            json=acceso,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        print("NEXI ROUTER VINCULAR DEMO ERROR:", repr(e))
        return False


def router_superior_resolver(telefono, texto, canal="whatsapp"):
    """Devuelve accion=menu|seleccionado|ruta y la empresa/motor cuando corresponda."""
    if not NEXIA_ROUTER_SUPERIOR_ACTIVO:
        demo = router_demo_access_sin_activar(telefono)
        if demo:
            return {"accion": "ruta", "empresa_id": demo.get("empresa_id"), "motor": "core", "origen": "demo", "demo_access": demo}
        return {"accion": "ruta", "empresa_id": DIEGO_EMPRESA_ID, "motor": "legacy", "origen": "legacy"}

    raw_texto = str(texto or "").strip()
    t = normalizar_texto(raw_texto)

    # Selecciones del list-picker de WhatsApp. No dependen del texto visible.
    if raw_texto.lower().startswith("nexi:pagina:"):
        try:
            pagina = int(raw_texto.rsplit(":", 1)[1])
        except Exception:
            pagina = 0
        router_contexto_borrar(telefono, canal=canal)
        return {"accion": "menu", "pagina": pagina}

    if raw_texto.lower() == "nexi:mi_asistente" and canal == "whatsapp":
        propio = router_asistente_propio(telefono) if canal == "whatsapp" else None
        if not propio:
            return {
                "accion": "menu",
                "respuesta": (
                    "Todavía no tienes un asistente asociado a este WhatsApp.\n\n"
                    f"Puedes crear tu prueba gratis aquí:\n{NEXIA_PRUEBA_URL}"
                ),
            }

        empresa_id = str(propio.get("empresa_id") or "").strip()
        origen = str(propio.get("origen") or "demo").strip().lower()
        row = router_contexto_guardar(telefono, empresa_id, motor="core", origen=origen, canal=canal) or {}
        row.update({
            "accion": "seleccionado",
            "empresa_id": empresa_id,
            "motor": "core",
            "origen": origen,
            "telefono": telefono,
        })
        if origen == "demo":
            row["demo_access"] = propio.get("demo_access") or {"empresa_id": empresa_id}
        return row

    if raw_texto.lower() == "nexi:nueva_prueba":
        router_contexto_borrar(telefono, canal=canal)
        return {
            "accion": "mensaje",
            "respuesta": (
                "🚀 Crea tu asistente Nexia gratis.\n\n"
                "Configura tu negocio y luego podrás probar tu propio asistente por WhatsApp.\n"
                "La prueba incluye 50 mensajes o 24 horas, lo que ocurra primero.\n\n"
                f"👉 {NEXIA_PRUEBA_URL}"
            ),
        }

    if raw_texto.lower() == "nexi:diego":
        row = router_contexto_guardar(telefono, DIEGO_EMPRESA_ID, motor="legacy", origen="diego", canal=canal) or {}
        row.update({"accion": "seleccionado", "empresa_id": DIEGO_EMPRESA_ID, "motor": "legacy", "telefono": telefono})
        return row

    m_empresa = re.fullmatch(r"(?i)nexi:empresa:([0-9a-f-]{36})", raw_texto)
    if m_empresa:
        empresa_id = m_empresa.group(1)
        # Seguridad: una empresa seleccionada desde el menú debe seguir pagada y activa.
        visibles = {e["empresa_id"]: e for e in router_empresas_pagadas_activas()}
        destino = visibles.get(empresa_id)
        if not destino:
            return {"accion": "menu", "respuesta": "Ese negocio ya no está disponible.\n\n" + router_menu_superior(telefono, canal=canal)}
        row = router_contexto_guardar(telefono, empresa_id, motor="core", origen="pagado", canal=canal) or {}
        row.update({"accion": "seleccionado", "empresa_id": empresa_id, "motor": "core", "telefono": telefono})
        return row

    m_prueba = re.fullmatch(r"(?i)nexi:prueba:([0-9a-f-]{36})", raw_texto)
    if m_prueba:
        empresa_id = m_prueba.group(1)
        demo = router_demo_publica_por_empresa(empresa_id)
        if not demo:
            return {
                "accion": "menu",
                "respuesta": "Esta demo ya no está disponible.\n\n" + router_menu_superior(telefono, canal=canal),
            }
        row = router_contexto_guardar(telefono, empresa_id, motor="core", origen="demo", canal=canal) or {}
        row.update({
            "accion": "seleccionado",
            "empresa_id": empresa_id,
            "motor": "core",
            "demo_access": demo.get("demo_access") or {"empresa_id": empresa_id},
            "telefono": telefono,
        })
        return row

    if t in {normalizar_texto(x) for x in ROUTER_MENU_COMMANDS}:
        router_contexto_borrar(telefono, canal=canal)
        return {"accion": "menu", "pagina": 0}

    actual = router_contexto_obtener(telefono, canal=canal)
    if actual:
        actual = dict(actual)
        actual["accion"] = "ruta"
        if str(actual.get("origen") or "") == "demo":
            demo_publica = router_demo_publica_por_empresa(actual.get("empresa_id"))
            if not demo_publica:
                router_contexto_borrar(telefono, canal=canal)
                return {"accion": "menu", "pagina": 0}
            actual["demo_access"] = demo_publica.get("demo_access") or {
                "empresa_id": actual.get("empresa_id")
            }
        return actual

    # Compatibilidad textual: Diego por nombre/1 y prueba por palabras explícitas.
    if t == "1" or t in {normalizar_texto(x) for x in ROUTER_DIEGO_COMMANDS}:
        row = router_contexto_guardar(telefono, DIEGO_EMPRESA_ID, motor="legacy", origen="diego", canal=canal) or {}
        row.update({"accion": "seleccionado", "empresa_id": DIEGO_EMPRESA_ID, "motor": "legacy", "telefono": telefono})
        return row

    if t in {normalizar_texto(x) for x in ROUTER_DEMO_COMMANDS}:
        propio = router_asistente_propio(telefono) if canal == "whatsapp" else None
        if not propio:
            return {
                "accion": "menu",
                "respuesta": "No encontré una prueba o plan activo asociado a este WhatsApp.\n\n" + router_menu_superior(telefono, canal=canal),
            }
        empresa_id = str(propio.get("empresa_id") or "")
        origen = str(propio.get("origen") or "demo").lower()
        row = router_contexto_guardar(telefono, empresa_id, motor="core", origen=origen, canal=canal) or {}
        row.update({"accion": "seleccionado", "empresa_id": empresa_id, "motor": "core", "origen": origen, "telefono": telefono})
        if origen == "demo":
            row["demo_access"] = propio.get("demo_access") or {"empresa_id": empresa_id}
        return row

    # Fallback por número si el cliente escribe en vez de tocar: usa el orden de la primera página.
    if re.fullmatch(r"\d{1,2}", t):
        idx = int(t) - 1
        opciones = router_opciones_menu(telefono, pagina=0, canal=canal)
        if 0 <= idx < len(opciones):
            op = opciones[idx]
            return router_superior_resolver(telefono, op.get("id") or "")

    codigo = router_codigo_desde_texto(texto)
    if codigo:
        destino = router_destino_por_codigo(codigo)
        if not destino:
            return {"accion": "menu", "respuesta": "Ese acceso no está disponible o ya no es válido.\n\n" + router_menu_superior(telefono, canal=canal)}
        empresa_id = str(destino.get("empresa_id") or "")
        motor = str(destino.get("motor") or "core").lower()

        if motor == "core" and canal == "whatsapp":
            router_vincular_demo_a_whatsapp(empresa_id, telefono)

        row = router_contexto_guardar(telefono, empresa_id, motor=motor, origen="codigo", codigo=codigo, canal=canal) or {}
        row.update({"accion": "seleccionado", "empresa_id": empresa_id, "motor": motor, "codigo": codigo, "telefono": telefono})
        return row

    # Primera entrada sin contexto: recepción. "Hola" no queda amarrado a Diego.
    return {"accion": "menu", "pagina": 0}


def router_activar_ruta(route, provider, canal="whatsapp"):
    empresa_id = str((route or {}).get("empresa_id") or "").strip()
    if not empresa_id:
        raise RuntimeError("Router sin empresa_id")
    activar_por_empresa(empresa_id, canal=canal, provider=provider)
    return empresa_id


# ============================================================
# servicios_actuales()
# ============================================================

SERVICIOS_DEFAULT = {
    "corte_hombre": {
        "numero": 1,
        "nombre": "Corte de cabello hombre",
        "precio": 17000,
        "precio_texto": "$17.000",
        "detalle": "Incluye perfilado de cejas, lavado de cabello y aplicación de producto.",
    },
    "perfilado_barba": {
        "numero": 2,
        "nombre": "Perfilado de barba",
        "precio": 10000,
        "precio_texto": "$10.000",
        "detalle": "",
    },
    "base_rizos": {
        "numero": 3,
        "nombre": "Base de rizos permanente",
        "precio": 65000,
        "precio_texto": "$65.000",
        "detalle": "",
    },
    "mechas_hombre": {
        "numero": 4,
        "nombre": "Mechas",
        "precio": 70000,
        "precio_texto": "desde $70.000",
        "detalle": "",
    },
    "decoloracion_global": {
        "numero": 5,
        "nombre": "Decoloración global",
        "precio": 120000,
        "precio_texto": "$120.000",
        "detalle": "",
    },
    "corte_mujer": {
        "numero": 6,
        "nombre": "Corte de cabello mujer",
        "precio": 30000,
        "precio_texto": "$30.000",
        "detalle": "Incluye lavado de cabello, hidratación y brushing.",
    },
    "masaje_hidratacion": {
        "numero": 7,
        "nombre": "Masaje de hidratación",
        "precio": 45000,
        "precio_texto": "$45.000",
        "detalle": "",
    },
    "botox_capilar": {
        "numero": 8,
        "nombre": "Botox capilar",
        "precio": 65000,
        "precio_texto": "desde $65.000",
        "detalle": "",
    },
    "alisado_permanente": {
        "numero": 9,
        "nombre": "Alisado permanente",
        "precio": 70000,
        "precio_texto": "desde $70.000",
        "detalle": "",
    },
    "retoque_raiz": {
        "numero": 10,
        "nombre": "Retoque de color de raíz",
        "precio": 50000,
        "precio_texto": "$50.000",
        "detalle": "",
    },
    "bano_color": {
        "numero": 11,
        "nombre": "Baño de color",
        "precio": 30000,
        "precio_texto": "$30.000",
        "detalle": "",
    },
    "diagnostico_balayage": {
        "numero": 12,
        "nombre": "Diagnóstico capilar gratuito para Balayage",
        "precio": 0,
        "precio_texto": "Diagnóstico gratuito · Balayage estimado desde $150.000",
        "detalle": "El valor final del Balayage se define después del diagnóstico capilar.",
    },
}

SERVICIO_POR_NUMERO_DEFAULT = {v["numero"]: k for k, v in SERVICIOS_DEFAULT.items()}


def tipo_negocio_actual():
    return normalizar_texto(str(cfg("tipo_negocio", "reservas") or ""))


def negocio_usa_reservas():
    tipo = tipo_negocio_actual()
    tipos_reserva = {
        "reservas", "agenda", "agendamiento", "servicios",
        "peluqueria", "barberia", "salon", "salon de belleza",
        "estilista", "spa", "clinica", "consulta"
    }
    return tipo in tipos_reserva and cfg_modulo("reservas", True)


def negocio_es_comercial():
    return not negocio_usa_reservas()


def mostrar_servicios():
    grupos={}
    for _,s in sorted(servicios_actuales().items(),key=lambda kv:(str(kv[1].get("categoria") or "Servicios"),int(kv[1].get("numero") or 9999))):grupos.setdefault(str(s.get("categoria") or "Servicios"),[]).append(s)
    out=[f"Estos son los servicios de {cfg('empresa_nombre',DEFAULT_NEGOCIO_NOMBRE)} 👇",""]
    for categoria,items in grupos.items():
        out.append(f"📌 {categoria.upper()}")
        for s in items:
            n=f"{s.get('numero')}. " if s.get("numero") is not None else "• ";p=f" — {s.get('precio_texto')}" if s.get("precio_texto") else "";out.append(f"{n}{s.get('nombre')}{p}")
        out.append("")
    if negocio_usa_reservas():
        out.append("Para agendar, responde con el número o nombre del servicio.")
    else:
        out.append("Si te interesa alguno, dime cuál o simplemente escribe *ME INTERESA* y te ayudo a avanzar.")
    return "\n".join(out).strip()

def detectar_servicio(texto):
    t=normalizar_texto(texto);m=re.fullmatch(r"\s*(\d{1,3})\s*",t)
    if m:return servicio_por_numero_actual().get(int(m.group(1)))
    mejor=None;score=0
    for codigo,s in servicios_actuales().items():
        aliases=s.get("aliases") or []
        if isinstance(aliases,str):aliases=[aliases]
        for raw in [codigo,s.get("nombre") or ""]+list(aliases):
            c=normalizar_texto(str(raw))
            if c and (c in t or t in c) and len(c)>score:mejor=codigo;score=len(c)
    return mejor


def corte_ambiguo(texto):
    t = normalizar_texto(texto)
    return "corte" in t and detectar_servicio(texto) is None


# ============================================================
# GOOGLE CALENDAR
# ============================================================

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def google_calendar_conexion(empresa_id=None):
    """Obtiene la conexión privada de Google Calendar para la empresa actual.

    La tabla solo se consulta desde backend con SERVICE_ROLE. Los refresh tokens
    nunca se devuelven al Portal.
    """
    empresa_id = str(empresa_id or empresa_actual_id() or "").strip()
    headers = backend_headers()
    if not empresa_id or not headers:
        return None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/google_calendar_conexiones",
            headers=headers,
            params={
                "select": "empresa_id,google_email,calendar_id,calendar_nombre,refresh_token,access_token,token_expiry,activo,updated_at",
                "empresa_id": f"eq.{empresa_id}",
                "activo": "eq.true",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        if r.status_code == 404:
            return None
        r.raise_for_status()
        rows = r.json() if r.content else []
        return rows[0] if rows else None
    except Exception as e:
        print("GOOGLE CALENDAR CONNECTION ERROR:", repr(e))
        return None


def es_diego_calendar_legacy(empresa_id=None):
    """Permite usar el refresh token global SOLO a la empresa legacy de Diego."""
    empresa_id = str(empresa_id or empresa_actual_id() or "").strip()
    return bool(empresa_id and DIEGO_EMPRESA_ID and empresa_id == str(DIEGO_EMPRESA_ID).strip())


def google_credentials():
    # 1) Cada cliente/empresa usa primero su propia conexión OAuth.
    conn = google_calendar_conexion()
    if conn and conn.get("refresh_token"):
        if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET]):
            raise RuntimeError("Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en Render")
        return Credentials(
            token=conn.get("access_token") or None,
            refresh_token=conn.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=GOOGLE_SCOPES,
        )

    # 2) Fallback legacy EXCLUSIVO para Diego.
    # Ninguna otra empresa puede caer en GOOGLE_REFRESH_TOKEN aunque no tenga OAuth propio.
    if not es_diego_calendar_legacy():
        raise RuntimeError("Google Calendar no está conectado para esta empresa")

    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        raise RuntimeError("Google Calendar legacy de Diego no está configurado")

    return Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_SCOPES,
    )


def google_calendar_id_actual():
    conn = google_calendar_conexion()
    if conn and conn.get("calendar_id"):
        return str(conn.get("calendar_id"))

    # El calendar_id global/legacy también queda restringido a Diego.
    if es_diego_calendar_legacy():
        return str(cfg("calendar_id", DEFAULT_CALENDAR_ID) or DEFAULT_CALENDAR_ID)

    raise RuntimeError("Google Calendar no está conectado para esta empresa")


def calendar_service():
    return build("calendar", "v3", credentials=google_credentials(), cache_discovery=False)


def negocio_tiene_calendar_real():
    """True si el tenant tiene OAuth propio o si es Diego con su Calendar legacy."""
    if google_calendar_conexion():
        return True
    return bool(
        es_diego_calendar_legacy()
        and GOOGLE_CLIENT_ID
        and GOOGLE_CLIENT_SECRET
        and GOOGLE_REFRESH_TOKEN
    )


def es_dia_atencion(fecha):
    return fecha.astimezone(zona_local()).weekday() in set(cfg("dias_atencion", [0,1,2,3,4,5]))


def eventos_ocupados(inicio_rango, fin_rango):
    service = calendar_service()
    data = service.events().list(
        calendarId=google_calendar_id_actual(),
        timeMin=inicio_rango.isoformat(),
        timeMax=fin_rango.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=500,
    ).execute()

    ocupados = []
    zona = zona_local()
    for ev in data.get("items", []):
        ini = (ev.get("start") or {}).get("dateTime")
        fin = (ev.get("end") or {}).get("dateTime")
        if ini and fin:
            try:
                di = datetime.fromisoformat(ini.replace("Z", "+00:00")).astimezone(zona)
                df = datetime.fromisoformat(fin.replace("Z", "+00:00")).astimezone(zona)
                ocupados.append((di, df))
            except Exception:
                pass
        elif (ev.get("start") or {}).get("date"):
            try:
                d = datetime.fromisoformat(ev["start"]["date"]).date()
                di = zona.localize(datetime.combine(d, datetime.min.time()))
                df = di + timedelta(days=1)
                ocupados.append((di, df))
            except Exception:
                pass
    return ocupados


def hora_libre(inicio, ocupados):
    fin = inicio + timedelta(minutes=cfg_int("duracion_reserva", DEFAULT_DURACION_RESERVA))
    return all(not (inicio < ocupado_fin and fin > ocupado_ini) for ocupado_ini, ocupado_fin in ocupados)


def verificar_disponibilidad(inicio):
    inicio = inicio.astimezone(zona_local())
    if inicio <= ahora_local() or not es_dia_atencion(inicio):
        return False
    if inicio.minute != 0 or inicio.hour < cfg_int("hora_apertura", DEFAULT_HORA_APERTURA) or inicio.hour >= cfg_int("hora_cierre", DEFAULT_HORA_CIERRE):
        return False
    fin = inicio + timedelta(minutes=cfg_int("duracion_reserva", DEFAULT_DURACION_RESERVA))
    limite = inicio.replace(hour=cfg_int("hora_cierre", DEFAULT_HORA_CIERRE), minute=0, second=0, microsecond=0)
    if fin > limite:
        return False
    return hora_libre(inicio, eventos_ocupados(inicio, fin))


def buscar_proximas_horas(desde=None, limite=15):
    ahora = ahora_local()
    desde = (desde or ahora).astimezone(zona_local())
    if desde < ahora:
        desde = ahora

    inicio_rango = desde
    fin_rango = desde + timedelta(days=31)
    ocupados = eventos_ocupados(inicio_rango, fin_rango)
    resultados = []

    for offset in range(32):
        dia = (desde + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        if not es_dia_atencion(dia):
            continue
        for h in range(cfg_int("hora_apertura", DEFAULT_HORA_APERTURA), cfg_int("hora_cierre", DEFAULT_HORA_CIERRE)):
            slot = dia.replace(hour=h)
            if slot <= ahora or slot < desde:
                continue
            if hora_libre(slot, ocupados):
                resultados.append(slot)
                if len(resultados) >= limite:
                    return resultados
    return resultados


def buscar_horas_dia(fecha):
    zona = zona_local()
    fecha = fecha.astimezone(zona)
    inicio = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
    if not es_dia_atencion(inicio):
        return []
    fin = inicio + timedelta(days=1)
    ocupados = eventos_ocupados(inicio, fin)
    ahora = ahora_local()
    out = []
    for h in range(cfg_int("hora_apertura", DEFAULT_HORA_APERTURA), cfg_int("hora_cierre", DEFAULT_HORA_CIERRE)):
        slot = inicio.replace(hour=h)
        if slot > ahora and hora_libre(slot, ocupados):
            out.append(slot)
    return out


def crear_evento(inicio, servicio_codigo, nombre, telefono, correo):
    # Revalidación justo antes de reservar para evitar doble reserva.
    if not verificar_disponibilidad(inicio):
        return {"ok": False, "ocupada": True}

    servicio = servicios_actuales()[servicio_codigo]
    fin = inicio + timedelta(minutes=cfg_int("duracion_reserva", DEFAULT_DURACION_RESERVA))
    body = {
        "summary": f"{servicio['nombre']} - {nombre}",
        "description": (
            f"Reserva creada por el Asistente Virtual de {cfg('asistente_nombre', DEFAULT_ASISTENTE_NOMBRE)}.\n\n"
            f"Cliente: {nombre}\nTeléfono: {telefono}\nCorreo: {correo}\n"
            f"Servicio: {servicio['nombre']}\nValor referencial: {servicio['precio_texto']}\n"
            f"Duración: {cfg_int('duracion_reserva', DEFAULT_DURACION_RESERVA)} minutos\nOrigen: WhatsApp"
        ),
        "start": {"dateTime": inicio.isoformat(), "timeZone": timezone_actual()},
        "end": {"dateTime": fin.isoformat(), "timeZone": timezone_actual()},
        "attendees": [{"email": correo, "displayName": nombre}],
        "extendedProperties": {
            "private": {
                "telefono": telefono,
                "cliente": nombre,
                "correo": correo,
                "servicio_codigo": servicio_codigo,
                "origen": "whatsapp_nexia",
            }
        },
    }
    try:
        resultado = calendar_service().events().insert(
            calendarId=google_calendar_id_actual(),
            body=body,
            sendUpdates="all",
        ).execute()
        return {"ok": True, "evento_id": resultado.get("id")}
    except Exception as e:
        print("GOOGLE CALENDAR CREAR EVENTO ERROR:", repr(e))
        return {"ok": False, "error": repr(e)}




# ============================================================
# NEXIA V3.1 - SINCRONIZACION GOOGLE CALENDAR <-> PORTAL
# ============================================================

CALENDAR_SYNC_DIAS_ATRAS = int(os.getenv("CALENDAR_SYNC_DIAS_ATRAS", "90"))
CALENDAR_SYNC_DIAS_ADELANTE = int(os.getenv("CALENDAR_SYNC_DIAS_ADELANTE", "365"))
CALENDAR_SYNC_MAX_RESULTS = int(os.getenv("CALENDAR_SYNC_MAX_RESULTS", "2500"))
CALENDAR_SYNC_AUTO_PORTAL = str(os.getenv("CALENDAR_SYNC_AUTO_PORTAL", "true")).lower() in {"1","true","yes","si"}
CALENDAR_CRON_SECRET = str(os.getenv("CALENDAR_CRON_SECRET") or "").strip()


def _calendar_iso_to_local(value, tz_name=None):
    if not value:
        return None
    tz = pytz.timezone(tz_name or timezone_actual())
    value = str(value)
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return tz.localize(datetime.strptime(value, "%Y-%m-%d"))
        # Google suele devolver RFC3339.
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = tz.localize(dt)
        return dt.astimezone(tz)
    except Exception:
        return None


def _calendar_event_cliente(event):
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    cliente = str(private.get("cliente") or "").strip()
    if cliente:
        return cliente
    attendees = event.get("attendees") or []
    for attendee in attendees:
        if isinstance(attendee, dict):
            display = str(attendee.get("displayName") or "").strip()
            if display:
                return display
    summary = str(event.get("summary") or "").strip()
    if " - " in summary:
        return summary.rsplit(" - ", 1)[-1].strip()
    return None


def _calendar_event_servicio(event):
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    codigo = str(private.get("servicio_codigo") or "").strip()
    if codigo:
        servicio = servicios_actuales().get(codigo) or {}
        if servicio.get("nombre"):
            return str(servicio.get("nombre")).strip()
    summary = str(event.get("summary") or "").strip()
    if " - " in summary:
        return summary.rsplit(" - ", 1)[0].strip()
    return summary or "Evento Calendar"


def _calendar_event_telefono(event):
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    tel = str(private.get("telefono") or "").strip()
    if tel:
        return re.sub(r"\D", "", normalizar_telefono(tel))
    desc = str(event.get("description") or "")
    m = re.search(r"(?:Tel[eé]fono|WhatsApp)\s*:\s*([+\d\s()-]{7,})", desc, flags=re.I)
    if m:
        return re.sub(r"\D", "", m.group(1))
    return None


def _calendar_event_correo(event):
    private = ((event.get("extendedProperties") or {}).get("private") or {})
    correo = str(private.get("correo") or "").strip()
    if correo:
        return correo
    attendees = event.get("attendees") or []
    for attendee in attendees:
        if isinstance(attendee, dict):
            email = str(attendee.get("email") or "").strip()
            if email:
                return email
    return None


def _calendar_event_payload(event, empresa_id):
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return None

    status_google = str(event.get("status") or "confirmed").strip().lower()
    start_data = event.get("start") or {}
    end_data = event.get("end") or {}
    start_raw = start_data.get("dateTime") or start_data.get("date")
    end_raw = end_data.get("dateTime") or end_data.get("date")

    inicio = _calendar_iso_to_local(start_raw, start_data.get("timeZone") or timezone_actual())
    fin = _calendar_iso_to_local(end_raw, end_data.get("timeZone") or timezone_actual())

    # Los eventos cancelados de Google a veces vuelven sin start/end completos.
    fecha = inicio.date().isoformat() if inicio else None
    hora = inicio.strftime("%H:%M:%S") if inicio else None

    private = ((event.get("extendedProperties") or {}).get("private") or {})
    origen = str(private.get("origen") or "").strip() or "google_calendar"

    estado = "cancelada" if status_google == "cancelled" else "confirmada"
    if str(event.get("transparency") or "").lower() == "transparent" and estado != "cancelada":
        estado = "confirmada"

    return {
        "empresa_id": str(empresa_id),
        "telefono": _calendar_event_telefono(event),
        "nombre_cliente": _calendar_event_cliente(event),
        "servicio": _calendar_event_servicio(event),
        "fecha": fecha,
        "hora": hora,
        "estado": estado,
        "google_event_id": event_id,
        "google_status": status_google,
        "google_html_link": event.get("htmlLink"),
        "calendar_updated_at": event.get("updated"),
        "inicio_at": inicio.isoformat() if inicio else None,
        "fin_at": fin.isoformat() if fin else None,
        "correo": _calendar_event_correo(event),
        "origen": origen,
        "updated_at": datetime.now(pytz.UTC).isoformat(),
    }


def _reserva_por_google_event_id(empresa_id, google_event_id):
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/reservas",
            headers=supabase_headers(),
            params={
                "select":"*",
                "empresa_id":f"eq.{empresa_id}",
                "google_event_id":f"eq.{google_event_id}",
                "limit":"1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        return rows[0] if rows else None
    except Exception:
        return None


def _upsert_reserva_calendar(payload):
    if not payload or not payload.get("empresa_id") or not payload.get("google_event_id"):
        return False

    existing = _reserva_por_google_event_id(payload["empresa_id"], payload["google_event_id"])
    try:
        if existing:
            # Para cancelados sin fecha/hora, preserva los datos previos del Portal.
            patch = dict(payload)
            for k in ("fecha","hora","inicio_at","fin_at","telefono","nombre_cliente","correo","servicio"):
                if patch.get(k) in (None, "") and existing.get(k) not in (None, ""):
                    patch[k] = existing.get(k)
            r = requests.patch(
                f"{SUPABASE_URL}/rest/v1/reservas",
                headers={**supabase_headers(), "Prefer":"return=minimal"},
                params={"id":f"eq.{existing.get('id')}"},
                json=patch,
                timeout=SUPABASE_TIMEOUT,
            )
        else:
            # Si es un cancelado completamente vacío que nunca conocimos, no ensucia agenda.
            if payload.get("estado") == "cancelada" and not payload.get("fecha") and not payload.get("servicio"):
                return True
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/reservas",
                headers={**supabase_headers(), "Prefer":"resolution=merge-duplicates,return=minimal"},
                params={"on_conflict":"empresa_id,google_event_id"},
                json=payload,
                timeout=SUPABASE_TIMEOUT,
            )
        r.raise_for_status()
        return True
    except Exception as e:
        detalle = ""
        try:
            detalle = f" | {r.status_code} {r.text[:600]}"
        except Exception:
            pass
        print("CALENDAR SYNC UPSERT ERROR:", repr(e), detalle)
        return False


def sincronizar_google_calendar_empresa(empresa_id, dias_atras=None, dias_adelante=None):
    """
    Calendar -> Supabase/Portal.
    Sincroniza eventos nuevos, cambios de hora y cancelaciones.
    """
    empresa_id = str(empresa_id or "").strip()
    if not empresa_id:
        return {"ok":False,"error":"empresa_id requerido"}

    activar_por_empresa(empresa_id, canal="portal")

    if not google_calendar_conexion(empresa_id) and not es_diego_calendar_legacy(empresa_id):
        return {"ok":True,"conectado":False,"procesados":0,"actualizados":0}

    ahora_utc = datetime.now(pytz.UTC)
    atras = int(dias_atras if dias_atras is not None else CALENDAR_SYNC_DIAS_ATRAS)
    adelante = int(dias_adelante if dias_adelante is not None else CALENDAR_SYNC_DIAS_ADELANTE)

    time_min = (ahora_utc - timedelta(days=max(atras,0))).isoformat().replace("+00:00","Z")
    time_max = (ahora_utc + timedelta(days=max(adelante,1))).isoformat().replace("+00:00","Z")

    service = calendar_service()
    calendar_id = google_calendar_id_actual()

    page_token = None
    procesados = 0
    actualizados = 0
    errores = 0

    while True:
        try:
            req = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                showDeleted=True,
                maxResults=min(max(CALENDAR_SYNC_MAX_RESULTS, 100), 2500),
                pageToken=page_token,
            )
            data = req.execute()
        except Exception as e:
            print("CALENDAR SYNC LIST ERROR:", empresa_id, repr(e))
            return {
                "ok":False,
                "conectado":True,
                "procesados":procesados,
                "actualizados":actualizados,
                "errores":errores + 1,
                "error":"No se pudo leer Google Calendar",
            }

        for event in data.get("items") or []:
            procesados += 1
            payload = _calendar_event_payload(event, empresa_id)
            if not payload:
                continue
            if _upsert_reserva_calendar(payload):
                actualizados += 1
            else:
                errores += 1

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    # Estado de última sincronización en conexión.
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/google_calendar_conexiones",
            headers={**backend_headers(),"Prefer":"return=minimal"},
            params={"empresa_id":f"eq.{empresa_id}"},
            json={
                "ultima_sincronizacion_at":datetime.now(pytz.UTC).isoformat(),
                "ultima_sincronizacion_estado":"ok" if not errores else "parcial",
            },
            timeout=SUPABASE_TIMEOUT,
        )
    except Exception:
        pass

    return {
        "ok":True,
        "conectado":True,
        "procesados":procesados,
        "actualizados":actualizados,
        "errores":errores,
    }


def _portal_reserva_actual(empresa_id, reserva_id):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/reservas",
        headers=supabase_headers(),
        params={
            "select":"*",
            "id":f"eq.{reserva_id}",
            "empresa_id":f"eq.{empresa_id}",
            "limit":"1",
        },
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json() if r.content else []
    return rows[0] if rows else None


@app.route("/portal/agenda/sincronizar", methods=["POST","OPTIONS"])
def portal_agenda_sincronizar():
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)

    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    if not empresa_id:
        return portal_json({"ok":False,"error":"Tu usuario no tiene empresa asociada"},409)

    resultado = sincronizar_google_calendar_empresa(empresa_id)
    return portal_json(resultado, 200 if resultado.get("ok") else 502)


@app.route("/portal/agenda/reserva/<reserva_id>", methods=["PATCH","DELETE","OPTIONS"])
def portal_agenda_editar_reserva(reserva_id):
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)

    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    if not empresa_id:
        return portal_json({"ok":False,"error":"Tu usuario no tiene empresa asociada"},409)

    activar_por_empresa(empresa_id, canal="portal")

    try:
        reserva = _portal_reserva_actual(empresa_id, reserva_id)
    except Exception as e:
        return portal_json({"ok":False,"error":"No se pudo leer la reserva"},500)

    if not reserva:
        return portal_json({"ok":False,"error":"Reserva no encontrada"},404)

    event_id = str(reserva.get("google_event_id") or "").strip()
    if not event_id:
        return portal_json({"ok":False,"error":"La reserva no está vinculada a Google Calendar"},409)

    if request.method == "DELETE":
        try:
            calendar_service().events().delete(
                calendarId=google_calendar_id_actual(),
                eventId=event_id,
                sendUpdates="all",
            ).execute()
        except Exception as e:
            # Si Google ya lo considera borrado/cancelado, igualmente reflejamos estado local.
            print("PORTAL CALENDAR CANCEL ERROR:", repr(e))

        try:
            requests.patch(
                f"{SUPABASE_URL}/rest/v1/reservas",
                headers={**supabase_headers(),"Prefer":"return=minimal"},
                params={"id":f"eq.{reserva_id}"},
                json={
                    "estado":"cancelada",
                    "google_status":"cancelled",
                    "updated_at":datetime.now(pytz.UTC).isoformat(),
                },
                timeout=SUPABASE_TIMEOUT,
            ).raise_for_status()
        except Exception as e:
            return portal_json({"ok":False,"error":"Calendar se actualizó, pero no pude actualizar el Portal"},500)

        return portal_json({"ok":True,"estado":"cancelada"})

    data = request.get_json(silent=True) or {}
    fecha = str(data.get("fecha") or reserva.get("fecha") or "").strip()
    hora = str(data.get("hora") or reserva.get("hora") or "").strip()
    servicio = str(data.get("servicio") or reserva.get("servicio") or "Reserva").strip()
    nombre = str(data.get("nombre_cliente") or reserva.get("nombre_cliente") or "").strip()

    if not fecha or not hora:
        return portal_json({"ok":False,"error":"Fecha y hora son obligatorias"},400)

    try:
        hora = hora[:5]
        naive = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
        inicio = zona_local().localize(naive)
        fin = inicio + timedelta(minutes=cfg_int("duracion_reserva", DEFAULT_DURACION_RESERVA))

        event = calendar_service().events().get(
            calendarId=google_calendar_id_actual(),
            eventId=event_id,
        ).execute()

        event["summary"] = f"{servicio} - {nombre}" if nombre else servicio
        event["start"] = {"dateTime":inicio.isoformat(),"timeZone":timezone_actual()}
        event["end"] = {"dateTime":fin.isoformat(),"timeZone":timezone_actual()}

        resultado = calendar_service().events().update(
            calendarId=google_calendar_id_actual(),
            eventId=event_id,
            body=event,
            sendUpdates="all",
        ).execute()

        payload = _calendar_event_payload(resultado, empresa_id)
        _upsert_reserva_calendar(payload)

        return portal_json({
            "ok":True,
            "reserva":{
                "id":reserva_id,
                "fecha":payload.get("fecha"),
                "hora":payload.get("hora"),
                "estado":payload.get("estado"),
            }
        })
    except Exception as e:
        print("PORTAL CALENDAR UPDATE ERROR:", repr(e))
        return portal_json({"ok":False,"error":"No pude actualizar la cita en Google Calendar"},502)


@app.route("/internal/calendar-sync", methods=["POST"])
def internal_calendar_sync():
    """
    Endpoint para Render Cron/otro scheduler.
    Header requerido: X-Nexia-Cron-Secret.
    """
    if not CALENDAR_CRON_SECRET:
        return portal_json({"ok":False,"error":"CALENDAR_CRON_SECRET no configurado"},503)

    supplied = str(request.headers.get("X-Nexia-Cron-Secret") or "")
    if not hmac.compare_digest(supplied, CALENDAR_CRON_SECRET):
        return portal_json({"ok":False,"error":"No autorizado"},401)

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/google_calendar_conexiones",
            headers=backend_headers(),
            params={"select":"empresa_id","activo":"eq.true","limit":"500"},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
    except Exception as e:
        return portal_json({"ok":False,"error":"No se pudieron listar conexiones"},500)

    resultados = []
    for row in rows:
        empresa_id = str(row.get("empresa_id") or "").strip()
        if not empresa_id:
            continue
        try:
            resultados.append({"empresa_id":empresa_id, **sincronizar_google_calendar_empresa(empresa_id)})
        except Exception as e:
            resultados.append({"empresa_id":empresa_id,"ok":False,"error":str(e)[:180]})

    return portal_json({
        "ok":True,
        "empresas":len(resultados),
        "resultados":resultados,
    })


# ============================================================
# FECHAS Y HORAS NATURALES
# ============================================================

DIAS_MAP = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}
MESES_MAP = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def detectar_hora(texto):
    t = normalizar_texto(texto)
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b(1[0-2]|[1-9])(?::([0-5]\d))?\s*(am|pm)\b", t)
    if m:
        h, mi, periodo = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if periodo == "pm" and h < 12:
            h += 12
        if periodo == "am" and h == 12:
            h = 0
        return h, mi
    m = re.search(r"\ba\s+las?\s+(\d{1,2})\b", t)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 6:
            h += 12
        return h, 0
    return None


def detectar_fecha(texto, hora_data=None):
    t = normalizar_texto(texto)
    ahora = ahora_local()
    base = ahora.replace(hour=0, minute=0, second=0, microsecond=0)

    m = re.search(
        r"\b(?:el\s+)?([0-3]?\d)\s*(?:de\s+)?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b",
        t,
    )
    if m:
        dia, mes = int(m.group(1)), MESES_MAP[m.group(2)]
        anio = ahora.year
        try:
            cand = zona_local().localize(datetime(anio, mes, dia))
            if cand.date() < ahora.date():
                cand = zona_local().localize(datetime(anio + 1, mes, dia))
            return cand
        except ValueError:
            return None

    if "pasado manana" in t:
        return base + timedelta(days=2)
    if "manana" in t:
        return base + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return base

    for nombre, weekday in DIAS_MAP.items():
        if re.search(rf"\b{nombre}\b", t):
            diferencia = (weekday - ahora.weekday()) % 7
            if diferencia == 0:
                # "lunes" hoy: si la hora pedida ya pasó, ir al siguiente lunes.
                if hora_data:
                    h, mi = hora_data
                    if ahora.replace(hour=h, minute=mi, second=0, microsecond=0) <= ahora:
                        diferencia = 7
                elif "proximo" in t:
                    diferencia = 7
            return base + timedelta(days=diferencia)
    return None


def fecha_hora_desde_texto(texto):
    h = detectar_hora(texto)
    f = detectar_fecha(texto, h)
    if f and h:
        return f.replace(hour=h[0], minute=h[1], second=0, microsecond=0)
    return None


def texto_menciona_fecha(texto):
    t = normalizar_texto(texto)
    claves = ["hoy", "manana", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    claves += list(MESES_MAP.keys())
    return any(re.search(rf"\b{re.escape(x)}\b", t) for x in claves)


def formatear_fecha(fecha):
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    f = fecha.astimezone(zona_local())
    return f"{dias[f.weekday()]} {f.day} de {meses[f.month-1]} a las {f.strftime('%H:%M')}"


def listar_horas(horas):
    if not horas:
        return "No encontré horas disponibles para esa fecha."
    return "\n".join(f"{i}. {formatear_fecha(h)}" for i, h in enumerate(horas, 1))


# ============================================================
# SUPABASE - HISTORIAL PARA PORTAL NEXIA
# ============================================================

def supabase_headers():
    if not SUPABASE_SERVICE_ROLE_KEY:
        return None
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }



def obtener_modo_atencion(identificador, canal="whatsapp"):
    """
    Devuelve 'bot' o 'ejecutivo' para una conversación.
    Si la conversación aún no existe o Supabase falla, usa 'bot'.
    """
    headers = supabase_headers()
    if not headers or not empresa_actual_id():
        return "bot"

    canal = (canal or "whatsapp").strip().lower()
    identificador = str(identificador or "").strip()
    if canal == "whatsapp":
        identificador = re.sub(r"\D", "", normalizar_telefono(identificador))
    else:
        identificador = re.sub(r"\D", "", identificador)

    if not identificador:
        return "bot"

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/conversaciones",
            headers=headers,
            params={
                "select": "id,modo_atencion,ultima_fecha",
                "empresa_id": f"eq.{empresa_actual_id()}",
                "telefono": f"eq.{identificador}",
                "canal": f"eq.{canal}",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        filas = r.json() if r.content else []
        if not filas:
            return "bot"

        fila = filas[0]
        modo = str(fila.get("modo_atencion") or "bot").lower()

        # El modo ejecutivo nunca queda bloqueado para siempre.
        # Si pasó el tiempo configurado sin actividad, la conversación vuelve al bot.
        if modo == "ejecutivo" and MODO_EJECUTIVO_TIMEOUT_MINUTOS > 0:
            ultima = str(fila.get("ultima_fecha") or "").strip()
            if ultima:
                try:
                    ultima_dt = datetime.fromisoformat(ultima.replace("Z", "+00:00"))
                    if ultima_dt.tzinfo is None:
                        ultima_dt = pytz.timezone(TIMEZONE).localize(ultima_dt)
                    ahora_dt = datetime.now(pytz.UTC)
                    minutos = (ahora_dt - ultima_dt.astimezone(pytz.UTC)).total_seconds() / 60
                    if minutos >= MODO_EJECUTIVO_TIMEOUT_MINUTOS:
                        establecer_modo_atencion(fila.get("id"), "bot")
                        print(
                            "MODO EJECUTIVO EXPIRADO:",
                            fila.get("id"),
                            f"{minutos:.1f} min -> bot",
                        )
                        return "bot"
                except Exception as e:
                    print("MODO EJECUTIVO TIMEOUT PARSE ERROR:", repr(e))

        return modo
    except Exception as e:
        print("SUPABASE MODO ATENCION ERROR:", repr(e))
        return "bot"


def establecer_modo_atencion(conversacion_id, modo):
    """
    Cambia el control de la conversación.
    modo: 'bot' o 'ejecutivo'
    """
    modo = str(modo or "").strip().lower()
    if modo not in {"bot", "ejecutivo"}:
        raise ValueError("Modo de atención inválido")

    headers = supabase_headers()
    if not headers:
        raise RuntimeError("Supabase no está configurado")

    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/conversaciones",
        headers={**headers, "Prefer": "return=minimal"},
        params={
            "id": f"eq.{conversacion_id}",
            "empresa_id": f"eq.{empresa_actual_id()}",
        },
        json={"modo_atencion": modo},
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    print("SUPABASE MODO ATENCION OK:", conversacion_id, modo)
    return True


def guardar_mensaje_supabase(
    telefono,
    direccion,
    mensaje,
    nombre_contacto=None,
    canal="whatsapp",
):
    """
    Guarda/actualiza la conversación y agrega el mensaje al Portal Nexia.

    direccion: 'entrante' o 'saliente'
    canal: 'whatsapp' o 'instagram'
    """
    headers = supabase_headers()
    if not headers or not empresa_actual_id():
        return

    canal = (canal or "whatsapp").strip().lower()
    identificador = str(telefono or "").strip()

    if canal == "whatsapp":
        identificador = re.sub(r"\D", "", normalizar_telefono(identificador))
    else:
        identificador = re.sub(r"\D", "", identificador)

    mensaje = (mensaje or "").strip()
    if not identificador or not mensaje:
        return

    try:
        params = {
            "select": "id,nombre_contacto,canal",
            "empresa_id": f"eq.{empresa_actual_id()}",
            "telefono": f"eq.{identificador}",
            "canal": f"eq.{canal}",
            "limit": "1",
        }

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/conversaciones",
            headers=headers,
            params=params,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        filas = r.json() if r.content else []

        ahora_iso = ahora_local().isoformat()
        conversacion_id = None

        if filas:
            conversacion_id = filas[0]["id"]
            cambios = {
                "ultimo_mensaje": mensaje,
                "ultima_fecha": ahora_iso,
                "canal": canal,
            }
            if nombre_contacto and not filas[0].get("nombre_contacto"):
                cambios["nombre_contacto"] = nombre_contacto

            r = requests.patch(
                f"{SUPABASE_URL}/rest/v1/conversaciones",
                headers={**headers, "Prefer": "return=minimal"},
                params={"id": f"eq.{conversacion_id}"},
                json=cambios,
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()

        else:
            nueva = {
                "empresa_id": empresa_actual_id(),
                "telefono": identificador,
                "nombre_contacto": nombre_contacto,
                "ultimo_mensaje": mensaje,
                "ultima_fecha": ahora_iso,
                "canal": canal,
            }

            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/conversaciones",
                headers={**headers, "Prefer": "return=representation"},
                json=nueva,
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()

            creadas = r.json() if r.content else []
            if not creadas:
                raise RuntimeError("Supabase no devolvió la conversación creada")
            conversacion_id = creadas[0]["id"]

        nuevo_mensaje = {
            "conversacion_id": conversacion_id,
            "empresa_id": empresa_actual_id(),
            "direccion": direccion,
            "mensaje": mensaje,
            "fecha": ahora_iso,
            "canal": canal,
        }

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/mensajes",
            headers={**headers, "Prefer": "return=minimal"},
            json=nuevo_mensaje,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()

        print(
            "SUPABASE LOG OK:",
            canal,
            identificador,
            direccion,
            conversacion_id,
        )

    except Exception as e:
        detalle = ""
        try:
            detalle = f" | {r.status_code} {r.text[:1000]}"
        except Exception:
            pass
        print("SUPABASE LOG ERROR:", repr(e), detalle)


def guardar_reserva_supabase(telefono, nombre_cliente, servicio_nombre, inicio, google_event_id=None):
    """
    Guarda una reserva confirmada en public.reservas para el Portal Nexia.

    La reserva de Google Calendar sigue siendo la fuente de confirmación del bot.
    Si Supabase falla, la cita ya creada en Calendar NO se elimina y el bot continúa.
    """
    headers = supabase_headers()
    if not headers or not empresa_actual_id():
        return False

    telefono_limpio = re.sub(r"\D", "", normalizar_telefono(telefono))
    nombre_cliente = (nombre_cliente or "").strip() or None
    servicio_nombre = (servicio_nombre or "").strip()

    if not telefono_limpio or not servicio_nombre or not inicio:
        return False

    try:
        # Normalizar la fecha/hora a la zona horaria del negocio.
        if inicio.tzinfo is None:
            inicio_local = zona_local().localize(inicio)
        else:
            inicio_local = inicio.astimezone(zona_local())

        nueva_reserva = {
            "empresa_id": empresa_actual_id(),
            "telefono": telefono_limpio,
            "nombre_cliente": nombre_cliente,
            "servicio": servicio_nombre,
            "fecha": inicio_local.date().isoformat(),
            "hora": inicio_local.strftime("%H:%M:%S"),
            "estado": "confirmada",
            "google_event_id": google_event_id,
            "updated_at": ahora_local().isoformat(),
        }

        # Si Google ya devolvió event_id, el guardado es idempotente.
        if google_event_id:
            nueva_reserva["google_status"] = "confirmed"
            nueva_reserva["origen"] = "whatsapp_nexia"
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/reservas",
                headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
                params={"on_conflict":"empresa_id,google_event_id"},
                json=nueva_reserva,
                timeout=SUPABASE_TIMEOUT,
            )
        else:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/reservas",
                headers={**headers, "Prefer": "return=representation"},
                json=nueva_reserva,
                timeout=SUPABASE_TIMEOUT,
            )
        r.raise_for_status()

        filas = r.json() if r.content else []
        reserva_id = filas[0].get("id") if filas else None
        print(
            "SUPABASE RESERVA OK:",
            telefono_limpio,
            nueva_reserva["fecha"],
            nueva_reserva["hora"],
            reserva_id,
        )
        return True

    except Exception as e:
        detalle = ""
        try:
            detalle = f" | {r.status_code} {r.text[:1000]}"
        except Exception:
            pass
        print("SUPABASE RESERVA ERROR:", repr(e), detalle)
        return False


# ============================================================
# GOOGLE SHEETS DESACTIVADO - SUPABASE ES LA FUENTE PRINCIPAL
# ============================================================

def guardar_mensaje(telefono, rol, mensaje, canal="whatsapp"):
    # Compatibilidad con llamadas existentes. No realiza I/O.
    # Todo el historial operativo se guarda en guardar_mensaje_supabase().
    return None


# ============================================================
# OPENAI OPCIONAL: RESPUESTA LIBRE, PERO SOLO DENTRO DEL NEGOCIO
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
# Nexia Core usa un modelo más nuevo y rápido para respuestas conversacionales.
# Se mantiene separado del modelo legacy para no alterar Diego ni otros flujos.
OPENAI_CORE_MODEL = os.getenv("OPENAI_CORE_MODEL", "gpt-5.4-mini").strip()
OPENAI_CORE_REASONING_EFFORT = os.getenv("OPENAI_CORE_REASONING_EFFORT", "none").strip().lower()
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "8"))
openai_client = (
    OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )
    if OPENAI_API_KEY
    else None
)


def respuesta_general(texto):
    empresa = str(cfg("empresa_nombre", DEFAULT_NEGOCIO_NOMBRE) or "").strip()
    asistente = str(cfg("asistente_nombre", DEFAULT_ASISTENTE_NOMBRE) or "").strip()
    usa_reservas = negocio_usa_reservas()

    if usa_reservas:
        base = (
            f"Soy el asistente virtual de {asistente} 😊. "
            "Puedo ayudarte con servicios, precios, horarios de atención, disponibilidad y reservas."
        )
    else:
        base = (
            f"Soy el asistente virtual de {empresa} 😊. "
            "Puedo ayudarte con sus servicios, precios y con información para contratar."
        )

    if not openai_client:
        if usa_reservas:
            return base + "\n\nPuedes preguntarme por un servicio, por nuestros horarios o escribir *AGENDAR*."
        return base + "\n\nPuedes preguntarme por los servicios o escribir *ME INTERESA* para avanzar."

    contexto_servicios = "; ".join(
        f"{s['nombre']}: {s['precio_texto']}" for s in servicios_actuales().values()
    )

    if usa_reservas:
        regla_flujo = """
Este negocio USA agenda y reservas.
Puedes orientar al cliente para elegir un servicio y reservar una hora.
Si pregunta por los horarios de atención, informa el horario configurado.
"""
        ambito = "servicios, precios, horarios de atención, disponibilidad y reservas"
    else:
        regla_flujo = """
Este negocio NO usa reservas de horas.
NUNCA ofrezcas reservar, agendar una cita, elegir fecha u hora.
Si el cliente manifiesta interés en contratar, pregúntale primero qué necesita que haga el servicio o bot.
Luego solicita su nombre y empresa/emprendimiento para continuar la atención comercial.
No inventes que ya se creó una reserva ni una reunión.
"""
        ambito = "servicios, precios, contratación y atención comercial"

    if es_empresa_nexia():
        contexto_contacto = """
REGLA ESTRICTA PARA NEXIA:
No entregues, menciones ni inventes dirección física, número de teléfono ni horarios de atención.
Si preguntan por dirección, teléfono o horario, indica que la atención se gestiona directamente por este chat y ofrece ayudar o derivar a un ejecutivo.
La derivación al ejecutivo ocurre internamente; nunca muestres datos personales del ejecutivo.
"""
    else:
        contexto_contacto = f"""
Dirección configurada: {cfg('direccion', DEFAULT_DIRECCION_ATENCION)}.
Teléfono de ejecutivo: {cfg('telefono_ejecutivo', DEFAULT_TELEFONO_EJECUTIVO)}.
Horario configurado: {mensaje_horarios(solo_texto=True)}.
"""

    system = f"""
Eres el asistente virtual de {empresa}.
Tipo de negocio: {cfg('tipo_negocio','')}.
Descripción de la empresa: {cfg('descripcion_empresa','') or 'Sin descripción adicional configurada.'}

Tu función es ayudar a clientes basándote únicamente en la información real configurada para esta empresa.
{regla_flujo}
{contexto_contacto}
Servicios: {contexto_servicios}.

No inventes información.
No hables de sistemas internos, APIs ni código.
No copies flujos de otras empresas.
Si el usuario escribe algo fuera de este ámbito, responde amablemente que eres el asistente virtual de {empresa} y que puedes ayudar con {ambito}.
Mantén la respuesta breve, natural y en español de Chile.
"""
    try:
        r = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": texto}],
        )
        respuesta_ia = (r.choices[0].message.content or "").strip() or base
        return proteger_respuesta_publica_nexia(respuesta_ia)
    except Exception as e:
        print("OPENAI FALLBACK ERROR:", repr(e))
        if usa_reservas:
            return base + "\n\nPuedes preguntarme por un servicio, por nuestros horarios o escribir *AGENDAR*."
        return base + "\n\nPuedes preguntarme por los servicios o escribir *ME INTERESA* para avanzar."


# ============================================================
# ESTADO WHATSAPP
# ============================================================

SESIONES = {}
SESIONES_LOCK = Lock()
PROCESADOS = {}
PROCESADOS_LOCK = Lock()


def estado_inicial(telefono):
    return {
        "telefono": telefono,
        "paso": "inicio",
        "servicio": None,
        "fecha_hora": None,
        "horas_ofrecidas": [],
        "nombre": None,
        "correo": None,
        "objetivo_comercial": None,
        "empresa_cliente": None,
    }


def get_estado(telefono):
    with SESIONES_LOCK:
        if telefono not in SESIONES:
            SESIONES[telefono] = estado_inicial(telefono)
        return SESIONES[telefono]


def reset_estado(telefono):
    with SESIONES_LOCK:
        SESIONES[telefono] = estado_inicial(telefono)
        return SESIONES[telefono]


def mensaje_bienvenida():
    empresa = str(cfg("empresa_nombre", DEFAULT_NEGOCIO_NOMBRE) or "").strip()
    asistente = str(cfg("asistente_nombre", "") or "").strip()
    tipo_negocio = str(cfg("tipo_negocio", "reservas") or "").strip().lower()

    if asistente and asistente.lower() != empresa.lower():
        presentacion = f"¡Hola! 👋 Soy {asistente}, el asistente virtual de {empresa}."
    else:
        presentacion = f"¡Hola! 👋 Soy el asistente virtual de {empresa}."

    if tipo_negocio in {"reservas", "agenda", "agendamiento", "servicios", "peluqueria", "barberia", "salon"}:
        ayuda = (
            "Estoy aquí para ayudarte con información del negocio, servicios, "
            "precios, horarios disponibles y reservas."
        )
        ejemplos = (
            "\n\nPuedes escribirme de forma natural, por ejemplo:\n"
            "• ¿Qué servicios tienen?\n"
            "• ¿Cuáles son sus horarios?\n"
            "• Quiero hacer una reserva"
        )
    elif tipo_negocio in {"ventas", "ecommerce", "tienda", "comercio", "retail", "tecnologia", "tecnológico", "tecnologico", "software", "servicios tecnologicos", "servicios tecnológicos", "automatizacion", "automatización"}:
        ayuda = (
            "Estoy aquí para ayudarte con información del negocio, servicios, "
            "precios y contratación."
        )
        ejemplos = (
            "\n\nPuedes escribirme de forma natural, por ejemplo:\n"
            "• ¿Qué servicios ofrecen?\n"
            "• ¿Cuáles son sus precios?\n"
            "• Me interesa contratar"
        )
    else:
        ayuda = (
            "Estoy aquí para ayudarte con información sobre la empresa, "
            "sus servicios y canales de atención."
        )
        ejemplos = (
            "\n\nPuedes escribirme de forma natural, por ejemplo:\n"
            "• ¿Qué servicios ofrecen?\n"
            "• ¿Cuáles son sus horarios?\n"
            "• Necesito más información"
        )

    return f"{presentacion}\n\n{ayuda}{ejemplos}"



def pregunta_contacto_sensible(texto):
    t = normalizar_texto(texto)
    frases = (
        "direccion", "direccion de nexia", "donde estan", "donde quedan",
        "ubicacion", "ubicacion de nexia", "telefono", "numero de telefono",
        "numero de contacto", "dame tu numero", "fono", "whatsapp de contacto"
    )
    return any(x in t for x in frases)


def mensaje_contacto_no_publicado():
    return (
        "La atención de Nexia se gestiona directamente por este chat 😊. "
        "Cuéntame qué necesitas y, si corresponde, te derivo a un ejecutivo sin compartir datos personales."
    )


def pregunta_horarios(texto):
    t = normalizar_texto(texto)
    frases = (
        "horario", "horarios", "horario de atencion", "horarios de atencion",
        "a que hora atienden", "a que hora abren", "a que hora cierran",
        "cuando atienden", "que dias atienden", "dias de atencion",
        "estan abiertos", "atienden hoy"
    )
    return any(x in t for x in frases)


def _dias_atencion_texto():
    dias = cfg("dias_atencion", [0,1,2,3,4,5]) or []
    try:
        dias = sorted({int(d) for d in dias})
    except Exception:
        dias = [0,1,2,3,4,5]

    nombres = {
        0: "lunes", 1: "martes", 2: "miércoles", 3: "jueves",
        4: "viernes", 5: "sábado", 6: "domingo"
    }

    # Rangos comunes para una respuesta más natural.
    if dias == [0,1,2,3,4]:
        return "lunes a viernes"
    if dias == [0,1,2,3,4,5]:
        return "lunes a sábado"
    if dias == [0,1,2,3,4,5,6]:
        return "lunes a domingo"

    return ", ".join(nombres.get(d, str(d)) for d in dias)


def _hora_legible(valor):
    texto = str(valor if valor is not None else "").strip()
    if not texto:
        return ""
    if re.fullmatch(r"\d{1,2}", texto):
        return f"{int(texto):02d}:00"
    if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", texto):
        return texto[:5]
    try:
        return f"{int(float(texto)):02d}:00"
    except Exception:
        return texto


def mensaje_horarios(solo_texto=False):
    dias = _dias_atencion_texto()
    apertura = _hora_legible(cfg("hora_apertura", DEFAULT_HORA_APERTURA))
    cierre = _hora_legible(cfg("hora_cierre", DEFAULT_HORA_CIERRE))
    texto = f"{dias}, de {apertura} a {cierre}"
    if solo_texto:
        return texto
    return f"🕒 Nuestro horario de atención es de *{texto}*."


def intencion_interes_comercial(texto):
    t = normalizar_texto(texto)
    frases = (
        "me interesa", "me interea", "estoy interesado", "estoy interesada",
        "quiero contratar", "quiero comprar", "quiero el servicio",
        "como contrato", "quiero contratarlo", "quiero hacerlo",
        "quiero un bot", "necesito un bot"
    )
    return any(x in t for x in frases)


def _parsear_nombre_empresa_objetivo(texto):
    partes = [p.strip() for p in re.split(r"[,;|]+", str(texto or "")) if p.strip()]
    if len(partes) >= 3:
        return partes[0], partes[1], ", ".join(partes[2:])
    return None, None, None


def procesar_comercial(estado, texto):
    """
    Flujo comercial genérico para empresas que NO trabajan con reservas.

    Flujo:
      interés -> necesidad/objetivo -> nombre + empresa -> resumen.
    También acepta una respuesta compacta:
      "Cristian Cadiz, La Ortiga, responder consultas"
    """
    t = str(texto or "").strip()
    paso = estado.get("paso") or "inicio"

    nombre, empresa_cliente, objetivo = _parsear_nombre_empresa_objetivo(t)
    if nombre and empresa_cliente and objetivo:
        estado["nombre"] = nombre
        estado["empresa_cliente"] = empresa_cliente
        estado["objetivo_comercial"] = objetivo
        estado["paso"] = "comercial_completo"
        return (
            f"¡Perfecto, {nombre}! 🙌\n\n"
            f"Ya tengo la información:\n"
            f"• Empresa/emprendimiento: *{empresa_cliente}*\n"
            f"• Necesidad: *{objetivo}*\n\n"
            f"Con esta información ya puedo derivarte a nuestro equipo. "
            f"Un ejecutivo continuará contigo por este mismo chat."
        )

    if paso in {"inicio", "comercial_inicio"}:
        estado["paso"] = "comercial_objetivo"
        return (
            "¡Genial! 🙌 Para orientarte mejor, cuéntame primero:\n\n"
            "*¿Qué te gustaría que hiciera el bot o servicio en tu negocio?*\n\n"
            "Por ejemplo: responder consultas, entregar información, gestionar pedidos, "
            "derivar clientes o automatizar procesos."
        )

    if paso == "comercial_objetivo":
        estado["objetivo_comercial"] = t
        estado["paso"] = "comercial_datos"
        return (
            "Perfecto 👍 Ahora indícame tu *nombre* y el *nombre de tu empresa o emprendimiento*.\n\n"
            "Por ejemplo: `Fabian Lopez, Centro Dental`"
        )

    if paso == "comercial_datos":
        partes = [p.strip() for p in re.split(r"[,;|]+", t) if p.strip()]
        if len(partes) >= 2:
            estado["nombre"] = partes[0]
            estado["empresa_cliente"] = partes[1]
            estado["paso"] = "comercial_completo"
            return (
                f"¡Gracias, {estado['nombre']}! 🙌\n\n"
                f"Ya tengo la información:\n"
                f"• Empresa/emprendimiento: *{estado['empresa_cliente']}*\n"
                f"• Necesidad: *{estado.get('objetivo_comercial') or 'Por definir'}*\n\n"
                f"Con esta información ya puedo derivarte a nuestro equipo. "
                f"Un ejecutivo continuará contigo por este mismo chat."
            )
        return "Indícame ambos datos, por favor: *tu nombre, empresa o emprendimiento*."

    if paso == "comercial_completo":
        return (
            "Ya tengo tus datos 😊. La conversación está siendo atendida por un ejecutivo."
        )

    estado["paso"] = "comercial_inicio"
    return procesar_comercial(estado, t)


def pedir_servicio():
    return "Claro 😊 ¿Qué servicio quieres agendar?\n\n" + mostrar_servicios()


def _frases_intencion_agenda():
    """Frases frecuentes en español/Chile para pedir, consultar o gestionar una reserva."""
    return (
        # intención directa
        "agendar", "agendar hora", "agendar una hora", "agendar cita", "agendar una cita",
        "agendar turno", "agendar una reserva", "quiero agendar", "quiero reservar",
        "quiero una reserva", "quiero una cita", "quiero un turno", "quiero una hora",
        "necesito agendar", "necesito reservar", "necesito una cita", "necesito una hora",
        "me gustaria agendar", "me gustaría agendar", "me gustaria reservar", "me gustaría reservar",
        "deseo agendar", "deseo reservar", "puedo agendar", "puedo reservar",
        "se puede agendar", "se puede reservar", "como agendo", "cómo agendo",
        "como reservo", "cómo reservo", "hacer una reserva", "hacer reserva",
        "crear una reserva", "reservar hora", "reservar una hora", "reservar cita",
        "sacar hora", "pedir hora", "tomar hora", "solicitar hora", "solicitar una hora",
        "pedir una cita", "sacar una cita", "tomar una cita", "pedir turno", "sacar turno",
        "reservar turno",

        # disponibilidad / consulta de agenda
        "hora disponible", "horas disponibles", "tienen horas", "tienes horas",
        "hay horas", "hay disponibilidad", "tienen disponibilidad", "tienes disponibilidad",
        "cuando hay hora", "cuándo hay hora", "cuando tienen hora", "cuándo tienen hora",
        "cuando tienes hora", "cuándo tienes hora", "proxima hora", "próxima hora",
        "proximas horas", "próximas horas", "ver disponibilidad", "consultar disponibilidad",
        "ver agenda", "agenda disponible", "disponibilidad de agenda",

        # lenguaje coloquial
        "quiero atenderme", "necesito atenderme", "me quiero atender",
        "quiero pedir hora", "necesito pedir hora", "quiero sacar hora",
        "quiero tomar hora", "me das una hora", "me puede dar una hora",
        "me pueden dar una hora", "dame una hora", "necesito una hora para",
        "quiero ir", "cuando puedo ir", "cuándo puedo ir",

        # acciones de reserva ya iniciada
        "cambiar mi hora", "cambiar la hora", "cambiar mi cita", "reagendar",
        "reagendar hora", "reagendar cita", "mover mi hora", "mover la hora",
        "cancelar reserva", "cancelar hora", "cancelar cita", "anular reserva",
        "anular hora", "anular cita",
    )


def _texto_parece_intencion_agenda(texto):
    t = normalizar_texto(texto)
    if not t:
        return False

    if any(frase in t for frase in _frases_intencion_agenda()):
        return True

    # Patrones flexibles para frases que no coinciden literalmente.
    patrones = (
        r"\b(quiero|necesito|deseo|quisiera|podria|podría|puedo)\b.{0,30}\b(agendar|reservar|hora|cita|turno)\b",
        r"\b(agendar|reservar|pedir|sacar|tomar|solicitar)\b.{0,20}\b(hora|cita|turno|reserva)\b",
        r"\b(hay|tienen|tienes)\b.{0,20}\b(hora|horas|disponibilidad|agenda)\b",
        r"\b(cuando|cuándo)\b.{0,30}\b(hora|horas|disponible|disponibilidad|atenderme|ir)\b",
        r"\b(reagendar|reprogramar|mover|cambiar|cancelar|anular)\b.{0,25}\b(hora|cita|turno|reserva)\b",
    )
    return any(re.search(p, t) for p in patrones)


def intencion_agendar(texto):
    if not negocio_usa_reservas():
        return False
    return _texto_parece_intencion_agenda(texto)


def pregunta_servicios(texto):
    t = normalizar_texto(texto)
    return any(x in t for x in ("servicio", "servicios", "precio", "precios", "cuanto", "valor", "valores", "que haces"))


def quiere_hablar_con_persona(texto):
    t = normalizar_texto(texto)
    frases = (
        "hablar con diego", "hablar con una persona", "hablar con persona",
        "hablar con ejecutivo", "hablar con un ejecutivo", "hablar con alguien",
        "quiero hablar con diego", "quiero hablar con una persona",
        "quiero hablar con un ejecutivo", "contactar a diego", "contacto diego",
        "ejecutivo", "persona real", "humano", "asesor",
    )
    return any(x in t for x in frases)



def obtener_conversacion_por_identificador(identificador, canal="whatsapp"):
    """
    Busca la conversación actual de la empresa/canal y devuelve su fila.
    """
    headers = supabase_headers()
    if not headers or not empresa_actual_id():
        return None

    canal = str(canal or "whatsapp").strip().lower()
    identificador = str(identificador or "").strip()

    if canal == "whatsapp":
        identificador = re.sub(r"\D", "", normalizar_telefono(identificador))
    else:
        identificador = re.sub(r"\D", "", identificador)

    if not identificador:
        return None

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/conversaciones",
        headers=headers,
        params={
            "select": "id,empresa_id,telefono,nombre_contacto,canal,modo_atencion,ultimo_mensaje,ultima_fecha",
            "empresa_id": f"eq.{empresa_actual_id()}",
            "telefono": f"eq.{identificador}",
            "canal": f"eq.{canal}",
            "limit": "1",
        },
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    filas = r.json() if r.content else []
    return filas[0] if filas else None


def enviar_correo_resend(destinatario, asunto, texto=None, html_body=None):
    """
    Envía una notificación con Resend.
    Retorna True si Resend acepta el envío.
    """
    destinatario = str(destinatario or "").strip()
    if not destinatario:
        print("RESEND: empresa sin correo_ejecutivo; no se envía notificación")
        return False

    if not RESEND_API_KEY:
        print("RESEND: falta RESEND_API_KEY en Render")
        return False

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [destinatario],
        "subject": str(asunto or "Nueva conversación derivada"),
    }

    if html_body:
        payload["html"] = html_body
    if texto:
        payload["text"] = texto

    try:
        r = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json() if r.content else {}
        print("RESEND EMAIL OK:", destinatario, data.get("id"))
        return True
    except Exception as e:
        detalle = ""
        try:
            detalle = f" | {r.status_code} {r.text[:600]}"
        except Exception:
            pass
        print("RESEND EMAIL ERROR:", repr(e), detalle)
        return False


def notificar_derivacion_ejecutivo(identificador, canal, estado=None, motivo=None):
    """
    Notifica por correo al ejecutivo de la empresa cuando una conversación
    pasa desde el bot a atención humana.
    """
    correo = str(cfg("correo_ejecutivo", EJECUTIVO_EMAIL) or "").strip()
    if not correo:
        print("DERIVACION: correo_ejecutivo no configurado para", empresa_actual_id())
        return False

    estado = estado or {}
    empresa = str(cfg("empresa_nombre", DEFAULT_NEGOCIO_NOMBRE) or "Empresa").strip()
    nombre = str(
        estado.get("nombre")
        or (obtener_conversacion_por_identificador(identificador, canal) or {}).get("nombre_contacto")
        or "Cliente"
    ).strip()
    empresa_cliente = str(estado.get("empresa_cliente") or "").strip()
    objetivo = str(estado.get("objetivo_comercial") or motivo or "Solicita atención de un ejecutivo").strip()
    canal_label = "WhatsApp" if str(canal).lower() == "whatsapp" else "Instagram"
    identificador_limpio = str(identificador or "").strip()

    conversacion = obtener_conversacion_por_identificador(identificador, canal) or {}
    conversacion_id = str(conversacion.get("id") or "").strip()
    portal_url = f"{PORTAL_ORIGIN}/portal.html"
    portal_params = []
    if conversacion_id:
        portal_params.extend([f"conversacion={conversacion_id}", "accion=tomar"])
    # Para una demo activa, permitir acceso temporal desde el correo sin login.
    try:
        plan_notif = estado_suscripcion_empresa(empresa_actual_id())
        if str(plan_notif.get("tipo_plan") or "").lower() == "demo" and str(plan_notif.get("estado") or "").lower() == "activo":
            demo_notif_token = str(_core_token_por_empresa(empresa_actual_id()) or "").strip()
            if demo_notif_token:
                portal_params.append(f"demo_token={demo_notif_token}")
    except Exception as e:
        print("DERIVACION DEMO PORTAL TOKEN SKIP:", repr(e))
    if portal_params:
        portal_url += "?" + "&".join(portal_params)

    asunto = f"🔔 Nueva conversación para ejecutivo — {empresa}"

    texto = (
        f"Nueva conversación derivada a ejecutivo\n\n"
        f"Empresa: {empresa}\n"
        f"Cliente: {nombre}\n"
        f"Empresa/emprendimiento del cliente: {empresa_cliente or 'No informado'}\n"
        f"Necesidad: {objetivo}\n"
        f"Canal: {canal_label}\n"
        f"Identificador: {identificador_limpio}\n\n"
        f"Ingresa al Portal Nexia para continuar la conversación:\n{portal_url}"
    )

    html_body = f"""
    <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#111827">
      <h2 style="margin-bottom:6px">🔔 Nueva conversación para ejecutivo</h2>
      <p style="margin-top:0;color:#6b7280">Se derivó una conversación desde el asistente de {html.escape(empresa)}.</p>
      <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:12px;padding:18px">
        <p><strong>Cliente:</strong> {html.escape(nombre)}</p>
        <p><strong>Empresa/emprendimiento:</strong> {html.escape(empresa_cliente or "No informado")}</p>
        <p><strong>Necesidad:</strong> {html.escape(objetivo)}</p>
        <p><strong>Canal:</strong> {html.escape(canal_label)}</p>
        <p><strong>Identificador:</strong> {html.escape(identificador_limpio)}</p>
      </div>
      <p style="margin-top:22px">
        <a href="{html.escape(portal_url)}"
           style="display:inline-block;background:#111827;color:#fff;text-decoration:none;padding:12px 18px;border-radius:9px">
          Abrir conversación
        </a>
      </p>
    </div>
    """

    return enviar_correo_resend(
        correo,
        asunto,
        texto=texto,
        html_body=html_body,
    )


def derivar_a_ejecutivo(identificador, canal="whatsapp", estado=None, motivo=None):
    """
    Cambia la conversación a modo ejecutivo y envía una sola notificación
    cuando efectivamente se produce la transición bot -> ejecutivo.
    """
    try:
        conversacion = obtener_conversacion_por_identificador(identificador, canal)
        if not conversacion:
            print("DERIVACION: conversación no encontrada")
            return False

        modo_anterior = str(conversacion.get("modo_atencion") or "bot").lower()
        if modo_anterior == "ejecutivo":
            print("DERIVACION: ya estaba en modo ejecutivo; no se duplica correo")
            return True

        establecer_modo_atencion(conversacion["id"], "ejecutivo")

        if estado is not None:
            estado["paso"] = "derivado_ejecutivo"

        notificar_derivacion_ejecutivo(
            identificador,
            canal,
            estado=estado,
            motivo=motivo,
        )
        return True

    except Exception as e:
        print("DERIVACION EJECUTIVO ERROR:", repr(e))
        return False


def mensaje_derivacion_ejecutivo():
    empresa = str(cfg("empresa_nombre", DEFAULT_NEGOCIO_NOMBRE) or "").strip()
    return (
        f"Perfecto 🙌 Ya envié tu solicitud al equipo de {empresa}. "
        "Un ejecutivo continuará contigo por este mismo chat."
    )


def mensaje_contacto_persona():
    # Compatibilidad con llamadas antiguas: ya no entrega un teléfono personal.
    return mensaje_derivacion_ejecutivo()


def es_menu(texto):
    return normalizar_texto(texto) in {"menu", "inicio", "reiniciar", "empezar de nuevo", "hola"}


def es_cancelar(texto):
    return normalizar_texto(texto) in {"cancelar", "salir", "no", "no gracias", "chao"}


def email_valido(texto):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", (texto or "").strip()))


def formatear_whatsapp_reserva(valor):
    """Formatea el número capturado desde WhatsApp para mostrarlo en la reserva."""
    raw = str(valor or "").strip()
    raw = raw.replace("whatsapp:", "").strip()
    digits = re.sub(r"\D", "", raw)

    if digits.startswith("56") and len(digits) == 11:
        # 56950064242 -> +56 9 5006 4242
        return f"+56 {digits[2]} {digits[3:7]} {digits[7:11]}"
    if digits:
        return f"+{digits}" if raw.startswith("+") or digits.startswith("56") else digits
    return raw


def procesar_agenda(estado, texto):
    t = normalizar_texto(texto)

    if es_cancelar(texto):
        telefono = estado["telefono"]
        reset_estado(estado.get("_session_key") or telefono)
        return "No hay problema 😊. Cuando quieras agendar una hora, escríbeme nuevamente."

    # 1) SERVICIO
    if estado["paso"] in {"inicio", "servicio"}:
        if corte_ambiguo(texto):
            estado["paso"] = "servicio"
            return "Perfecto. ¿El corte es para *hombre* o *mujer*?"

        servicio = detectar_servicio(texto)
        if servicio:
            estado["servicio"] = servicio
            estado["paso"] = "fecha"

            # Si el mismo mensaje trae fecha y hora, aprovecharla.
            solicitada = fecha_hora_desde_texto(texto)
            if solicitada:
                if verificar_disponibilidad(solicitada):
                    estado["fecha_hora"] = solicitada.isoformat()
                    estado["paso"] = "nombre"
                    return f"Perfecto ✅ Tengo disponible {formatear_fecha(solicitada)}. ¿Cuál es tu nombre y apellido?"
                return "Esa hora no está disponible. Dime otro día/hora o escribe *PRÓXIMAS HORAS*."

            # Si trae solo fecha, listar ese día.
            fecha = detectar_fecha(texto)
            if fecha:
                horas = buscar_horas_dia(fecha)
                estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
                estado["paso"] = "seleccionar_hora"
                if not horas:
                    return "Ese día no tiene horas disponibles. Dime otra fecha o escribe *PRÓXIMAS HORAS*."
                return f"Estas son las horas disponibles para ese día 👇\n\n{listar_horas(horas)}\n\nResponde con el número de la hora que prefieres."

            return (
                f"Perfecto 👍 Servicio: *{servicios_actuales()[servicio]['nombre']}* ({servicios_actuales()[servicio]['precio_texto']}).\n\n"
                "¿Qué día te gustaría venir? Puedes escribir, por ejemplo, *mañana*, *viernes* o *12 de septiembre*."
            )

        estado["paso"] = "servicio"
        return pedir_servicio()

    # 2) FECHA
    if estado["paso"] == "fecha":
        if "proxima" in t or "disponible" in t or "cuando" in t:
            horas = buscar_proximas_horas()
        else:
            solicitada = fecha_hora_desde_texto(texto)
            if solicitada:
                if verificar_disponibilidad(solicitada):
                    estado["fecha_hora"] = solicitada.isoformat()
                    estado["paso"] = "nombre"
                    return f"Perfecto ✅ Tengo disponible {formatear_fecha(solicitada)}. ¿Cuál es tu nombre y apellido?"
                return "Esa hora no está disponible. Dime otra hora o escribe *PRÓXIMAS HORAS*."
            fecha = detectar_fecha(texto)
            horas = buscar_horas_dia(fecha) if fecha else buscar_proximas_horas()

        estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
        estado["paso"] = "seleccionar_hora"
        if not horas:
            estado["paso"] = "fecha"
            return "No encontré horas disponibles. Dime otra fecha, por favor."
        return f"Tengo estas horas disponibles 👇\n\n{listar_horas(horas)}\n\nResponde con el número de la hora que prefieres."

    # 3) ELEGIR HORA DE LISTA
    if estado["paso"] == "seleccionar_hora":
        m = re.fullmatch(r"\s*(\d{1,2})\s*", t)
        ofrecidas = estado.get("horas_ofrecidas") or []
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(ofrecidas):
                slot = datetime.fromisoformat(ofrecidas[idx])
                if verificar_disponibilidad(slot):
                    estado["fecha_hora"] = slot.isoformat()
                    estado["paso"] = "nombre"
                    return f"Excelente ✅ {formatear_fecha(slot)}. ¿Cuál es tu nombre y apellido?"
                estado["paso"] = "fecha"
                return "Esa hora acaba de ocuparse. Dime otra fecha y te muestro nuevas opciones."

        solicitada = fecha_hora_desde_texto(texto)
        if solicitada and verificar_disponibilidad(solicitada):
            estado["fecha_hora"] = solicitada.isoformat()
            estado["paso"] = "nombre"
            return f"Excelente ✅ {formatear_fecha(solicitada)}. ¿Cuál es tu nombre y apellido?"
        return "Elige una de las horas escribiendo su número, o dime otra fecha."

    # 4) NOMBRE
    if estado["paso"] == "nombre":
        if len((texto or "").strip()) < 2 or email_valido(texto):
            return "Indícame tu nombre y apellido, por favor."
        estado["nombre"] = (texto or "").strip()[:120]
        estado["paso"] = "correo"
        return "Gracias 😊 ¿Cuál es tu correo electrónico? Lo usaremos para enviarte la invitación de la reserva."

    # 5) CORREO
    if estado["paso"] == "correo":
        correo = (texto or "").strip().lower()
        if not email_valido(correo):
            return "Ese correo no parece válido. Escríbelo nuevamente, por ejemplo: nombre@correo.cl"
        estado["correo"] = correo
        estado["paso"] = "confirmar"
        servicio = servicios_actuales()[estado["servicio"]]
        fecha = datetime.fromisoformat(estado["fecha_hora"])
        return (
            "Confirma tu reserva 👇\n\n"
            f"✂️ Servicio: {servicio['nombre']}\n"
            f"💰 Valor: {servicio['precio_texto']}\n"
            f"📅 {formatear_fecha(fecha)}\n"
            f"👤 {estado['nombre']}\n"
            f"📱 WhatsApp: {formatear_whatsapp_reserva(estado.get('telefono'))}\n"
            f"📧 {estado['correo']}\n"
            + ((f"📍 {cfg('direccion')}\n") if str(cfg("direccion", "") or "").strip() else "")
            + "\nSi todo está correcto, escribe *CONFIRMAR*."
        )

    # 6) CONFIRMAR -> RESERVA INMEDIATA, SIN PAGO
    if estado["paso"] == "confirmar":
        if t not in {"confirmar", "confirmo", "si", "sí", "ok"}:
            return "Para crear la reserva escribe *CONFIRMAR*. Si quieres salir, escribe *CANCELAR*."

        inicio = datetime.fromisoformat(estado["fecha_hora"])
        resultado = crear_evento(
            inicio=inicio,
            servicio_codigo=estado["servicio"],
            nombre=estado["nombre"],
            telefono=normalizar_telefono(estado["telefono"]),
            correo=estado["correo"],
        )
        if resultado.get("ocupada"):
            estado["paso"] = "fecha"
            estado["fecha_hora"] = None
            return "Esa hora acaba de ocuparse. Dime otra fecha y te muestro nuevas opciones."
        if not resultado.get("ok"):
            return "Tuve un problema creando la reserva en Calendar. Intenta nuevamente en unos segundos."

        servicio = servicios_actuales()[estado["servicio"]]
        nombre = estado["nombre"]
        correo = estado["correo"]
        fecha_txt = formatear_fecha(inicio)
        telefono = estado["telefono"]

        # Registrar también la reserva en Supabase para el Portal Nexia.
        # Un fallo de Supabase no invalida la reserva ya confirmada en Calendar.
        guardar_reserva_supabase(
            telefono=telefono,
            nombre_cliente=nombre,
            servicio_nombre=servicio["nombre"],
            inicio=inicio,
            google_event_id=resultado.get("evento_id"),
        )

        # Core usa una clave de sesión aislada por empresa; legacy usa teléfono.
        # Reiniciamos la clave correcta para que una reserva terminada no deje
        # pasos antiguos activos al siguiente mensaje.
        reset_estado(estado.get("_session_key") or telefono)
        return (
            "✅ *¡Reserva confirmada!*\n\n"
            f"✂️ Servicio: {servicio['nombre']}\n"
            f"💰 Valor: {servicio['precio_texto']}\n"
            f"📅 {fecha_txt}\n"
            f"👤 {nombre}\n"
            f"📱 WhatsApp: {formatear_whatsapp_reserva(telefono)}\n"
            f"📧 {correo}\n"
            + ((f"📍 {cfg('direccion')}\n") if str(cfg("direccion", "") or "").strip() else "")
            + f"⏱️ Duración: {cfg_int('duracion_reserva', DEFAULT_DURACION_RESERVA)} minutos\n\n"
            "No necesitas realizar ningún pago para agendar. ¡Te esperamos! 😊"
        )

    estado["paso"] = "inicio"
    return mensaje_bienvenida()



# ============================================================
# NEXI CORE V1 - RAMA EXPERIMENTAL AISLADA
# ============================================================
# Esta capa NO reemplaza los flujos productivos existentes (ej. Diego).
# Se usa mediante endpoints /core/* y crea tenants demo separados.

import uuid
import time
CORE_ONBOARDING_VERSION = "nexi-core-v1.5"
CORE_DEMO_CHANNEL = "web"

CORE_WEB_MAX_PAGES = int(os.getenv("CORE_WEB_MAX_PAGES", "6"))
CORE_WEB_MAX_CHARS_PAGE = int(os.getenv("CORE_WEB_MAX_CHARS_PAGE", "18000"))
CORE_WEB_TIMEOUT = int(os.getenv("CORE_WEB_TIMEOUT", "8"))
CORE_WEB_USER_AGENT = os.getenv(
    "CORE_WEB_USER_AGENT",
    "NexiCoreBot/1.0 (+https://nexia-tech.com)"
).strip()

CORE_COMMON_FIELDS = [
    "nombre_contacto",
    "nombre_negocio",
    "nombre_asistente",
    "rubro",
    "direccion_fisica",
    "ecommerce_provider",
    "productos_servicios",
    "presencia_digital",
    "objetivo",
    "personas_atencion",
]

CORE_QUESTIONS = {
    "nombre_contacto": {
        "text": "Para comenzar, ¿cuál es tu nombre?",
        "kind": "text",
    },
    "nombre_negocio": {
        "text": "¿Cómo se llama tu negocio, marca o actividad?",
        "kind": "text",
        "placeholder": "Ej: Veterinaria Luna, Diego Estilista, Estudio Pérez",
    },
    "nombre_asistente": {
        "text": "¿Qué nombre quieres darle a tu asistente?",
        "kind": "text",
        "placeholder": "Ej: Luna, Sofía, Max, Asistente Virtual",
        "help": "Este será el nombre con el que se presentará frente a tus clientes.",
    },
    "rubro": {
        "text": "Cuéntame brevemente, ¿a qué se dedica tu negocio o actividad?",
        "kind": "long_text",
    },
    "direccion_fisica": {
        "text": "¿Tu negocio tiene una dirección física donde atiende clientes?",
        "kind": "physical_address",
        "help": "Si atiendes presencialmente, agrega la dirección. También puedes pegar el enlace exacto de Google Maps si ya lo tienes.",
    },
    "ecommerce_provider": {
        "text": "¿Tienes una tienda online?",
        "kind": "ecommerce_provider",
        "help": "Si usas Jumpseller o Shopify, Nexia tomará productos, precios y stock directamente desde la tienda cuando la conectes en el Portal.",
        "options": ["Jumpseller", "Shopify", "Otra plataforma", "No tengo tienda online"],
    },
    "productos_servicios": {
        "text": "¿Qué productos o servicios ofrece tu negocio?",
        "kind": "service_catalog",
        "help": "Agrega uno por uno. El precio es opcional: si no lo publicas, déjalo vacío.",
    },
    "presencia_digital": {
        "text": "Agrega tu sitio web y redes sociales públicas.",
        "kind": "digital_presence",
        "help": "Todo es opcional. Si agregas un sitio web, después podrás decidir si quieres usar su información pública para responder consultas. Las redes sociales se guardan para poder informarlas a tus clientes.",
    },
    "objetivo": {
        "text": "¿Qué necesitas que haga tu asistente?",
        "kind": "multi_choice",
        "options": [
            "Responder consultas",
            "Atender clientes",
            "Vender productos o servicios",
            "Informar precios",
            "Guardar datos de personas interesadas",
            "Agendar o reservar",
            "Hacer seguimiento",
            "Derivar a una persona",
            "Resolver preguntas frecuentes",
            "Automatizar otro proceso",
        ],
        "help": "Puedes elegir más de una opción.",
    },
    "personas_atencion": {
        "text": "¿Cuántas personas atienden actualmente consultas o clientes?",
        "kind": "choice",
        "options": ["Solo yo", "2 a 3 personas", "4 a 10 personas", "Más de 10"],
        "help": "Esto nos ayuda a preparar la derivación y el trabajo del equipo.",
    },
    "aprende_web": {
        "text": "¿Quieres usar la información pública de tu sitio web para responder consultas?",
        "kind": "choice",
        "options": ["Sí, aprender de mi web", "No, solo guardar el enlace"],
        "help": "Si eliges Sí, se leerán páginas públicas relevantes de tu sitio y se guardará una versión resumida para responder consultas.",
    },
    "whatsapp_demo": {
        "text": "¿Cuál es el número de WhatsApp desde el que probarás tu asistente?",
        "kind": "phone",
        "placeholder": "+56912345678",
    },
    "agenda_que": {
        "text": "Veo que necesitas agenda o reservas. ¿Qué se agenda?",
        "kind": "text",
    },
    "agenda_duracion": {
        "text": "¿Cuánto dura normalmente cada atención o reserva?",
        "kind": "text",
    },
    "agenda_dias": {
        "text": "¿Qué días se puede reservar?",
        "kind": "text",
    },
    "agenda_horario": {
        "text": "¿En qué horario se puede agendar?",
        "kind": "text",
    },
    "agenda_buffer": {
        "text": "¿Necesitas tiempo entre una atención y otra?",
        "kind": "text_or_no",
    },
    "guardar_interesados": {
        "text": "¿Quieres guardar datos de las personas interesadas para contactarlas después?",
        "kind": "choice",
        "options": [
            "Sí",
            "No",
            "Sí, solo nombre y contacto",
            "Sí, quiero elegir qué datos guardar",
        ],
    },
    "handoff": {
        "text": "¿Quieres permitir que una conversación se derive a una persona cuando sea necesario?",
        "kind": "choice",
        "options": ["Sí, permitir derivaciones", "No permitir derivaciones"],
        "help": "Si un cliente necesita atención humana, la conversación podrá pausarse y se avisará a la persona encargada.",
    },
    "email_contacto": {
        "text": "¿Qué correo quieres usar para tu acceso al Portal Nexia?",
        "kind": "email",
        "help": "Usaremos este correo para asociar tu acceso al Portal Nexia y también para enviarte avisos durante tu prueba gratuita. Este dato es privado y nunca se mostrará a tus clientes.",
    },
    "tono": {
        "text": "¿Cómo quieres que se comunique tu asistente?",
        "kind": "multi_choice",
        "options": ["Cercano", "Profesional", "Formal", "Amigable", "Directo y breve", "Puede usar emojis", "Sin emojis"],
    },
    "desconocido": {
        "text": "Si tu asistente no sabe una respuesta, ¿qué debe hacer?",
        "kind": "choice",
        "options": ["Decir que no cuenta con esa información", "Pedir más detalles", "Derivar a una persona"],
    },
}


def _core_norm(v):
    return normalizar_texto(str(v or "")).strip()


def _core_tipo(v):
    # V1.5 se centra en trabajo/negocio. Se mantiene compatibilidad con demos antiguas.
    t=_core_norm(v)
    if any(x in t for x in ("personal","para mi","persona")):
        return "personal"
    return "empresa"



def _core_si(v):
    t=_core_norm(v)
    return t.startswith("si") or t in {"yes","s","1","true"}


def _core_necesita_agenda(datos):
    t=" ".join(_core_norm(datos.get(k)) for k in ("objetivo","productos_servicios"))
    return any(x in t for x in ("agenda","agendar","reserva","reservar","cita","hora","reunion","turno"))



def _core_canales_texto(datos):
    return " ".join(_core_norm(datos.get(k)) for k in ("canales_actuales","canales_deseados"))


def _core_presencia(datos):
    raw = datos.get("presencia_digital")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}

def _core_email_valido(value):
    value = str(value or "").strip()
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))

def _core_es_handoff(texto):
    """
    Detecta solicitudes explícitas de contacto humano, incluso cuando
    el usuario pide hablar con una persona por su nombre.
    """
    t = _core_norm(texto)

    directas = (
        "derivar", "derivame", "derívame",
        "hablar con una persona", "hablar con persona", "hablar con alguien",
        "hablar con ejecutivo", "hablar con un ejecutivo",
        "persona real", "humano", "asesor", "ejecutivo",
        "contactar a alguien", "quiero contacto",
        "contactar por correo", "contacto por correo",
        "quiero que me contacten", "que me contacten",
    )
    if any(x in t for x in directas):
        return True

    # "quiero hablar con Cristian", "puedo hablar con María", etc.
    if re.search(r"\b(?:quiero|quisiera|necesito|puedo|podria|podría)?\s*hablar\s+con\s+[a-záéíóúñü]{2,}(?:\s+[a-záéíóúñü]{2,})?", t):
        return True

    if re.search(r"\bcontactar\s+(?:a|con)\s+[a-záéíóúñü]{2,}", t):
        return True

    return False


def _core_es_confirmacion(texto):
    t = _core_norm(texto)
    return t in {
        "si", "sí", "ok", "okay", "dale", "claro", "bueno",
        "por favor", "quiero", "de acuerdo", "ya", "perfecto"
    }


def _core_es_rechazo(texto):
    t = _core_norm(texto)
    return t in {"no", "nop", "no gracias", "ahora no", "mejor no"}




def _core_cancelar_handoff_texto(texto):
    t = _core_norm(texto)
    return any(x in t for x in (
        "salir",
        "cancelar",
        "no quiero",
        "no gracias",
        "dejalo",
        "déjalo",
        "seguir con el bot",
        "seguir con el asistente",
        "volver",
        "volver al bot",
        "volver al asistente",
    ))


def _core_handoff_lookup(empresa_id, identificador, canal):
    ident = _normalizar_identificador_demo(identificador, canal)
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nexi_core_handoff_sesiones",
        headers=_core_headers(),
        params={
            "select": "*",
            "empresa_id": f"eq.{empresa_id}",
            "identificador": f"eq.{ident}",
            "canal": f"eq.{canal}",
            "limit": "1",
        },
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json() if r.content else []
    return rows[0] if rows else None

def _core_handoff_upsert(empresa_id, identificador, canal, payload):
    ident = _normalizar_identificador_demo(identificador, canal)
    row = {
        "empresa_id": empresa_id,
        "identificador": ident,
        "canal": canal,
        "updated_at": datetime.now(pytz.UTC).isoformat(),
        **payload,
    }
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_core_handoff_sesiones",
        headers=_core_headers("resolution=merge-duplicates,return=representation"),
        params={"on_conflict": "empresa_id,canal,identificador"},
        json=row,
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json() if r.content else []
    return rows[0] if rows else row

def _core_handoff_elapsed_minutes(handoff):
    raw=str((handoff or {}).get("started_at") or "").strip()
    if not raw:
        return None
    try:
        inicio=datetime.fromisoformat(raw.replace("Z","+00:00"))
        if inicio.tzinfo is None:
            inicio=pytz.UTC.localize(inicio)
        return max(0.0,(datetime.now(pytz.UTC)-inicio.astimezone(pytz.UTC)).total_seconds()/60.0)
    except Exception:
        return None

def _core_handoff_expirado(handoff):
    mins=_core_handoff_elapsed_minutes(handoff)
    return mins is not None and CORE_HANDOFF_TIMEOUT_MINUTOS > 0 and mins >= CORE_HANDOFF_TIMEOUT_MINUTOS

def _core_handoff_request(empresa_id, identificador, canal, datos, motivo):
    """Registra handoff Core y envía correo con acceso directo a la conversación."""
    perfil = _core_profile(empresa_id) or {}
    pd = perfil.get("datos") or {}
    tipo = _core_tipo(pd.get("tipo_cliente")) or "empresa"
    contacto = str(pd.get("nombre_contacto") or "Cliente").strip()
    empresa_demo = str(pd.get("empresa_nombre") or pd.get("nombre_negocio") or pd.get("profesion") or "").strip()
    # El destinatario es el correo del ejecutivo configurado para el tenant.
    correo = str(cfg("correo_ejecutivo", "") or EJECUTIVO_EMAIL or "").strip()

    payload = {
        "empresa_id": empresa_id,
        "identificador": _normalizar_identificador_demo(identificador, canal),
        "canal": canal,
        "nombre_contacto": str(datos.get("nombre") or contacto or "").strip() or None,
        "empresa_contacto": str(datos.get("empresa") or empresa_demo or "").strip() or None,
        "motivo": str(motivo or datos.get("motivo") or "").strip() or "Solicita atención humana",
        "estado": "pendiente",
        "created_at": datetime.now(pytz.UTC).isoformat(),
        "updated_at": datetime.now(pytz.UTC).isoformat(),
    }
    rr = requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_core_handoffs",
        headers=_core_headers("return=representation"),
        json=payload,
        timeout=SUPABASE_TIMEOUT,
    )
    rr.raise_for_status()
    rows = rr.json() if rr.content else []
    if rows:
        payload = dict(rows[0])

    # Busca la conversación normalizada del Portal. Las demos WhatsApp se sincronizan
    # también con public.conversaciones/public.mensajes en V1.9.
    conv = obtener_conversacion_por_identificador(identificador, canal) or {}
    conversacion_id = str(conv.get("id") or "").strip()
    handoff_id = str(payload.get("id") or "").strip()
    params = []
    if conversacion_id:
        params.append(f"conversacion={conversacion_id}")
    if handoff_id:
        params.append(f"handoff={handoff_id}")
    params.append("accion=tomar")

    # Si la conversación pertenece a una demo activa, el correo abre el Portal
    # con el token temporal de esa misma demo. Así no pide usuario/contraseña.
    # Clientes pagados y superadmin siguen usando su autenticación normal.
    demo_portal_token = ""
    try:
        plan_handoff = estado_suscripcion_empresa(empresa_id)
        if str(plan_handoff.get("tipo_plan") or "").lower() == "demo" and str(plan_handoff.get("estado") or "").lower() == "activo":
            demo_portal_token = str(_core_token_por_empresa(empresa_id) or "").strip()
    except Exception as e:
        print("HANDOFF DEMO PORTAL TOKEN SKIP:", repr(e))
    if demo_portal_token:
        params.append(f"demo_token={demo_portal_token}")

    portal_url = f"{PORTAL_ORIGIN}/portal.html" + ("?" + "&".join(params) if params else "")

    if correo:
        negocio = str(cfg("empresa_nombre", "Nexia") or "Nexia")
        asunto = f"🔔 Solicitud de atención — {negocio}"
        texto_mail = (
            f"Nueva solicitud de atención humana\n\n"
            f"Nombre: {payload.get('nombre_contacto') or 'No informado'}\n"
            f"Empresa/actividad: {payload.get('empresa_contacto') or 'No informado'}\n"
            f"Motivo: {payload.get('motivo')}\n"
            f"Canal: {canal}\n\n"
            f"Abre directamente la conversación en el Portal Nexia:\n{portal_url}"
        )
        html_mail = f"""
        <div style="font-family:Arial,sans-serif;max-width:620px;margin:auto;color:#111827">
          <div style="padding:22px 0"><strong style="font-size:20px">NEXIA</strong></div>
          <h2 style="margin-bottom:6px">🔔 Nueva solicitud de atención</h2>
          <p style="margin-top:0;color:#6b7280">Un cliente de {html.escape(negocio)} necesita atención humana.</p>
          <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:14px;padding:18px">
            <p><strong>Cliente:</strong> {html.escape(str(payload.get('nombre_contacto') or 'No informado'))}</p>
            <p><strong>Empresa/actividad:</strong> {html.escape(str(payload.get('empresa_contacto') or 'No informado'))}</p>
            <p><strong>Motivo:</strong> {html.escape(str(payload.get('motivo') or ''))}</p>
            <p><strong>Canal:</strong> {html.escape(str(canal).title())}</p>
          </div>
          <p style="margin:24px 0 8px">
            <a href="{html.escape(portal_url)}" style="display:inline-block;background:#111827;color:white;text-decoration:none;padding:13px 20px;border-radius:10px;font-weight:700">Abrir conversación</a>
          </p>
          <p style="font-size:12px;color:#6b7280">{"Acceso temporal: este enlace abre el Portal sin contraseña durante tu prueba gratuita." if demo_portal_token else "Por seguridad, el acceso de clientes permanentes requiere iniciar sesión."} La conversación se toma manualmente dentro del portal.</p>
        </div>
        """
        enviar_correo_resend(correo, asunto, texto=texto_mail, html_body=html_mail)
    return payload

def _core_handoff_prompt(tipo):
    if tipo == "empresa":
        return (
            "Claro 😊. Voy a registrar tu solicitud para que una persona continúe contigo por este mismo canal.\n\n"
            "Escríbeme en un solo mensaje:\n• tu nombre\n• tu empresa\n• el motivo de tu consulta"
        )
    if tipo == "profesional":
        return (
            "Claro 😊. Voy a registrar tu solicitud para que una persona continúe contigo por este mismo canal.\n\n"
            "Escríbeme en un solo mensaje:\n• tu nombre\n• tu profesión o actividad\n• el motivo de tu consulta"
        )
    return (
        "Claro 😊. Voy a registrar tu solicitud para que una persona continúe contigo por este mismo canal.\n\n"
        "Escríbeme en un solo mensaje:\n• tu nombre\n• el motivo de tu consulta"
    )

def _core_parse_handoff_details(tipo, texto):
    limpio = str(texto or "").strip()
    partes = [p.strip() for p in re.split(r"[,;|\\n]+", limpio) if p.strip()]
    if tipo == "empresa":
        return {
            "nombre": partes[0] if len(partes) > 0 else "",
            "empresa": partes[1] if len(partes) > 1 else "",
            "motivo": ", ".join(partes[2:]) if len(partes) > 2 else (partes[1] if len(partes) > 1 else ""),
        }
    if tipo == "profesional":
        return {
            "nombre": partes[0] if len(partes) > 0 else "",
            "empresa": partes[1] if len(partes) > 1 else "",
            "motivo": ", ".join(partes[2:]) if len(partes) > 2 else (partes[1] if len(partes) > 1 else ""),
        }
    return {
        "nombre": partes[0] if len(partes) > 0 else "",
        "empresa": "",
        "motivo": ", ".join(partes[1:]) if len(partes) > 1 else "",
    }


def _core_secuencia(datos):
    seq=list(CORE_COMMON_FIELDS)

    ecommerce_provider = _core_norm(datos.get("ecommerce_provider"))
    if ecommerce_provider in {"jumpseller", "shopify"}:
        seq = [k for k in seq if k != "productos_servicios"]

    presencia=_core_presencia(datos)
    sitio=str(presencia.get("web") or "").strip()
    if sitio:
        seq.append("aprende_web")

    canales=_core_canales_texto(datos)
    if "whatsapp" in canales:
        seq.append("whatsapp_demo")

    if _core_necesita_agenda(datos):
        seq += ["agenda_que","agenda_duracion","agenda_dias","agenda_horario","agenda_buffer"]

    objetivo=_core_norm(datos.get("objetivo"))
    if any(x in objetivo for x in ("personas interesadas","interesad","seguimiento","vender")):
        seq.append("guardar_interesados")

    seq.append("handoff")
    if _core_si(datos.get("handoff")):
        seq.append("email_contacto")

    seq += ["tono","desconocido"]

    out=[]
    for k in seq:
        if k not in out:
            out.append(k)
    return out


def _core_next_question(datos):
    for key in _core_secuencia(datos):
        val=datos.get(key)
        if val is None or (isinstance(val,str) and not val.strip()):
            q=dict(CORE_QUESTIONS[key]); q["key"]=key
            return q
    return None


def _core_headers(prefer=None):
    h=backend_headers()
    if not h: raise RuntimeError("Supabase no configurado")
    if prefer: h={**h,"Prefer":prefer}
    return h


def _core_get_session(token):
    r=requests.get(f"{SUPABASE_URL}/rest/v1/nexi_core_onboarding",headers=_core_headers(),params={"select":"*","token":f"eq.{token}","limit":"1"},timeout=SUPABASE_TIMEOUT)
    r.raise_for_status(); rows=r.json() if r.content else []
    return rows[0] if rows else None


def _core_save_session(token, payload):
    payload={**payload,"updated_at":datetime.now(pytz.UTC).isoformat()}
    r=requests.patch(f"{SUPABASE_URL}/rest/v1/nexi_core_onboarding",headers=_core_headers("return=representation"),params={"token":f"eq.{token}"},json=payload,timeout=SUPABASE_TIMEOUT)
    r.raise_for_status(); rows=r.json() if r.content else []
    return rows[0] if rows else payload



class _CoreHTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip=0
        self.parts=[]
        self.links=[]
    def handle_starttag(self, tag, attrs):
        tag=(tag or "").lower()
        if tag in {"script","style","noscript","svg"}:
            self.skip += 1
        if tag == "a":
            href=dict(attrs).get("href")
            if href:
                self.links.append(href)
    def handle_endtag(self, tag):
        if (tag or "").lower() in {"script","style","noscript","svg"} and self.skip:
            self.skip -= 1
    def handle_data(self, data):
        if not self.skip:
            txt=re.sub(r"\s+"," ",str(data or "")).strip()
            if txt:
                self.parts.append(txt)


def _core_normalizar_url_web(url):
    raw=str(url or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw="https://" + raw
    p=urlparse(raw)
    if p.scheme not in {"http","https"} or not p.netloc:
        return ""
    return raw.rstrip("/")


def _core_fetch_web_page(url):
    r=requests.get(
        url,
        headers={"User-Agent": CORE_WEB_USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        timeout=CORE_WEB_TIMEOUT,
        allow_redirects=True,
    )
    r.raise_for_status()
    ctype=str(r.headers.get("content-type") or "").lower()
    if "text/html" not in ctype:
        return None
    parser=_CoreHTMLTextParser()
    parser.feed(r.text[:500000])
    texto="\n".join(parser.parts)
    texto=re.sub(r"\n{3,}","\n\n",texto).strip()
    return {
        "url": r.url,
        "texto": texto[:CORE_WEB_MAX_CHARS_PAGE],
        "links": parser.links[:250],
    }


def _core_crawl_web(url):
    inicio=_core_normalizar_url_web(url)
    if not inicio:
        return []
    host=urlparse(inicio).netloc.lower()
    cola=[inicio]
    vistos=set()
    paginas=[]

    while cola and len(paginas) < CORE_WEB_MAX_PAGES:
        actual, cola = cola[0], cola[1:]
        actual=urldefrag(actual)[0].rstrip("/")
        if not actual or actual in vistos:
            continue
        vistos.add(actual)
        try:
            data=_core_fetch_web_page(actual)
        except Exception as e:
            print("CORE WEB FETCH ERROR:", actual, repr(e))
            continue
        if not data or len(data.get("texto") or "") < 80:
            continue
        paginas.append({"url":data["url"],"texto":data["texto"]})
        for href in data.get("links") or []:
            try:
                u=urldefrag(urljoin(data["url"],href))[0]
                p=urlparse(u)
                if p.scheme in {"http","https"} and p.netloc.lower()==host:
                    if not re.search(r"\.(?:jpg|jpeg|png|gif|webp|svg|pdf|zip|mp4|mp3)(?:$|\?)",u,re.I):
                        cola.append(u)
            except Exception:
                pass
    return paginas


def _core_resumir_web(paginas):
    if not paginas:
        return ""
    contenido=[]
    for p in paginas:
        contenido.append(f"URL: {p['url']}\n{p['texto']}")
    bruto="\n\n---\n\n".join(contenido)[:50000]

    if not openai_client:
        return bruto[:12000]

    system=(
        "Extrae conocimiento empresarial útil desde el contenido público de un sitio web. "
        "No inventes nada. Resume solo información explícita: descripción, productos, servicios, "
        "precios si aparecen, horarios, ubicaciones, preguntas frecuentes, políticas y contacto público. "
        "Devuelve texto claro y compacto en español."
    )
    try:
        r=openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role":"system","content":system},
                {"role":"user","content":bruto},
            ],
        )
        return (r.choices[0].message.content or "").strip()[:18000]
    except Exception as e:
        print("CORE WEB SUMMARY ERROR:",repr(e))
        return bruto[:12000]


def _core_guardar_conocimiento_web(empresa_id, url):
    paginas=_core_crawl_web(url)
    resumen=_core_resumir_web(paginas)
    if not resumen:
        return {"ok":False,"pages":0,"summary":""}

    rows=[]
    for i,p in enumerate(paginas,1):
        rows.append({
            "empresa_id":empresa_id,
            "fuente":"web",
            "url":p["url"],
            "titulo":f"Página web {i}",
            "contenido":p["texto"],
            "resumen":resumen if i==1 else None,
            "activo":True,
            "updated_at":datetime.now(pytz.UTC).isoformat(),
        })
    r=requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_core_conocimiento",
        headers=_core_headers("return=minimal"),
        json=rows,
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    print("NEXI CORE WEB KNOWLEDGE OK:",empresa_id,len(rows),url)
    return {"ok":True,"pages":len(rows),"summary":resumen}


def _core_conocimiento_web(empresa_id, consulta):
    """
    Recupera conocimiento web. Las filas se cachean por empresa; el ranking
    por consulta se hace en memoria. Así evitamos un GET a Supabase por mensaje.
    """
    import time as _time
    empresa_id = str(empresa_id or "").strip()
    cache_key = f"core_web_rows:{empresa_id}"
    rows = _cache_get(cache_key)

    if rows is None:
        t0 = _time.perf_counter()
        try:
            r=requests.get(
                f"{SUPABASE_URL}/rest/v1/nexi_core_conocimiento",
                headers=_core_headers(),
                params={
                    "select":"url,titulo,contenido,resumen",
                    "empresa_id":f"eq.{empresa_id}",
                    "activo":"eq.true",
                    "fuente":"eq.web",
                    "limit":"12",
                },
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()
            rows=r.json() if r.content else []
            _cache_set(cache_key, rows)
            print(f"NEXI PERF conocimiento_supabase={_time.perf_counter()-t0:.3f}s rows={len(rows)}")
        except Exception as e:
            print("CORE WEB KNOWLEDGE READ ERROR:",repr(e))
            return ""
    else:
        print(f"NEXI PERF conocimiento_cache=HIT rows={len(rows)}")

    if not rows:
        return ""

    palabras=[x for x in re.findall(r"[a-záéíóúñ0-9]{4,}",_core_norm(consulta)) if len(x)>=4]
    scored=[]
    for row in rows:
        text=" ".join(str(row.get(k) or "") for k in ("titulo","contenido","resumen"))
        norm=_core_norm(text)
        score=sum(norm.count(p) for p in palabras)
        scored.append((score,row))
    scored.sort(key=lambda x:x[0],reverse=True)
    elegidos=[r for s,r in scored[:3] if s>0] or [r for _,r in scored[:2]]
    piezas=[]
    for row in elegidos:
        contenido=str(row.get("contenido") or "")
        resumen=str(row.get("resumen") or "")
        piezas.append(f"Fuente: {row.get('url')}\n{resumen or contenido[:3500]}")
    return "\n\n".join(piezas)[:9000]


def _core_create_company_from_session(session):
    datos=dict(session.get("datos") or {})
    token=session["token"]
    if session.get("empresa_id"):
        return str(session["empresa_id"])

    tipo="empresa"
    empresa_nombre=(datos.get("nombre_negocio") or datos.get("nombre_contacto") or "Nexia").strip()
    now=datetime.now(pytz.UTC).isoformat()

    er=requests.post(
        f"{SUPABASE_URL}/rest/v1/empresas",
        headers=_core_headers("return=representation"),
        json={"nombre":f"{empresa_nombre}","activo":True},
        timeout=SUPABASE_TIMEOUT,
    )
    er.raise_for_status()
    empresa=(er.json() or [None])[0]
    if not empresa:
        raise RuntimeError("No se pudo crear la empresa para la prueba")
    empresa_id=str(empresa["id"])

    objetivos=str(datos.get("objetivo") or "")
    usa_reservas=_core_necesita_agenda(datos)
    descripcion=" | ".join(
        x for x in [
            str(datos.get("rubro") or ""),
            str(datos.get("productos_servicios") or ""),
        ] if x.strip()
    )

    modulos={
        "ia":True,
        "reservas":usa_reservas,
        "handoff_humano":_core_si(datos.get("handoff")),
        "whatsapp":"whatsapp" in _core_canales_texto(datos),
        "instagram":"instagram" in _core_canales_texto(datos),
        "web":True,
    }

    presencia=_core_presencia(datos)
    sitio_web=str(presencia.get("web") or "").strip()
    redes={k:v for k,v in presencia.items() if k != "web" and str(v or "").strip()}

    prompt_extra=(
        f"NEXI CORE V1.5. Negocio: {empresa_nombre}. Asistente: {datos.get('nombre_asistente','Nexi')}. "
        f"Rubro: {datos.get('rubro','')}. Objetivos: {objetivos}. "
        f"Productos/servicios declarados: {datos.get('productos_servicios','')}. "
        f"Canales actuales: {datos.get('canales_actuales','')}. "
        f"Canales deseados: {datos.get('canales_deseados','')}. "
        f"Personas que atienden clientes: {datos.get('personas_atencion','')}. "
        f"Sitio web público: {sitio_web}. Redes sociales públicas: {json.dumps(redes,ensure_ascii=False)}. "
        f"Tono: {datos.get('tono','')}. Si no sabe: {datos.get('desconocido','')}. " "Nunca muestres teléfonos privados, correos internos ni datos personales del ejecutivo."
    )

    agenda_apertura, agenda_cierre = _core_parse_business_hours(
        datos.get("agenda_horario"), DEFAULT_HORA_APERTURA, DEFAULT_HORA_CIERRE
    )
    agenda_duracion_min = _core_parse_duration_minutes(datos.get("agenda_duracion"), DEFAULT_DURACION_RESERVA)
    agenda_dias_cfg = _core_parse_days(datos.get("agenda_dias"))

    cfg_payload={
        "empresa_id":empresa_id,
        "tipo_negocio":"reservas" if usa_reservas else "comercial",
        "descripcion_empresa":descripcion,
        "asistente_nombre":str(datos.get("nombre_asistente") or "Nexi").strip(),
        "correo_ejecutivo":str(datos.get("email_contacto") or "").strip(),
        "timezone":TIMEZONE,
        "hora_apertura":agenda_apertura,
        "hora_cierre":agenda_cierre,
        "duracion_reserva":agenda_duracion_min,
        "dias_atencion":agenda_dias_cfg,
        "prompt_extra":prompt_extra,
        "modulos":modulos,
    }
    cr=requests.post(
        f"{SUPABASE_URL}/rest/v1/configuracion_bot",
        headers=_core_headers("return=representation"),
        json=cfg_payload,
        timeout=SUPABASE_TIMEOUT,
    )
    cr.raise_for_status()

    # Catálogo estructurado del onboarding: se convierte en servicios reales de la empresa.
    catalogo = datos.get("catalogo_productos_servicios") or []
    if isinstance(catalogo, list) and catalogo:
        servicios_payload = []
        for idx, item in enumerate(catalogo[:100], 1):
            if not isinstance(item, dict):
                continue
            nombre_servicio = str(item.get("nombre") or "").strip()
            if not nombre_servicio:
                continue
            precio_texto = str(item.get("precio") or "").strip()
            digitos = re.sub(r"\D", "", precio_texto)
            precio_num = int(digitos) if digitos else 0
            servicios_payload.append({
                "empresa_id": empresa_id,
                "codigo": f"onboarding_{idx}",
                "numero": idx,
                "nombre": nombre_servicio[:180],
                "categoria": "Productos y servicios",
                "precio": precio_num,
                "precio_texto": precio_texto or "Valor por confirmar",
                "detalle": "",
                "aliases": [],
                "duracion_minutos": agenda_duracion_min,
                "orden": idx,
                "activo": True,
            })
        if servicios_payload:
            sr = requests.post(
                f"{SUPABASE_URL}/rest/v1/servicios",
                headers=_core_headers("return=minimal"),
                json=servicios_payload,
                timeout=SUPABASE_TIMEOUT,
            )
            sr.raise_for_status()

    profile={
        "empresa_id":empresa_id,
        "onboarding_token":token,
        "version":CORE_ONBOARDING_VERSION,
        "tipo_cliente":"empresa",
        "datos":datos,
        "canales_actuales":str(datos.get("canales_actuales") or ""),
        "canales_deseados":str(datos.get("canales_deseados") or ""),
        "sitio_web":sitio_web,
        "redes_sociales":json.dumps(redes,ensure_ascii=False),
        "objetivo":objetivos,
        "created_at":now,
        "updated_at":now,
    }
    pr=requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",
        headers=_core_headers("return=representation"),
        json=profile,
        timeout=SUPABASE_TIMEOUT,
    )
    pr.raise_for_status()

    if sitio_web and _core_si(datos.get("aprende_web")):
        try:
            resultado_web=_core_guardar_conocimiento_web(empresa_id,sitio_web)
            datos["web_knowledge"] = resultado_web
            _core_save_session(token,{"datos":datos})
        except Exception as e:
            print("CORE WEB KNOWLEDGE CREATE ERROR:",repr(e))
            datos["web_knowledge"] = {"ok":False,"error":str(e)[:250]}
            try:
                _core_save_session(token,{"datos":datos})
            except Exception as save_error:
                # El aprendizaje web no debe bloquear la creación de la demo.
                print("CORE WEB KNOWLEDGE STATUS SAVE ERROR:",repr(save_error))

    whatsapp_demo=str(datos.get("whatsapp_demo") or "").strip()
    activar_demo_empresa(empresa_id, whatsapp_demo if whatsapp_demo else None, "whatsapp")

    # V3.4.8: la demo también recibe usuario/clave real de Portal.
    # Usuario = email de onboarding. Clave = texto antes de @.
    email_portal = str(datos.get("email_contacto") or "").strip().lower()
    nombre_portal = str(datos.get("nombre_contacto") or empresa_nombre or "Cliente Nexia").strip()
    if email_portal and "@" in email_portal:
        try:
            acceso_portal = _portal_crear_o_actualizar_acceso(
                empresa_id, email_portal, nombre_portal
            )
            datos["portal_acceso"] = {
                "email": acceso_portal["email"],
                "password": acceso_portal["password"],
                "auto": True,
            }
        except Exception as e:
            # No bloquear la creación de la demo si Auth presenta un error puntual.
            print("PORTAL DEMO AUTO ACCESS ERROR:", repr(e))

    _core_save_session(token,{"empresa_id":empresa_id,"estado":"demo_activa","completado":True,"datos":datos})
    return empresa_id


def _core_profile(empresa_id):
    r=requests.get(f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",headers=_core_headers(),params={"select":"*","empresa_id":f"eq.{empresa_id}","limit":"1"},timeout=SUPABASE_TIMEOUT)
    r.raise_for_status(); rows=r.json() if r.content else []
    return rows[0] if rows else None


def _core_token_por_empresa(empresa_id):
    """Obtiene el token de onboarding asociado a la empresa demo."""
    try:
        r=requests.get(
            f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",
            headers=_core_headers(),
            params={"select":"onboarding_token","empresa_id":f"eq.{empresa_id}","limit":"1"},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status(); rows=r.json() if r.content else []
        return str(rows[0].get("onboarding_token") or "").strip() if rows else ""
    except Exception as e:
        print("NEXI CORE TOKEN LOOKUP ERROR:",repr(e)); return ""


def _core_whatsapp_destino():
    """Número compartido al que el participante debe escribir para probar la demo."""
    raw=str(TWILIO_WHATSAPP_FROM or GUPSHUP_SOURCE or "").strip()
    return re.sub(r"\D","",raw)


def _core_whatsapp_info(empresa_id=None):
    """
    Acceso directo a la prueba por WhatsApp.
    No depende de haber preguntado el WhatsApp del creador en onboarding.
    Usa un código de Router ligado al mismo empresa_id para continuar exactamente
    la misma demo/configuración.
    """
    destino = _core_whatsapp_destino()
    empresa_id = str(empresa_id or "").strip()

    if not destino or not empresa_id:
        return {
            "enabled": False,
            "destination": (f"+{destino}" if destino else None),
            "wa_url": None,
            "empresa_id": empresa_id or None,
            "codigo_acceso": None,
        }

    codigo = None
    try:
        codigo, _ = _portal_destino_asistente(empresa_id)
    except Exception as e:
        print("CORE WHATSAPP DIRECT ACCESS WARN:", repr(e))

    if not codigo:
        # Fallback estable; si la tabla de destinos aún no está disponible,
        # el botón se oculta para no enviar al usuario a una ruta incorrecta.
        return {
            "enabled": False,
            "destination": f"+{destino}",
            "wa_url": None,
            "empresa_id": empresa_id,
            "codigo_acceso": None,
        }

    texto = f"NEXI {codigo}"
    return {
        "enabled": True,
        "destination": f"+{destino}",
        "wa_url": f"https://wa.me/{destino}?text={quote(texto)}",
        "empresa_id": empresa_id,
        "codigo_acceso": codigo,
        "mensaje_acceso": texto,
    }



def proteger_respuesta_publica_core(texto):
    """
    Filtro FINAL obligatorio para cualquier respuesta de demo.
    Los datos privados de configuración jamás se publican.
    """
    original = str(texto or "")
    if not original:
        return original

    salida = original

    # Exactos privados: se eliminan sin depender del contexto.
    internos = {
        str(cfg("telefono_ejecutivo", "") or "").strip(),
        str(cfg("correo_ejecutivo", "") or "").strip(),
        str(DEFAULT_TELEFONO_EJECUTIVO or "").strip(),
        str(EJECUTIVO_EMAIL or "").strip(),
    }
    perfil = None
    try:
        perfil = _core_profile(empresa_actual_id()) or {}
    except Exception:
        perfil = {}
    pd = (perfil or {}).get("datos") or {}
    internos.add(str(pd.get("email_contacto") or "").strip())
    internos.add(str(pd.get("whatsapp_demo") or "").strip())

    for valor in internos:
        if valor:
            salida = re.sub(re.escape(valor), "[dato privado]", salida, flags=re.IGNORECASE)

    patron_tel = re.compile(r"(?<!\d)(?:\+?56[ .-]*)?9(?:[ .-]*\d){8}(?!\d)")
    patron_email = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")

    lineas = []
    for linea in salida.splitlines():
        normal = normalizar_texto(linea)
        contexto_contacto = any(k in normal for k in (
            "correo", "email", "e-mail", "telefono", "teléfono", "whatsapp",
            "contacto", "contactar", "escribeme", "escríbeme", "llama", "llamar",
            "puedes contact", "por correo", "por whatsapp",
        ))
        if contexto_contacto:
            linea = patron_tel.sub("[dato privado]", linea)
            linea = patron_email.sub("[dato privado]", linea)
        lineas.append(linea)

    return "\n".join(lineas).strip()



def _core_respuesta_no_verificable(texto):
    """
    Detecta preguntas sobre el estado actual de una PERSONA cuando Nexia no
    dispone de una fuente en tiempo real.

    V3.4.6: nunca intercepta consultas de ecommerce. Frases como
    "dónde está mi pedido" o "cómo va la orden 7441" sí tienen una fuente
    potencial en la tienda conectada y deben llegar al agente Ecommerce.
    """
    t = _core_norm(texto)

    # Exclusión explícita de objetos/estados consultables por integraciones.
    if re.search(r"\b(pedido|pedidos|orden|ordenes|órdenes|tracking)\b", t):
        return False
    if any(x in t for x in (
        "estado del pedido", "seguimiento de pedido",
        "despacho de mi", "envio de mi", "envío de mi",
        "donde esta mi compra", "dónde está mi compra",
    )):
        return False

    patrones = (
        "como esta", "cómo está", "como se encuentra", "cómo se encuentra",
        "donde esta", "dónde está", "que esta haciendo", "qué está haciendo",
        "esta bien", "está bien", "como sigue", "cómo sigue",
    )
    return any(p in t for p in patrones)




# ============================================================
# NEXI V1.6 - ORQUESTADOR CENTRAL + AGENTES ESPECIALIZADOS
# ============================================================

CORE_AGENT_NAMES = {
    "atencion": "Atención",
    "conocimiento": "Conocimiento",
    "ventas": "Ventas",
    "agenda": "Agenda",
    "soporte": "Soporte",
    "seguimiento": "Seguimiento",
    "ecommerce": "Ecommerce",
}


def _core_public_profile(empresa_id):
    """Perfil seguro para agentes, cacheado por empresa para reducir viajes a Supabase."""
    empresa_id = str(empresa_id or "").strip()
    cache_key = f"core_public_profile:{empresa_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached.get("perfil") or {}, dict(cached.get("datos") or {})

    perfil = _core_profile(empresa_id) or {}
    datos = dict(perfil.get("datos") or {})

    # Expone como campos públicos simples la presencia digital guardada en onboarding.
    presencia = _core_presencia(datos)
    if presencia:
        if str(presencia.get("web") or "").strip():
            datos["web"] = str(presencia.get("web") or "").strip()
            datos["sitio_web"] = datos["web"]
        for red in ("instagram", "facebook", "tiktok"):
            if str(presencia.get(red) or "").strip():
                datos[red] = str(presencia.get(red) or "").strip()

    for privado in (
        "nombre_contacto",
        "email_contacto",
        "whatsapp_demo",
        "correo_ejecutivo",
        "telefono_ejecutivo",
        "web_knowledge",
    ):
        datos.pop(privado, None)

    _cache_set(cache_key, {"perfil": perfil, "datos": datos})
    return perfil, dict(datos)


def _core_agent_enabled(agent, datos):
    objetivo = _core_norm(datos.get("objetivo"))
    if agent == "agenda":
        return _core_necesita_agenda(datos)
    if agent == "ventas":
        return any(x in objetivo for x in (
            "vender", "venta", "precio", "informar precios",
            "personas interesadas", "seguimiento",
        )) or True
    if agent == "seguimiento":
        return "seguimiento" in objetivo
    return True


def _core_intencion_info_negocio(texto):
    """
    Detecta intención general de conocer el negocio sin depender de frases exactas.
    Ejemplos cubiertos:
    - quiero saber de ustedes
    - quisiera conocer más de la empresa
    - necesito información del negocio
    - me cuentas sobre ustedes
    - qué hacen / a qué se dedican
    - quiénes son / qué es esta empresa
    """
    t = _core_norm(texto)
    if not t:
        return False

    # Si la consulta tiene una intención especializada clara, no la convertimos
    # en "información general". El router especializado conserva prioridad.
    especializadas = (
        "precio", "precios", "cuanto cuesta", "cuánto cuesta", "cotizar",
        "cotizacion", "cotización", "comprar", "contratar",
        "agendar", "reservar", "reserva", "cita", "turno",
        "problema", "error", "falla", "soporte", "reclamo",
        "seguimiento", "estado de mi",
    )
    if any(x in t for x in especializadas):
        return False

    patrones = (
        # "quiero saber de ustedes", "quisiera conocer la empresa"
        r"\b(quiero|quisiera|necesito|deseo|me gustaria|me gustaría|podria|podría)\b.{0,35}\b(saber|conocer|informacion|información|info|datos|detalles)\b.{0,35}\b(de|del|sobre|acerca de)?\s*(ustedes|empresa|negocio|marca)\b",

        # "quiero saber más", "quiero conocer más de ustedes"
        r"\b(quiero|quisiera|necesito|me gustaria|me gustaría)\b.{0,25}\b(saber|conocer)\b.{0,20}\b(mas|más)\b(?:.{0,25}\b(ustedes|empresa|negocio|marca)\b)?",

        # "dame información de la empresa", "cuéntame sobre ustedes"
        r"\b(dame|entregame|entrégame|cuentame|cuéntame|explicame|explícame)\b.{0,30}\b(informacion|información|info|sobre|acerca|ustedes|empresa|negocio|marca)\b",

        # "información de ustedes / del negocio / sobre la empresa"
        r"\b(informacion|información|info|datos|detalles)\b.{0,20}\b(de|del|sobre|acerca de)\b.{0,15}\b(ustedes|empresa|negocio|marca)\b",

        # "qué hacen", "a qué se dedican", "quiénes son"
        r"\b(que|qué)\b.{0,8}\b(hacen|son|ofrecen)\b",
        r"\b(a que|a qué)\b.{0,12}\b(se dedican|dedican)\b",
        r"\b(quienes|quiénes)\b.{0,8}\b(son)\b",

        # "qué es esta empresa / este negocio"
        r"\b(que|qué)\b.{0,8}\b(es)\b.{0,12}\b(esta|este|la|el)?\s*(empresa|negocio|marca)\b",

        # "sobre ustedes", "acerca del negocio" como consulta corta
        r"^\s*(quiero\s+)?(saber\s+)?(mas\s+|más\s+)?(sobre|acerca de)\s+(ustedes|la empresa|el negocio|la marca)\s*[?.!]*$",

        # "cómo trabajan", "cómo funciona la empresa/el servicio/la plataforma"
        r"\b(como|cómo)\b.{0,15}\b(trabajan|trabaja|funciona|funcionan|operan|opera)\b(?:.{0,25}\b(ustedes|empresa|negocio|servicio|plataforma|solucion|solución)\b)?",
    )
    return any(re.search(p, t, flags=re.IGNORECASE) for p in patrones)


def _core_route_intent(texto, datos):
    """
    Router barato y determinista.
    No consume una llamada adicional a OpenAI.
    """
    t = _core_norm(texto)

    if _core_es_handoff(texto):
        return "handoff"

    if _ecommerce_intent(texto):
        return "ecommerce"

    if _texto_parece_intencion_agenda(texto):
        # La intención es agenda aunque el negocio aún no haya conectado Calendar.
        # La capa de ejecución decide si agenda realmente o informa que estará disponible pronto.
        return "agenda"

    if any(x in t for x in (
        "precio", "precios", "cuanto cuesta", "cuánto cuesta", "cuanto cobran",
        "cuánto cobran", "cotizar", "cotizacion", "cotización", "comprar",
        "contratar", "plan", "planes", "valor", "valores",
    )):
        return "ventas"

    if any(x in t for x in (
        "problema", "error", "falla", "no funciona", "ayuda tecnica",
        "ayuda técnica", "soporte", "reclamo", "incidente",
    )):
        return "soporte"

    if any(x in t for x in (
        "seguimiento", "estado de mi", "como va mi", "cómo va mi",
        "mi solicitud", "mi pedido", "mi caso",
    )):
        return "seguimiento" if _core_agent_enabled("seguimiento", datos) else "atencion"

    if _core_intencion_info_negocio(texto):
        return "conocimiento"

    if any(x in t for x in (
        "que hacen", "qué hacen", "que ofrece", "qué ofrece", "que ofrecen", "qué ofrecen",
        "informacion de ustedes", "información de ustedes",
        "informacion del negocio", "información del negocio",
        "informacion de la empresa", "información de la empresa",
        "quiero informacion", "quiero información",
        "necesito informacion", "necesito información",
        "cuentame de ustedes", "cuéntame de ustedes",
        "sobre ustedes", "sobre la empresa", "sobre el negocio",
        "a que se dedican", "a qué se dedican",
        "servicios", "productos", "horario", "horarios", "direccion", "dirección",
        "donde estan", "dónde están", "web", "instagram", "facebook", "redes",
        "contacto", "politica", "política", "envio", "envío", "despacho",
    )):
        return "conocimiento"

    return "atencion"


def _core_agent_context(empresa_id, texto, public_profile=None):
    """
    Construye contexto reutilizando el perfil que ya cargó el orquestador.
    Evita consultar el mismo perfil dos veces en un turno.
    """
    import time as _time
    t0 = _time.perf_counter()
    if public_profile is None:
        perfil, datos = _core_public_profile(empresa_id)
    else:
        perfil, datos = public_profile

    empresa = cfg("empresa_nombre", "Nexia")
    asistente = cfg("asistente_nombre", "Nexi")

    tk = _time.perf_counter()
    conocimiento_web = _core_conocimiento_web(empresa_id, texto)
    print(
        f"NEXI PERF contexto_total={_time.perf_counter()-t0:.3f}s "
        f"conocimiento={_time.perf_counter()-tk:.3f}s"
    )
    return {
        "perfil": perfil,
        "datos": datos,
        "empresa": empresa,
        "asistente": asistente,
        "conocimiento_web": conocimiento_web,
    }


def _core_valor_publico(datos, *keys):
    """Primer valor público no vacío entre varias claves, ignorando textos de preguntas."""
    for key in keys:
        valor = datos.get(key)
        if isinstance(valor, (list, tuple)):
            valor = ", ".join(str(x).strip() for x in valor if str(x).strip())
        elif isinstance(valor, dict):
            continue
        valor = str(valor or "").strip()
        if not valor:
            continue

        # Evita exponer como respuesta una pregunta/placeholder del propio onboarding.
        pregunta = (CORE_QUESTIONS.get(key) or {}).get("text") if "CORE_QUESTIONS" in globals() else None
        placeholder = (CORE_QUESTIONS.get(key) or {}).get("placeholder") if "CORE_QUESTIONS" in globals() else None
        if pregunta and _core_norm(valor) == _core_norm(pregunta):
            continue
        if placeholder and _core_norm(valor) == _core_norm(placeholder):
            continue

        return valor
    return ""


def _core_respuesta_estructurada(texto, datos, empresa=None, asistente=None):
    """
    Capa rápida y determinista para consultas frecuentes.
    No llama a OpenAI ni consulta conocimiento web.
    Retorna None solo cuando la consulta realmente necesita razonamiento/redacción.
    """
    t = _core_norm(texto)
    datos = dict(datos or {})
    empresa = str(
        empresa
        or datos.get("nombre_negocio")
        or datos.get("empresa_nombre")
        or cfg("empresa_nombre", "este negocio")
        or "este negocio"
    ).strip()
    asistente = str(
        asistente
        or datos.get("nombre_asistente")
        or datos.get("asistente_nombre")
        or cfg("asistente_nombre", "asistente virtual")
        or "asistente virtual"
    ).strip()

    # Saludos simples: jamás necesitan IA.
    if re.fullmatch(r"\s*(hola+|holi+|buenas|buenos dias|buenos días|buenas tardes|buenas noches|hey|alo|aló)\s*[!.?]*\s*", t):
        return f"¡Hola! 👋 Soy {asistente}, el asistente virtual de {empresa}. ¿En qué te puedo ayudar?"

    rubro = _core_valor_publico(datos, "rubro", "tipo_negocio")
    descripcion = _core_valor_publico(
        datos, "descripcion_empresa", "descripcion", "proposito", "propósito"
    )
    oferta = _core_valor_publico(
        datos, "productos_servicios", "servicios_productos", "servicios", "productos"
    )
    objetivo = _core_valor_publico(datos, "objetivo")
    web = _core_valor_publico(datos, "web", "sitio_web", "website")
    instagram = _core_valor_publico(datos, "instagram")
    facebook = _core_valor_publico(datos, "facebook")
    tiktok = _core_valor_publico(datos, "tiktok")
    direccion = _core_valor_publico(datos, "direccion", "direccion_completa")
    google_maps_url = _core_valor_publico(datos, "google_maps_url")
    atiende_direccion = bool(datos.get("atiende_direccion_fisica"))

    pregunta_ubicacion = any(x in t for x in (
        "direccion", "dirección", "donde estan", "dónde están", "donde quedan",
        "dónde quedan", "ubicacion", "ubicación", "como llego", "cómo llego",
        "local", "tienda fisica", "tienda física", "atienden presencial",
        "atencion presencial", "atención presencial",
    ))
    if pregunta_ubicacion:
        if atiende_direccion and direccion:
            respuesta = f"Sí 😊 Atendemos presencialmente en {direccion}."
            referencia = _core_valor_publico(datos, "referencia_direccion")
            if referencia:
                respuesta += f" Referencia: {referencia}."
            if google_maps_url:
                respuesta += f"\n\n📍 Google Maps: {google_maps_url}"
            return respuesta
        return (
            "Este negocio no tiene una dirección física de atención publicada. "
            "La atención se realiza a través de sus canales disponibles."
        )

    def _texto_descriptivo(valor):
        valor = str(valor or "").strip()
        return bool(valor and (len(valor) >= 90 or ". " in valor or valor.count(",") >= 2))

    def _cerrar_frase(valor):
        valor = str(valor or "").strip()
        if not valor:
            return ""
        return valor if valor.endswith((".", "!", "?")) else valor + "."

    pregunta_precio = any(x in t for x in (
        "precio", "precios", "cuanto cuesta", "cuánto cuesta", "cuanto cobran",
        "cuánto cobran", "valor", "valores", "plan", "planes", "tarifa", "tarifas",
    ))
    pregunta_compra = any(x in t for x in (
        "quiero comprar", "quiero contratar", "me interesa contratar",
        "me interesa comprar", "como contrato", "cómo contrato",
        "como compro", "cómo compro", "quiero un bot", "necesito un bot",
        "adquirir", "contratar el servicio", "comprar el servicio",
    ))
    if pregunta_precio:
        # Nexia tiene sus planes públicos definidos en el propio backend.
        if es_empresa_nexia():
            p500 = NEXIA_PLANES.get("nexia_500") or {}
            p1000 = NEXIA_PLANES.get("nexia_1000") or {}
            if p500.get("precio") and p1000.get("precio"):
                return (
                    f"Tenemos dos planes: Nexia 500 por ${int(p500['precio']):,} CLP "
                    f"y Nexia 1000 por ${int(p1000['precio']):,} CLP. "
                    "Cada uno incluye la cantidad de mensajes indicada en el plan."
                ).replace(",", ".")
        precio_publico = _core_valor_publico(datos, "precios", "precio", "valores", "tarifas", "planes")
        if precio_publico:
            return f"Estos son los valores disponibles: {precio_publico}"

        # El onboarding guarda servicios/precios dentro de productos_servicios
        # y también puede conservar el catálogo estructurado.
        catalogo = datos.get("catalogo_productos_servicios")
        if isinstance(catalogo, list):
            items_con_precio = []
            for item in catalogo:
                if not isinstance(item, dict):
                    continue
                nombre = str(item.get("nombre") or "").strip()
                precio = str(item.get("precio") or "").strip()
                if nombre and precio:
                    items_con_precio.append(f"{nombre}: {precio}")
            if items_con_precio:
                return "Estos son los valores disponibles: " + "; ".join(items_con_precio)

        # Compatibilidad con perfiles donde productos_servicios ya viene
        # como texto del tipo "Servicio — 5.990".
        if oferta and re.search(r"(\$\s*\d|\b\d{1,3}(?:[\.\,]\d{3})+\b|\b\d+\s*(?:clp|usd|uf)\b)", oferta, flags=re.IGNORECASE):
            return f"Estos son los valores disponibles: {oferta}"

        return (
            "Los valores no están publicados en la información disponible. "
            "Puedo contarte qué servicios ofrecemos o ayudarte a solicitar una cotización."
        )

    if pregunta_compra:
        if es_empresa_nexia():
            p500 = NEXIA_PLANES.get("nexia_500") or {}
            p1000 = NEXIA_PLANES.get("nexia_1000") or {}
            if p500.get("precio") and p1000.get("precio"):
                return (
                    "¡Claro! Puedes contratar Nexia directamente. "
                    f"Tenemos Nexia 500 por ${int(p500['precio']):,} CLP y "
                    f"Nexia 1000 por ${int(p1000['precio']):,} CLP. "
                    "Si quieres, te ayudo a elegir el plan que mejor se ajusta a tu negocio."
                ).replace(",", ".")
        if oferta:
            return (
                f"Claro. Ofrecemos {_cerrar_frase(oferta)} "
                "Si te interesa contratar, puedo orientarte con el siguiente paso."
            )
        return (
            "Claro. Puedo ayudarte a conocer el servicio y orientarte para contratar. "
            "Cuéntame brevemente qué necesitas."
        )

    pregunta_general = _core_intencion_info_negocio(texto)
    if pregunta_general:
        partes = []
        if descripcion:
            partes.append(_cerrar_frase(descripcion))
        elif rubro:
            if _texto_descriptivo(rubro):
                partes.append(_cerrar_frase(rubro))
            else:
                partes.append(f"{empresa} se dedica a {rubro}.")
        if oferta:
            partes.append(f"Ofrecemos {_cerrar_frase(oferta)}")
        elif objetivo and not partes:
            partes.append(_cerrar_frase(objetivo))
        if partes:
            return " ".join(partes).strip() + " ¿Hay algo en particular que quieras conocer?"
        return (
            f"Puedo ayudarte con información sobre {empresa}, sus servicios y cómo funciona. "
            "¿Qué te gustaría saber?"
        )

    pregunta_oferta = any(x in t for x in (
        "que ofrecen", "qué ofrecen", "que ofrece", "qué ofrece",
        "servicios", "productos", "que venden", "qué venden",
    ))
    if pregunta_oferta:
        if oferta:
            return f"Ofrecemos {_cerrar_frase(oferta)} Si quieres, te doy más información sobre alguno en particular."
        if descripcion:
            return _cerrar_frase(descripcion) + " Si quieres, te explico alguno de nuestros servicios en particular."
        if rubro and _texto_descriptivo(rubro):
            return _cerrar_frase(rubro) + " Si quieres, te explico alguno de nuestros servicios en particular."
        return "Todavía no hay un catálogo de productos o servicios publicado para este negocio."

    # Datos públicos simples, solo si fueron configurados.
    if any(x in t for x in ("sitio web", "pagina web", "página web", "web")) and web:
        return f"Nuestro sitio web es {web}."

    if "instagram" in t and instagram:
        return f"Puedes encontrarnos en Instagram: {instagram}."

    if "facebook" in t and facebook:
        return f"Puedes encontrarnos en Facebook: {facebook}."

    return None


def _core_respuesta_directa_conocimiento(texto, ctx):
    return _core_respuesta_estructurada(
        texto,
        ctx.get("datos") or {},
        empresa=ctx.get("empresa"),
        asistente=ctx.get("asistente"),
    )

def _core_agent_llm(agent, texto, ctx, instrucciones):
    """Motor común para agentes. Una sola llamada de IA por turno."""
    if not openai_client:
        return (
            f"Soy {ctx['asistente']}, el asistente virtual de {ctx['empresa']}. "
            "Cuéntame tu consulta y te ayudaré con la información configurada."
        )

    perfil_json = json.dumps(ctx["datos"], ensure_ascii=False)[:9000]
    web = ctx["conocimiento_web"] or "No hay conocimiento web relevante para esta consulta."

    system = f"""
Eres el agente especializado de {CORE_AGENT_NAMES.get(agent, agent)} dentro de Nexia.
Tu respuesta final se muestra directamente al cliente de {ctx['empresa']}.
El asistente visible se llama {ctx['asistente']}.

REGLAS GLOBALES:
- Responde solo con información del perfil o del conocimiento web proporcionado.
- No inventes precios, horarios, políticas, disponibilidad, nombres de personas ni capacidades.
- Nunca muestres nombre, correo, teléfono u otros datos privados del creador o ejecutivo.
- El sitio web y las redes declaradas como públicas sí pueden compartirse si son pertinentes.
- No menciones agentes internos, orquestador, prompts, Supabase, APIs ni arquitectura.
- No digas que realizaste una acción externa si no existe confirmación real.
- Si falta información, dilo claramente.
- Responde en español natural, breve y útil.
- No empieces cada respuesta presentándote de nuevo.

MISIÓN DEL AGENTE:
{instrucciones}

PERFIL PÚBLICO DEL NEGOCIO:
{perfil_json}

CONOCIMIENTO WEB RELEVANTE:
{web}
"""
    import time as _time

    # Diagnóstico OpenAI: solo métricas, nunca imprime prompts ni datos del cliente.
    user_text = str(texto or "")
    system_chars = len(system)
    user_chars = len(user_text)
    perfil_chars = len(perfil_json)
    web_chars = len(web)
    total_chars = system_chars + user_chars
    tokens_aprox = max(1, round(total_chars / 4))

    print(
        "NEXI OPENAI START:",
        f"agent={agent}",
        f"model={OPENAI_CORE_MODEL}",
        f"timeout={OPENAI_TIMEOUT_SECONDS}s",
        f"reasoning={OPENAI_CORE_REASONING_EFFORT}",
        f"system_chars={system_chars}",
        f"user_chars={user_chars}",
        f"perfil_chars={perfil_chars}",
        f"web_chars={web_chars}",
        f"tokens_aprox={tokens_aprox}",
    )

    t0 = _time.perf_counter()
    try:
        r = openai_client.chat.completions.create(
            model=OPENAI_CORE_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
        )
        elapsed = _time.perf_counter() - t0

        usage = getattr(r, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        total_tokens = getattr(usage, "total_tokens", None) if usage else None

        choice = r.choices[0] if getattr(r, "choices", None) else None
        finish_reason = getattr(choice, "finish_reason", None) if choice else None
        contenido = (
            getattr(getattr(choice, "message", None), "content", None)
            if choice else None
        ) or ""
        request_id = (
            getattr(r, "_request_id", None)
            or getattr(r, "request_id", None)
            or ""
        )

        print(
            "NEXI OPENAI OK:",
            f"agent={agent}",
            f"model={OPENAI_CORE_MODEL}",
            f"tiempo={elapsed:.3f}s",
            f"prompt_tokens={prompt_tokens}",
            f"completion_tokens={completion_tokens}",
            f"total_tokens={total_tokens}",
            f"output_chars={len(contenido)}",
            f"finish_reason={finish_reason}",
            f"request_id={request_id or 'n/a'}",
        )
        print(f"NEXI PERF openai agent={agent} tiempo={elapsed:.3f}s")
        return contenido.strip() or "No tengo suficiente información para responder eso."

    except Exception as e:
        elapsed = _time.perf_counter() - t0
        error_type = type(e).__name__
        causa = getattr(e, "__cause__", None)
        causa_type = type(causa).__name__ if causa else None

        print(
            "NEXI OPENAI ERROR:",
            f"agent={agent}",
            f"model={OPENAI_CORE_MODEL}",
            f"tiempo={elapsed:.3f}s",
            f"timeout_config={OPENAI_TIMEOUT_SECONDS}s",
            f"reasoning={OPENAI_CORE_REASONING_EFFORT}",
            f"error_type={error_type}",
            f"cause_type={causa_type or 'n/a'}",
            f"system_chars={system_chars}",
            f"user_chars={user_chars}",
            f"perfil_chars={perfil_chars}",
            f"web_chars={web_chars}",
            f"tokens_aprox={tokens_aprox}",
        )
        print(f"NEXI PERF openai_error agent={agent} tiempo={elapsed:.3f}s")
        print("NEXI AGENT ERROR:", agent, repr(e))
        return "No pude procesar esa consulta en este momento. Intenta nuevamente."


def _core_agent_atencion(empresa_id, texto, ctx=None):
    ctx = ctx or _core_agent_context(empresa_id, texto)
    return _core_agent_llm(
        "atencion",
        texto,
        ctx,
        (
            "Atiende saludos, consultas generales y orientación inicial. "
            "Sé cordial y directo. No enumeres todos los servicios salvo que te los pidan. "
            "Si la intención corresponde claramente a otra capacidad, responde solo con la "
            "información necesaria y evita prometer acciones que no ejecutaste."
        ),
    )


def _core_agent_conocimiento(empresa_id, texto, ctx=None):
    ctx = ctx or _core_agent_context(empresa_id, texto)

    directa = _core_respuesta_directa_conocimiento(texto, ctx)
    if directa:
        print("NEXI PERF fast_path=conocimiento")
        return directa

    return _core_agent_llm(
        "conocimiento",
        texto,
        ctx,
        (
            "Responde preguntas sobre el negocio, productos, servicios, horarios, políticas, "
            "ubicaciones, web y redes usando principalmente la fuente web y el perfil. "
            "Cuando una respuesta no esté en las fuentes, indícalo sin completar con suposiciones."
        ),
    )


def _core_agent_ventas(empresa_id, texto, ctx=None):
    ctx = ctx or _core_agent_context(empresa_id, texto)
    return _core_agent_llm(
        "ventas",
        texto,
        ctx,
        (
            "Ayuda a una persona interesada a entender productos, servicios y precios disponibles. "
            "No inventes valores. Si no existe precio publicado, dilo de forma simple y, si la "
            "configuración permite derivación humana, ofrece derivar para cotización. "
            "No solicites datos innecesarios antes de que el usuario acepte la derivación."
        ),
    )



def _core_parse_duration_minutes(valor, default=60):
    """Convierte textos del onboarding (ej. '45 min', '1 hora', '1.5 horas') a minutos."""
    t = _core_norm(valor)
    if not t:
        return int(default)
    m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(hora|horas|hr|hrs|h)\b", t)
    if m:
        try:
            return max(15, min(480, int(float(m.group(1).replace(',', '.')) * 60)))
        except Exception:
            pass
    m = re.search(r"(\d+)\s*(min|minuto|minutos)\b", t)
    if m:
        return max(15, min(480, int(m.group(1))))
    m = re.search(r"\b(\d{1,3})\b", t)
    if m:
        n = int(m.group(1))
        # En onboarding, valores pequeños suelen significar horas.
        if n <= 8 and "min" not in t:
            n *= 60
        return max(15, min(480, n))
    return int(default)


def _core_parse_business_hours(valor, default_open=9, default_close=18):
    t = _core_norm(valor)
    horas = [int(x) for x in re.findall(r"(?<!\d)([0-2]?\d)(?::[0-5]\d)?(?!\d)", t)]
    horas = [h for h in horas if 0 <= h <= 23]
    if len(horas) >= 2:
        apertura, cierre = horas[0], horas[1]
        if cierre > apertura:
            return apertura, cierre
    return int(default_open), int(default_close)


def _core_parse_days(valor):
    t = _core_norm(valor)
    if not t:
        return [0, 1, 2, 3, 4, 5]
    names = {"lunes":0,"martes":1,"miercoles":2,"jueves":3,"viernes":4,"sabado":5,"domingo":6}
    # Rangos habituales.
    if "lunes a viernes" in t or "lunes-viernes" in t:
        return [0,1,2,3,4]
    if "lunes a sabado" in t or "lunes-sabado" in t:
        return [0,1,2,3,4,5]
    if "lunes a domingo" in t or "lunes-domingo" in t or "todos los dias" in t:
        return [0,1,2,3,4,5,6]
    out = [idx for name, idx in names.items() if name in t]
    return sorted(set(out)) or [0,1,2,3,4,5]


def _core_agenda_catalog(datos):
    """Crea un catálogo seguro para Core sin caer jamás en SERVICIOS_DEFAULT de Diego."""
    raw = str(datos.get("agenda_que") or datos.get("productos_servicios") or "").strip()
    if not raw:
        items = ["Reserva"]
    else:
        # Separar solo delimitadores claros; no destrozar descripciones completas.
        items = [x.strip(" -•\t") for x in re.split(r"[\n;|]+", raw) if x.strip(" -•\t")]
        if len(items) == 1 and raw.count(",") <= 5:
            comma = [x.strip() for x in raw.split(",") if x.strip()]
            if 1 < len(comma) <= 6:
                items = comma
        items = items[:8] or ["Reserva"]
    catalog = {}
    for i, name in enumerate(items, 1):
        code = f"core_servicio_{i}"
        catalog[code] = {
            "numero": i,
            "nombre": name[:120],
            "precio": 0,
            "precio_texto": "Valor por confirmar",
            "detalle": "",
            "categoria": "Servicios",
            "aliases": [name[:120]],
            "duracion_minutos": None,
        }
    return catalog


def _core_prepare_calendar_runtime(datos):
    """Adapta la configuración de agenda del onboarding al mismo motor usado por Diego."""
    actual = dict(tenant_actual())
    apertura, cierre = _core_parse_business_hours(
        datos.get("agenda_horario"),
        actual.get("hora_apertura") or DEFAULT_HORA_APERTURA,
        actual.get("hora_cierre") or DEFAULT_HORA_CIERRE,
    )
    actual["hora_apertura"] = apertura
    actual["hora_cierre"] = cierre
    actual["duracion_reserva"] = _core_parse_duration_minutes(
        datos.get("agenda_duracion"),
        actual.get("duracion_reserva") or DEFAULT_DURACION_RESERVA,
    )
    actual["dias_atencion"] = _core_parse_days(datos.get("agenda_dias"))
    # CRÍTICO: si Core no tiene servicios estructurados propios, crear un catálogo
    # desde el onboarding para impedir cualquier fallback a los servicios de Diego.
    if not actual.get("servicios"):
        actual["servicios"] = _core_agenda_catalog(datos)
    # Las empresas Core no heredan la dirección privada/default de Diego.
    actual["direccion"] = str(datos.get("direccion") or "").strip()
    set_tenant(actual)
    return actual


def _core_procesar_agenda_estandar(empresa_id, telefono, texto, datos):
    """Usa el mismo motor conversacional y de disponibilidad de Diego, aislado por empresa_id."""
    _core_prepare_calendar_runtime(datos)
    session_key = f"core:{empresa_id}:{_normalizar_identificador_demo(telefono, 'whatsapp')}"
    estado = get_estado(session_key)
    estado["telefono"] = telefono
    estado["_session_key"] = session_key
    return procesar_agenda(estado, texto)


def _core_agent_agenda(empresa_id, texto, ctx=None):
    ctx = ctx or _core_agent_context(empresa_id, texto)
    datos = ctx["datos"]
    agenda = {
        "que": datos.get("agenda_que"),
        "duracion": datos.get("agenda_duracion"),
        "dias": datos.get("agenda_dias"),
        "horario": datos.get("agenda_horario"),
        "buffer": datos.get("agenda_buffer"),
    }
    agenda_txt = json.dumps(agenda, ensure_ascii=False)
    return _core_agent_llm(
        "agenda",
        texto,
        ctx,
        (
            "Orienta sobre agenda o reservas usando únicamente la configuración disponible. "
            f"Configuración declarada de agenda: {agenda_txt}. "
            "Durante la prueba no afirmes que una reserva quedó creada ni que una hora está disponible "
            "si no existe una confirmación real de calendario. Puedes pedir la fecha/hora deseada "
            "como intención de reserva y explicar las condiciones conocidas."
        ),
    )


def _core_agent_soporte(empresa_id, texto, ctx=None):
    ctx = ctx or _core_agent_context(empresa_id, texto)
    return _core_agent_llm(
        "soporte",
        texto,
        ctx,
        (
            "Ayuda con problemas o dudas de soporte usando solo procedimientos publicados o "
            "configurados. Si no existe solución documentada, dilo y ofrece derivación humana "
            "solo cuando esté habilitada."
        ),
    )


def _core_agent_seguimiento(empresa_id, texto, ctx=None):
    ctx = ctx or _core_agent_context(empresa_id, texto)
    return _core_agent_llm(
        "seguimiento",
        texto,
        ctx,
        (
            "Gestiona consultas de seguimiento. No inventes estados de solicitudes, pedidos, "
            "reservas o casos. Si no existe una fuente de estado en tiempo real, explica que no "
            "puedes confirmarlo y ofrece el siguiente paso permitido."
        ),
    )



# ============================================================
# NEXIA V3.0 - ECOMMERCE ADAPTER
# JUMPSELLER + SHOPIFY (LECTURA)
# ============================================================

ECOMMERCE_TIMEOUT = float(os.getenv("ECOMMERCE_TIMEOUT", "7"))
SHOPIFY_API_VERSION = os.getenv("SHOPIFY_API_VERSION", "2026-07").strip()


def _ecommerce_fernet():
    raw = str(os.getenv("ECOMMERCE_ENCRYPTION_KEY") or "").strip()
    if raw:
        try:
            return Fernet(raw.encode("utf-8"))
        except Exception:
            pass
    secret = str(os.getenv("SECRET_KEY") or app.secret_key or "change-me-in-render").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def _ecommerce_encrypt(value):
    value = str(value or "")
    return _ecommerce_fernet().encrypt(value.encode()).decode() if value else ""


def _ecommerce_decrypt(value):
    value = str(value or "")
    if not value:
        return ""
    try:
        return _ecommerce_fernet().decrypt(value.encode()).decode()
    except Exception:
        return ""


def _ecommerce_clean_domain(value):
    value = str(value or "").strip()
    value = re.sub(r"^https?://", "", value, flags=re.I)
    return value.strip("/")


def _ecommerce_get_config(empresa_id, use_cache=True):
    empresa_id = str(empresa_id or "").strip()
    if not empresa_id:
        return None
    cache_key = f"ecommerce_config:{empresa_id}"
    if use_cache:
        c = _cache_get(cache_key)
        if c is not None:
            return c or None
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/ecommerce_integraciones",
            headers=backend_headers(),
            params={
                "select":"*",
                "empresa_id":f"eq.{empresa_id}",
                "activo":"eq.true",
                "limit":"1"
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        if not rows:
            _cache_set(cache_key, {})
            return None
        row = dict(rows[0])
        for src, dst in (
            ("access_token_enc","access_token"),
            ("login_key_enc","login_key"),
            ("auth_token_enc","auth_token"),
        ):
            row[dst] = _ecommerce_decrypt(row.get(src))
        _cache_set(cache_key, row)
        return row
    except Exception as e:
        print("NEXI ECOMMERCE CONFIG ERROR:", repr(e))
        return None


def _ecommerce_clear_cache(empresa_id):
    """
    Invalida la cache ecommerce usando la misma estructura de TENANT_CACHE.
    _cache_set() no acepta ttl, por eso eliminamos la clave directamente.
    """
    key = f"ecommerce_config:{empresa_id}"
    try:
        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(key, None)
    except Exception as e:
        print("NEXI ECOMMERCE CACHE CLEAR ERROR:", repr(e))


def _ecommerce_money(value, currency="CLP"):
    try:
        n = float(value)
        if str(currency or "CLP").upper() == "CLP":
            return f"${int(round(n)):,}".replace(",", ".")
        return f"{n:,.2f} {currency}"
    except Exception:
        return str(value or "")


def _ecommerce_payment_status_label(status):
    """Traduce estados técnicos de pago a texto claro para clientes."""
    raw = str(status or "").strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    labels = {
        "paid": "Pagado",
        "pending": "Pendiente de pago",
        "unpaid": "No pagado",
        "authorized": "Pago autorizado",
        "partially_paid": "Pago parcial",
        "refunded": "Reembolsado",
        "partially_refunded": "Reembolso parcial",
        "voided": "Pago anulado",
        "cancelled": "Pago cancelado",
        "canceled": "Pago cancelado",
        "abandoned": "Pago no completado",
    }
    return labels.get(key, raw.replace("_", " ").strip().capitalize())


def _ecommerce_fulfillment_status_label(status):
    """
    Traduce fulfillment sin prometer entrega física.
    'fulfilled' significa que la preparación/despacho fue cumplido en la tienda,
    no necesariamente que el transportista ya entregó al cliente.
    """
    raw = str(status or "").strip()
    key = raw.lower().replace("-", "_").replace(" ", "_")
    labels = {
        "fulfilled": "Despachado / preparación completada",
        "unfulfilled": "Pendiente de preparación o despacho",
        "partial": "Parcialmente despachado",
        "partially_fulfilled": "Parcialmente despachado",
        "restocked": "Reingresado a stock",
        "cancelled": "Despacho cancelado",
        "canceled": "Despacho cancelado",
    }
    return labels.get(key, raw.replace("_", " ").strip().capitalize())


def _jumpseller_headers(cfg):
    h = {
        "Accept":"application/json",
        "User-Agent":"Nexia/3.4.4",
    }
    access_token = str(cfg.get("access_token") or "").strip()
    if access_token:
        h["Authorization"] = f"Bearer {access_token}"
    return h


def _jumpseller_request(method, url, cfg, **kwargs):
    headers = _jumpseller_headers(cfg)
    login_key = str(cfg.get("login_key") or "").strip()
    auth_token = str(cfg.get("auth_token") or "").strip()
    access_token = str(cfg.get("access_token") or "").strip()

    auth = None
    auth_mode = "none"
    if login_key and auth_token:
        # Mismo mecanismo que el código anterior que ya funcionó:
        # HTTP Basic Auth -> requests genera Authorization: Basic ...
        auth = (login_key, auth_token)
        auth_mode = "basic_auth"
    elif access_token:
        auth_mode = "oauth"

    print(
        "JUMPSELLER REQUEST:",
        method.upper(),
        url,
        "auth_mode=",
        auth_mode,
        "login_len=",
        len(login_key),
        "auth_len=",
        len(auth_token),
        "oauth_len=",
        len(access_token),
    )

    r = requests.request(
        method,
        url,
        headers=headers,
        auth=auth,
        timeout=ECOMMERCE_TIMEOUT,
        **kwargs,
    )

    if not r.ok:
        body = (r.text or "").strip()
        print(
            "JUMPSELLER API ERROR:",
            "status=", r.status_code,
            "content_type=", r.headers.get("Content-Type"),
            "request_id=", r.headers.get("X-Request-Id") or r.headers.get("X-Request-ID"),
            "body=", body[:1500],
        )
        raise RuntimeError(
            f"Jumpseller rechazó la conexión (HTTP {r.status_code}): "
            f"{body[:500] or 'sin detalle en la respuesta'}"
        )

    return r


def _jumpseller_normalize_image_url(value):
    """Normaliza URLs de imágenes Jumpseller sin inventar rutas."""
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        for key in (
            "url", "src", "image_url", "original", "large", "medium",
            "thumb", "thumbnail", "public_url"
        ):
            candidate = value.get(key)
            if candidate:
                return _jumpseller_normalize_image_url(candidate)
        nested = value.get("image")
        if nested:
            return _jumpseller_normalize_image_url(nested)
        return ""

    url = str(value).strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return ""


def _jumpseller_product_images(prod):
    """
    Extrae foto principal + galería usando varias formas de respuesta
    observables en APIs ecommerce. Limita galería para controlar RAM.
    """
    max_images = max(1, int(os.getenv("JUMPSELLER_MAX_IMAGES_PER_PRODUCT", "6")))
    urls = []

    def add(value):
        url = _jumpseller_normalize_image_url(value)
        if url and url not in urls:
            urls.append(url)

    # Posibles campos principales.
    for key in (
        "image", "image_url", "featured_image", "main_image",
        "primary_image", "thumbnail"
    ):
        add(prod.get(key))

    # Galería estándar.
    images = prod.get("images") or []
    if isinstance(images, dict):
        images = images.get("images") or images.get("data") or [images]
    if isinstance(images, list):
        for raw in images:
            if isinstance(raw, dict) and "image" in raw:
                add(raw.get("image"))
            add(raw)
            if len(urls) >= max_images:
                break

    return urls[:max_images]


def _jumpseller_product_stock(prod):
    """Suma stock controlado de variantes; None = no informado/ilimitado."""
    variants = prod.get("variants") or []
    if not isinstance(variants, list):
        variants = []

    values = []
    unlimited = False

    for raw in variants:
        v = raw.get("variant", raw) if isinstance(raw, dict) else {}
        if not isinstance(v, dict):
            continue
        if v.get("stock_unlimited") is True:
            unlimited = True
            continue
        raw_stock = None
        for key in ("stock", "stock_quantity", "quantity"):
            if v.get(key) is not None:
                raw_stock = v.get(key)
                break
        if raw_stock is not None:
            try:
                values.append(max(0, int(float(raw_stock))))
            except Exception:
                pass

    if values:
        return sum(values)
    if unlimited or prod.get("stock_unlimited") is True:
        return None

    for key in ("stock", "stock_quantity", "quantity"):
        if prod.get(key) is not None:
            try:
                return max(0, int(float(prod.get(key))))
            except Exception:
                pass
    return None


def _jumpseller_light_product(prod, cfg):
    """
    Representación liviana para catálogos grandes.
    Conserva lo necesario para búsqueda/respuesta y hasta N fotos.
    """
    images = _jumpseller_product_images(prod)

    # Precio: primero producto; si no existe, toma la primera variante con precio.
    price = prod.get("price")
    if price in (None, ""):
        for raw in (prod.get("variants") or []):
            v = raw.get("variant", raw) if isinstance(raw, dict) else {}
            if isinstance(v, dict) and v.get("price") not in (None, ""):
                price = v.get("price")
                break

    url = (
        prod.get("url")
        or prod.get("storefront_url")
        or prod.get("permalink")
        or prod.get("product_url")
        or ""
    )

    name = str(prod.get("name") or "Producto").strip()
    description = str(prod.get("description") or "").strip()
    sku = str(prod.get("sku") or "").strip()
    brand = str(prod.get("brand") or "").strip()

    # Índice de búsqueda precomputado para miles de productos.
    search_text = _core_norm(
        " ".join([name, description[:2500], sku, brand])
    )

    return {
        "id": prod.get("id"),
        "name": name,
        "description": description[:2500],
        "price": price,
        "currency": cfg.get("currency") or "CLP",
        "stock": _jumpseller_product_stock(prod),
        "sku": sku or None,
        "brand": brand or None,
        "url": url,
        "image_url": images[0] if images else "",
        "foto_principal": images[0] if images else "",
        "images": images,
        "fotos": images,
        "provider": "jumpseller",
        "_search": search_text,
    }


def _jumpseller_catalog(cfg, force=False):
    """
    Descarga y cachea catálogo completo con paginación.

    Pensado para catálogos grandes:
    - 100 productos/página.
    - Máximo 100 páginas por defecto = hasta 10.000 productos.
    - Configurable con JUMPSELLER_MAX_PRODUCT_PAGES.
    - Reintentos 429/5xx.
    - Deduplicación por ID.
    - Cache configurable para no recorrer miles de productos en cada mensaje.
    """
    empresa_id = str(cfg.get("empresa_id") or cfg.get("id") or "").strip()
    store_id = str(cfg.get("store_url") or cfg.get("store_domain") or "jumpseller")
    cache_key = f"ecommerce_catalog:{empresa_id or store_id}"

    cache_seconds = max(
        30, int(os.getenv("JUMPSELLER_CATALOG_CACHE_SECONDS", "300"))
    )

    if not force:
        cached = TENANT_CACHE.get(cache_key)
        if isinstance(cached, dict):
            ts = float(cached.get("ts") or 0)
            products = cached.get("products")
            if isinstance(products, list) and (time.time() - ts) < cache_seconds:
                print(
                    "JUMPSELLER CATALOG CACHE HIT:",
                    "productos=", len(products),
                    "edad_s=", round(time.time() - ts, 1),
                )
                return products

    page_size = 100
    max_pages = max(
        1, int(os.getenv("JUMPSELLER_MAX_PRODUCT_PAGES", "100"))
    )
    productos = []
    vistos = set()
    base_url = "https://api.jumpseller.com/v1/products.json"

    for page in range(1, max_pages + 1):
        params = {"page": page, "limit": page_size}
        response_data = None

        for attempt in range(1, 4):
            try:
                response_data = _jumpseller_request(
                    "GET", base_url, cfg, params=params
                )
                break
            except Exception as e:
                txt = str(e)
                transient = any(
                    code in txt
                    for code in (
                        "HTTP 429", "HTTP 500", "HTTP 502",
                        "HTTP 503", "HTTP 504"
                    )
                )
                if not transient or attempt >= 3:
                    raise
                espera = 0.4 * attempt
                print(
                    "JUMPSELLER PRODUCTS RETRY:",
                    "pagina=", page,
                    "intento=", attempt,
                    "espera_s=", espera,
                )
                time.sleep(espera)

        data = response_data
        if hasattr(data, "json"):
            payload = data.json() if getattr(data, "content", b"") else {}
        else:
            payload = data

        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("products") or []
        else:
            items = []

        nuevos = 0
        con_foto = 0

        for raw in items:
            prod = (
                raw.get("product", raw)
                if isinstance(raw, dict)
                else {}
            )
            if not isinstance(prod, dict) or not prod:
                continue

            pid = str(prod.get("id") or "").strip()
            dedup_key = pid or json.dumps(
                [
                    prod.get("name"),
                    prod.get("sku"),
                    prod.get("url"),
                    prod.get("permalink"),
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
            if dedup_key in vistos:
                continue
            vistos.add(dedup_key)

            light = _jumpseller_light_product(prod, cfg)
            if light.get("image_url"):
                con_foto += 1
            productos.append(light)
            nuevos += 1

        print(
            "JUMPSELLER PRODUCTS PAGE:",
            page,
            "recibidos=", len(items),
            "nuevos=", nuevos,
            "con_foto=", con_foto,
            "acumulados=", len(productos),
        )

        # Una página incompleta marca el final del catálogo.
        if len(items) < page_size:
            break
    else:
        print(
            "JUMPSELLER PRODUCTS WARNING:",
            "se alcanzó max_pages=", max_pages,
            "productos=", len(productos),
        )

    TENANT_CACHE[cache_key] = {
        "ts": time.time(),
        "products": productos,
    }

    print(
        "JUMPSELLER CATALOG READY:",
        "productos=", len(productos),
        "con_foto=", sum(1 for p in productos if p.get("image_url")),
    )
    return productos


def _jumpseller_products(cfg, query="", limit=6, force=False):
    """
    Busca sobre el catálogo paginado/cacheado.

    Compatible con llamadas históricas:
      _jumpseller_products(cfg, "polera", 5)
    """
    catalog = _jumpseller_catalog(cfg, force=force)

    q = _core_norm(query)
    words = [w for w in q.split() if len(w) > 2]
    limit = min(max(int(limit or 6), 1), 50)

    out = []
    for prod in catalog:
        hay = str(prod.get("_search") or "")
        if words and not all(word in hay for word in words):
            continue

        # No exponer índice interno al Portal/API.
        clean = {k: v for k, v in prod.items() if k != "_search"}
        out.append(clean)
        if len(out) >= limit:
            break

    print(
        "JUMPSELLER SEARCH:",
        "query=", str(query or "")[:80],
        "resultados=", len(out),
        "catalogo=", len(catalog),
    )
    return out


def _jumpseller_order(cfg, reference):
    r = _jumpseller_request(
        "GET",
        "https://api.jumpseller.com/v1/orders.json",
        cfg,
        params={"limit":100},
    )
    rows = r.json() if r.content else []
    ref = _core_norm(reference).replace("#","").strip()
    for raw in rows:
        order = raw.get("order") if isinstance(raw, dict) and "order" in raw else raw
        if not isinstance(order, dict):
            continue
        candidates = [
            order.get("id"), order.get("number"), order.get("order_number"), order.get("email")
        ]
        candidates = [_core_norm(x).replace("#","") for x in candidates if x not in (None,"")]
        if ref in candidates:
            return {
                "number":order.get("number") or order.get("order_number") or order.get("id"),
                "payment_status":order.get("payment_status") or order.get("status"),
                "fulfillment_status":order.get("fulfillment_status") or order.get("shipping_status"),
                "total":order.get("total"),
                "currency":order.get("currency") or cfg.get("currency") or "CLP",
                "tracking_number":order.get("shipping_tracking_number"),
                "tracking_company":order.get("shipping_tracking_company"),
                "provider":"jumpseller",
            }
    return None


def _shopify_graphql(cfg, query, variables=None):
    domain = _ecommerce_clean_domain(cfg.get("store_domain"))
    if not domain:
        raise RuntimeError("Dominio Shopify no configurado")
    url = f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
    r = requests.post(
        url,
        headers={
            "X-Shopify-Access-Token":cfg.get("access_token") or "",
            "Content-Type":"application/json",
            "Accept":"application/json",
        },
        json={"query":query,"variables":variables or {}},
        timeout=ECOMMERCE_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json() if r.content else {}
    if body.get("errors"):
        raise RuntimeError(str(body["errors"])[:500])
    return body.get("data") or {}


def _shopify_products(cfg, query, limit=6):
    gql = """
    query NexiaProducts($first:Int!, $query:String!) {
      products(first:$first, query:$query) {
        nodes {
          id title handle onlineStoreUrl totalInventory
          variants(first:10) {
            nodes { id title sku price availableForSale inventoryQuantity }
          }
        }
      }
    }
    """
    data = _shopify_graphql(
        cfg, gql,
        {"first":min(max(int(limit),1),20),"query":str(query or "").strip()}
    )
    out = []
    for prod in ((data.get("products") or {}).get("nodes") or []):
        variants = ((prod.get("variants") or {}).get("nodes") or [])
        first = variants[0] if variants else {}
        out.append({
            "id":prod.get("id"),
            "name":prod.get("title") or "Producto",
            "price":first.get("price"),
            "currency":cfg.get("currency") or "CLP",
            "stock":prod.get("totalInventory"),
            "sku":first.get("sku"),
            "url":prod.get("onlineStoreUrl"),
            "provider":"shopify",
        })
    return out


def _shopify_order(cfg, reference):
    ref = str(reference or "").strip()
    q = f"email:{ref}" if "@" in ref else f"name:{ref}"
    gql = """
    query NexiaOrder($query:String!) {
      orders(first:5, query:$query) {
        nodes {
          id name displayFinancialStatus displayFulfillmentStatus
          currentTotalPriceSet { shopMoney { amount currencyCode } }
          fulfillments { trackingInfo { number company url } }
        }
      }
    }
    """
    data = _shopify_graphql(cfg, gql, {"query":q})
    nodes = ((data.get("orders") or {}).get("nodes") or [])
    if not nodes:
        return None
    order = nodes[0]
    money = ((order.get("currentTotalPriceSet") or {}).get("shopMoney") or {})
    tracking = []
    for f in order.get("fulfillments") or []:
        tracking.extend(f.get("trackingInfo") or [])
    tr = tracking[0] if tracking else {}
    return {
        "number":order.get("name"),
        "payment_status":order.get("displayFinancialStatus"),
        "fulfillment_status":order.get("displayFulfillmentStatus"),
        "total":money.get("amount"),
        "currency":money.get("currencyCode") or cfg.get("currency") or "CLP",
        "tracking_number":tr.get("number"),
        "tracking_company":tr.get("company"),
        "tracking_url":tr.get("url"),
        "provider":"shopify",
    }


def ecommerce_search_products(empresa_id, query, limit=6):
    cfg_e = _ecommerce_get_config(empresa_id)
    if not cfg_e:
        return []
    provider = str(cfg_e.get("provider") or "").lower()
    if provider == "jumpseller":
        return _jumpseller_products(cfg_e, query, limit)
    if provider == "shopify":
        return _shopify_products(cfg_e, query, limit)
    return []


def ecommerce_get_order(empresa_id, reference):
    cfg_e = _ecommerce_get_config(empresa_id)
    if not cfg_e:
        return None
    provider = str(cfg_e.get("provider") or "").lower()
    if provider == "jumpseller":
        return _jumpseller_order(cfg_e, reference)
    if provider == "shopify":
        return _shopify_order(cfg_e, reference)
    return None


def _ecommerce_extract_ref(texto):
    t = str(texto or "")
    m = re.search(r"#?\b(\d{3,12})\b", t)
    if m:
        return m.group(1)
    m = re.search(r"[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}", t)
    return m.group(0) if m else ""


def _ecommerce_product_query(texto):
    t = str(texto or "")
    t = re.sub(
        r"\b(tienen|tienes|hay|stock|disponible|disponibilidad|producto|productos|precio|precios|valor|valores|cuanto|cuánto|cuesta|quiero|busco|necesito|comprar|venden|vende|una|un|el|la|los|las|de|en)\b",
        " ", t, flags=re.I
    )
    return re.sub(r"\s+"," ",t).strip(" ?!.,") or str(texto or "").strip()


def _ecommerce_intent(texto):
    t = _core_norm(texto)

    # Pedidos/órdenes deben ir siempre al agente Ecommerce, incluso cuando
    # el usuario escribe solo "pedido 7441".
    if re.search(r"\b(pedido|pedidos|orden|ordenes|órdenes|tracking)\b", t):
        return True

    return any(x in t for x in (
        "stock","disponible","disponibilidad","sku","talla","color",
        "producto","productos","catalogo","catálogo",
        "estado del pedido","seguimiento de pedido",
        "despacho de mi","envio de mi","envío de mi",
    ))


def _core_agent_ecommerce(empresa_id, texto, ctx=None):
    cfg_e = _ecommerce_get_config(empresa_id)
    if not cfg_e:
        return (
            "Este negocio todavía no tiene una tienda online conectada a Nexia. "
            "Puedo ayudarte con la información general disponible."
        )

    t = _core_norm(texto)
    wants_order = any(x in t for x in (
        "pedido","orden","tracking","seguimiento","despacho de mi","envio de mi","envío de mi"
    ))

    if wants_order:
        ref = _ecommerce_extract_ref(texto)
        if not ref:
            return "Para revisar tu pedido, indícame el número de pedido o el correo asociado a la compra."
        try:
            order = ecommerce_get_order(empresa_id, ref)
        except Exception as e:
            print("NEXI ECOMMERCE ORDER ERROR:", repr(e))
            return "No pude consultar el pedido en este momento. Intenta nuevamente en unos minutos."
        if not order:
            return "No encontré un pedido con ese dato. Revisa el número o correo e inténtalo nuevamente."

        numero = order.get("number") or ref
        lines = [f"📦 Pedido #{numero}"]

        if order.get("payment_status"):
            lines.append(
                f"💳 Pago: {_ecommerce_payment_status_label(order['payment_status'])}"
            )

        if order.get("fulfillment_status"):
            lines.append(
                f"🚚 Despacho: {_ecommerce_fulfillment_status_label(order['fulfillment_status'])}"
            )

        if order.get("total"):
            lines.append(
                f"💰 Total: {_ecommerce_money(order['total'], order.get('currency'))}"
            )

        if order.get("tracking_number"):
            tr = f"📍 Seguimiento: {order['tracking_number']}"
            if order.get("tracking_company"):
                tr += f" ({order['tracking_company']})"
            lines.append(tr)

        if order.get("tracking_url"):
            lines.append(f"🔗 Seguimiento online: {order['tracking_url']}")

        return "\n\n".join(lines)

    query = _ecommerce_product_query(texto)
    try:
        products = ecommerce_search_products(empresa_id, query, 5)
    except Exception as e:
        print("NEXI ECOMMERCE PRODUCT ERROR:", repr(e))
        return "No pude consultar la tienda en este momento. Intenta nuevamente en unos minutos."

    if not products:
        return "No encontré un producto que coincida con tu búsqueda en la tienda conectada."

    lines = []
    for prod in products[:5]:
        line = f"• {prod.get('name') or 'Producto'}"
        if prod.get("price") not in (None,""):
            line += f" — {_ecommerce_money(prod['price'], prod.get('currency'))}"
        if prod.get("stock") is not None:
            try:
                line += " — disponible" if int(prod["stock"]) > 0 else " — sin stock"
            except Exception:
                pass
        if prod.get("url"):
            line += f"\n  🔗 {prod['url']}"
        if prod.get("image_url"):
            line += f"\n  🖼️ {prod['image_url']}"
        lines.append(line)
    return "Encontré esto en la tienda:\n" + "\n".join(lines)


@app.route("/portal/integraciones/ecommerce", methods=["GET","POST","DELETE","OPTIONS"])
def portal_integracion_ecommerce():
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)

    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    if not empresa_id:
        return portal_json({"ok":False,"error":"Tu usuario no tiene una empresa asociada"},409)

    try:
        headers = backend_headers()

        if request.method == "GET":
            cfg_e = _ecommerce_get_config(empresa_id, use_cache=False)
            if not cfg_e:
                return portal_json({"ok":True,"conectado":False})
            return portal_json({
                "ok":True,
                "conectado":True,
                "provider":cfg_e.get("provider"),
                "store_domain":cfg_e.get("store_domain"),
                "store_url":cfg_e.get("store_url"),
                "modo":"lectura",
            })

        if request.method == "DELETE":
            r = requests.patch(
                f"{SUPABASE_URL}/rest/v1/ecommerce_integraciones",
                headers={**headers,"Prefer":"return=minimal"},
                params={"empresa_id":f"eq.{empresa_id}"},
                json={"activo":False,"updated_at":datetime.now(pytz.UTC).isoformat()},
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()
            _ecommerce_clear_cache(empresa_id)
            return portal_json({"ok":True})

        data = request.get_json(silent=True) or {}
        provider = str(data.get("provider") or "").strip().lower()
        if provider not in {"jumpseller","shopify"}:
            return portal_json({"ok":False,"error":"Proveedor no válido"},400)

        payload = {
            "empresa_id":empresa_id,
            "provider":provider,
            "activo":True,
            "updated_at":datetime.now(pytz.UTC).isoformat(),
        }

        if provider == "jumpseller":
            store_url = str(data.get("store_url") or "").strip()
            login_key = str(data.get("login_key") or "").strip()
            auth_token = str(data.get("auth_token") or "").strip()
            if not (login_key and auth_token):
                return portal_json({"ok":False,"error":"Ingresa Login Key y Auth Token de Jumpseller."},400)
            payload.update({
                "store_url":store_url,
                "store_domain":_ecommerce_clean_domain(store_url),
                "login_key_enc":_ecommerce_encrypt(login_key),
                "auth_token_enc":_ecommerce_encrypt(auth_token),
                "access_token_enc":"",
            })

        if provider == "shopify":
            domain = _ecommerce_clean_domain(data.get("store_domain"))
            token = str(data.get("access_token") or "").strip()
            if not domain or not token:
                return portal_json({"ok":False,"error":"Ingresa el dominio myshopify.com y el Admin API access token."},400)
            payload.update({
                "store_domain":domain,
                "store_url":f"https://{domain}",
                "access_token_enc":_ecommerce_encrypt(token),
                "login_key_enc":"",
                "auth_token_enc":"",
            })

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/ecommerce_integraciones",
            headers={**headers,"Prefer":"resolution=merge-duplicates,return=representation"},
            params={"on_conflict":"empresa_id"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        _ecommerce_clear_cache(empresa_id)

        # Prueba de conexión.
        cfg_test = _ecommerce_get_config(empresa_id, use_cache=False)
        try:
            if provider == "jumpseller":
                _jumpseller_products(cfg_test, "", 1)
            else:
                _shopify_products(cfg_test, "", 1)
        except Exception as e:
            print("NEXI ECOMMERCE TEST CONNECTION ERROR:",repr(e))
            msg = str(e)
            return portal_json({
                "ok":False,
                "error": msg if "Jumpseller rechazó la conexión" in msg else
                         "Las credenciales se guardaron, pero la prueba de conexión falló. Revisa permisos, token y dominio."
            },400)

        return portal_json({"ok":True,"conectado":True,"provider":provider})

    except Exception as e:
        print("PORTAL ECOMMERCE ERROR:",repr(e))
        return portal_json({"ok":False,"error":"No se pudo gestionar la integración ecommerce"},500)


@app.route("/portal/integraciones/ecommerce/probar", methods=["POST","OPTIONS"])
def portal_integracion_ecommerce_probar():
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)
    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    data = request.get_json(silent=True) or {}
    query = str(data.get("query") or "").strip()
    if not query:
        return portal_json({"ok":False,"error":"Escribe un producto para probar"},400)
    try:
        products = ecommerce_search_products(empresa_id, query, 5)
        return portal_json({"ok":True,"productos":products})
    except Exception as e:
        print("PORTAL ECOMMERCE PROBAR ERROR:",repr(e))
        return portal_json({"ok":False,"error":"No pude consultar la tienda"},500)


CORE_AGENT_REGISTRY = {
    "atencion": _core_agent_atencion,
    "conocimiento": _core_agent_conocimiento,
    "ventas": _core_agent_ventas,
    "agenda": _core_agent_agenda,
    "soporte": _core_agent_soporte,
    "seguimiento": _core_agent_seguimiento,
    "ecommerce": _core_agent_ecommerce,
}


def _core_log_orchestration(empresa_id, token, canal, texto, agente):
    """Auditoría no bloqueante: si la tabla aún no existe, la conversación sigue."""
    try:
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/nexi_core_orquestacion",
            headers=_core_headers("return=minimal"),
            json={
                "empresa_id": empresa_id,
                "onboarding_token": token or None,
                "canal": canal,
                "mensaje": str(texto or "")[:4000],
                "agente": agente,
                "created_at": datetime.now(pytz.UTC).isoformat(),
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
    except Exception as e:
        # La auditoría del orquestador es opcional y nunca debe botar la conversación.
        if "404" in str(e):
            print("NEXI ORCHESTRATION LOG: tabla nexi_core_orquestacion no instalada; se omite auditoría.")
        else:
            print("NEXI ORCHESTRATION LOG SKIP:", repr(e))


def _core_orchestrate(empresa_id, texto, token=None, canal="web"):
    """
    Orquestador de producción:
    1) carga perfil una sola vez;
    2) clasifica intención sin IA;
    3) responde en forma estructurada cuando los datos ya bastan;
    4) recién entonces arma contexto web y llama a OpenAI como fallback.
    """
    import time as _time
    total0 = _time.perf_counter()

    t0 = _time.perf_counter()
    perfil, datos = _core_public_profile(empresa_id)
    perfil_s = _time.perf_counter() - t0

    t0 = _time.perf_counter()
    agente = _core_route_intent(texto, datos)
    router_s = _time.perf_counter() - t0

    if agente == "handoff":
        agente = "atencion"
    if not _core_agent_enabled(agente, datos):
        agente = "atencion"

    empresa = str(
        datos.get("nombre_negocio")
        or datos.get("empresa_nombre")
        or cfg("empresa_nombre", "este negocio")
        or "este negocio"
    ).strip()
    asistente = str(
        datos.get("nombre_asistente")
        or datos.get("asistente_nombre")
        or cfg("asistente_nombre", "asistente virtual")
        or "asistente virtual"
    ).strip()

    # FAST PATH global: evita OpenAI Y evita consulta de conocimiento web.
    t0 = _time.perf_counter()
    directa = None
    if agente in {"atencion", "conocimiento", "ventas"}:
        directa = _core_respuesta_estructurada(
            texto, datos, empresa=empresa, asistente=asistente
        )
    fast_s = _time.perf_counter() - t0

    if directa:
        agente_real = "conocimiento" if agente in {"atencion", "conocimiento"} else agente
        t0 = _time.perf_counter()
        _core_log_orchestration(empresa_id, token, canal, texto, agente_real)
        audit_s = _time.perf_counter() - t0
        total_s = _time.perf_counter() - total0
        print("NEXI ORCHESTRATOR:", empresa_id, canal, "->", agente_real, "(FAST STRUCTURED)")
        print(
            f"NEXI PERF TOTAL={total_s:.3f}s perfil={perfil_s:.3f}s "
            f"router={router_s:.3f}s fast={fast_s:.3f}s contexto=0.000s "
            f"agente=0.000s auditoria={audit_s:.3f}s openai=NO"
        )
        return directa, agente_real

    # Solo las consultas no resolubles con datos estructurados llegan aquí.
    t0 = _time.perf_counter()
    ctx = _core_agent_context(
        empresa_id, texto, public_profile=(perfil, datos)
    )
    contexto_s = _time.perf_counter() - t0

    fn = CORE_AGENT_REGISTRY.get(agente, _core_agent_atencion)
    t0 = _time.perf_counter()
    respuesta = fn(empresa_id, texto, ctx=ctx)
    agente_s = _time.perf_counter() - t0

    t0 = _time.perf_counter()
    _core_log_orchestration(empresa_id, token, canal, texto, agente)
    audit_s = _time.perf_counter() - t0

    total_s = _time.perf_counter() - total0
    print("NEXI ORCHESTRATOR:", empresa_id, canal, "->", agente, "(AI FALLBACK)")
    print(
        f"NEXI PERF TOTAL={total_s:.3f}s perfil={perfil_s:.3f}s "
        f"router={router_s:.3f}s fast={fast_s:.3f}s contexto={contexto_s:.3f}s "
        f"agente={agente_s:.3f}s auditoria={audit_s:.3f}s"
    )
    return respuesta, agente

def _core_responder_demo_web(token, empresa_id, texto):
    """Simulador web con handoff persistente."""
    perfil = _core_profile(empresa_id) or {}
    datos_perfil = perfil.get("datos") or {}
    identificador = f"web:{token}"

    _core_log_message(token, empresa_id, "entrante", texto)

    hs = _core_handoff_lookup(empresa_id, identificador, "web")
    estado_handoff = str((hs or {}).get("estado") or "").strip().lower()

    if estado_handoff == "derivado":
        if not _core_handoff_expirado(hs):
            return 'Seguimos en contacto con el ejecutivo. Tu solicitud ya fue enviada y tus mensajes están quedando registrados para que pueda revisarlos al continuar la atención.'
        _core_handoff_upsert(
            empresa_id,
            identificador,
            "web",
            {
                "estado": "cerrado",
                "datos": {
                    **(hs.get("datos") or {}),
                    "cierre": "timeout",
                    "timeout_minutos": CORE_HANDOFF_TIMEOUT_MINUTOS,
                },
                "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
            },
        )
        return (
            f"No hemos podido conectarte con una persona dentro de los "
            f"{CORE_HANDOFF_TIMEOUT_MINUTOS} minutos estimados. "
            "Puedo seguir ayudándote por aquí mientras tanto.\n\n"
            + _core_orchestrate(empresa_id, texto, token=token, canal="web")[0]
        )

    if estado_handoff == "recolectando":
        if _core_cancelar_handoff_texto(texto):
            _core_handoff_upsert(
                empresa_id,
                identificador,
                "web",
                {
                    "estado": "cerrado",
                    "datos": {**(hs.get("datos") or {}), "cierre": "cancelado_usuario"},
                    "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            return "Perfecto, cancelé la derivación. Seguimos con el asistente 😊 ¿En qué te puedo ayudar?"

        detalles = _core_parse_handoff_details("empresa", texto)
        nombre = str(detalles.get("nombre") or "").strip()
        empresa_contacto = str(detalles.get("empresa") or "").strip()
        motivo = str(detalles.get("motivo") or "").strip()

        if not nombre or not empresa_contacto or not motivo:
            return _core_handoff_prompt("empresa")

        _core_handoff_request(empresa_id, identificador, "web", detalles, motivo)
        _core_handoff_upsert(
            empresa_id,
            identificador,
            "web",
            {
                "estado": "derivado",
                "datos": detalles,
                "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
            },
        )
        return (
            f"Gracias, {nombre} 🙌\n\n"
            "Ya registré tu solicitud y estamos en contacto con el ejecutivo. "
            f"El tiempo estimado de atención es de hasta {CORE_HANDOFF_TIMEOUT_MINUTOS} minutos. "
            "Puedes seguir escribiendo; tus mensajes quedarán registrados mientras esperas."
        )

    if estado_handoff == "ofrecido":
        if _core_cancelar_handoff_texto(texto):
            canal_handoff = "web" if str(identificador).startswith("web:") else "whatsapp"
            destino_handoff = identificador if canal_handoff == "web" else telefono
            _core_handoff_upsert(
                empresa_id,
                destino_handoff,
                canal_handoff,
                {
                    "estado": "cerrado",
                    "datos": {**(hs.get("datos") or {}), "cierre": "cancelado_usuario"},
                    "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            return "Perfecto, cancelé la derivación. Seguimos con el asistente 😊 ¿En qué te puedo ayudar?"

        if _core_es_confirmacion(texto):
            _core_handoff_upsert(
                empresa_id,
                identificador,
                "web",
                {
                    "estado": "recolectando",
                    "datos": {},
                    "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            return _core_handoff_prompt("empresa")

        if _core_es_rechazo(texto):
            _core_handoff_upsert(
                empresa_id,
                identificador,
                "web",
                {
                    "estado": "cerrado",
                    "datos": hs.get("datos") or {},
                    "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            return "Perfecto. Seguimos por aquí 😊 ¿En qué más te puedo ayudar?"

        return (
            "Tengo pendiente tu solicitud de hablar con una persona. "
            "Si quieres continuar, responde sí; si prefieres seguir con el asistente, responde no."
        )

    if _core_es_handoff(texto) and _core_si(datos_perfil.get("handoff")):
        _core_handoff_upsert(
            empresa_id,
            identificador,
            "web",
            {
                "estado": "recolectando",
                "datos": {"solicitud_original": str(texto or "")},
                "started_at": datetime.now(pytz.UTC).isoformat(),
            },
        )
        return _core_handoff_prompt("empresa")

    respuesta, agente_usado = _core_orchestrate(empresa_id, texto, token=token, canal="web")

    nr = normalizar_texto(respuesta)
    if _core_si(datos_perfil.get("handoff")) and any(
        x in nr
        for x in (
            "puedo derivarte",
            "quieres que te derive",
            "puedo ponerte en contacto",
            "quieres hablar con una persona",
            "derivarte internamente",
        )
    ):
        _core_handoff_upsert(
            empresa_id,
            identificador,
            "web",
            {
                "estado": "ofrecido",
                "datos": {"respuesta_oferta": respuesta},
                "started_at": datetime.now(pytz.UTC).isoformat(),
            },
        )

    return respuesta


def _core_responder_demo_whatsapp(demo_access, telefono, texto):
    """Procesa demo real por WhatsApp con handoff contextual y privacidad estricta."""
    empresa_id=str((demo_access or {}).get("empresa_id") or empresa_actual_id() or "").strip()
    if not empresa_id:
        raise RuntimeError("Demo sin empresa_id")

    activar_por_empresa(empresa_id,canal="whatsapp",provider="demo")
    token=_core_token_por_empresa(empresa_id)
    perfil=_core_profile(empresa_id) or {}
    datos_perfil=perfil.get("datos") or {}
    tipo=_core_tipo(datos_perfil.get("tipo_cliente")) or "personal"

    if token:
        _core_log_message(token,empresa_id,"entrante",texto)
    # V1.9: espejo operativo para que las demos aparezcan en la bandeja humana.
    guardar_mensaje_supabase(telefono, "entrante", texto, canal="whatsapp")

    hs=_core_handoff_lookup(empresa_id,telefono,"whatsapp")
    estado_handoff=str((hs or {}).get("estado") or "").strip().lower()

    # V2.2.2: Agenda tiene prioridad sobre handoffs pendientes (ofrecido/recolectando).
    # Un handoff ya DERIVADO sigue teniendo prioridad porque la atención humana ya fue solicitada.
    agenda_session_key = f"core:{empresa_id}:{_normalizar_identificador_demo(telefono, 'whatsapp')}"
    agenda_estado = get_estado(agenda_session_key)
    agenda_activa = str(agenda_estado.get("paso") or "inicio") != "inicio"

    # V3.4.5: contexto corto para seguimiento de pedidos.
    # Si Nexia acaba de pedir número/correo, una respuesta como "7441"
    # debe interpretarse como referencia del pedido y no como texto aislado.
    texto_core = texto
    ref_pedido_contextual = ""
    if agenda_estado.get("ecommerce_esperando_referencia"):
        ref_pedido_contextual = _ecommerce_extract_ref(texto)
        if ref_pedido_contextual:
            texto_core = f"pedido {ref_pedido_contextual}"
            agenda_estado["ecommerce_esperando_referencia"] = False
            print(
                "NEXI ECOMMERCE ORDER CONTEXT:",
                empresa_id,
                _normalizar_identificador_demo(telefono, "whatsapp"),
                "ref_detectada=SI",
            )

    # V2.5.3: una consulta explícita por servicios debe interrumpir un paso viejo
    # de agenda (por ejemplo, seleccionar_hora) y volver a la selección de servicio.
    # Esto evita que "quiero conocer sus servicios" reutilice horas de una reserva anterior.
    if agenda_activa and pregunta_servicios(texto):
        agenda_estado["paso"] = "servicio"
        agenda_estado["servicio"] = None
        agenda_estado["fecha_hora"] = None
        agenda_estado["horas_ofrecidas"] = []
        agenda_activa = True
        print(
            "NEXI CORE AGENDA REINICIO POR CONSULTA SERVICIOS:",
            empresa_id,
            _normalizar_identificador_demo(telefono, "whatsapp"),
        )

    agente_previsto = _core_route_intent(texto_core, datos_perfil)
    agenda_solicitada = agente_previsto == "agenda"

    if estado_handoff in {"recolectando", "ofrecido"} and (agenda_solicitada or agenda_activa):
        _core_handoff_upsert(
            empresa_id,
            telefono,
            "whatsapp",
            {
                "estado": "cerrado",
                "datos": {**((hs or {}).get("datos") or {}), "cierre": "interrumpido_por_agenda"},
                "started_at": (hs or {}).get("started_at") or datetime.now(pytz.UTC).isoformat(),
            },
        )
        estado_handoff = "cerrado"
        print("NEXI CORE HANDOFF CERRADO POR AGENDA:", empresa_id, _normalizar_identificador_demo(telefono, "whatsapp"))

    if estado_handoff == "derivado":
        if not _core_handoff_expirado(hs):
            print("NEXI CORE HANDOFF ACTIVO:",empresa_id,_normalizar_identificador_demo(telefono,"whatsapp"))
            return 'Seguimos en contacto con el ejecutivo. Tu solicitud ya fue enviada y tus mensajes están quedando registrados para que pueda revisarlos al continuar la atención.'
        _core_handoff_upsert(
            empresa_id,telefono,"whatsapp",
            {
                "estado":"cerrado",
                "datos":{**(hs.get("datos") or {}),"cierre":"timeout","timeout_minutos":CORE_HANDOFF_TIMEOUT_MINUTOS},
                "started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
            }
        )
        respuesta=(
            f"No hemos podido conectarte con una persona dentro de los {CORE_HANDOFF_TIMEOUT_MINUTOS} minutos estimados. "
            "Puedo seguir ayudándote por aquí mientras tanto.\n\n"
            + _core_orchestrate(empresa_id, texto, token=token, canal="whatsapp")[0]
        )

    if estado_handoff == "recolectando":
        if _core_cancelar_handoff_texto(texto):
            _core_handoff_upsert(
                empresa_id,
                telefono,
                "whatsapp",
                {
                    "estado": "cerrado",
                    "datos": {**(hs.get("datos") or {}), "cierre": "cancelado_usuario"},
                    "started_at": hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            return "Perfecto, cancelé la derivación. Seguimos con el asistente 😊 ¿En qué te puedo ayudar?"

        detalles=_core_parse_handoff_details(tipo,texto)
        motivo=str(detalles.get("motivo") or "").strip()
        nombre=str(detalles.get("nombre") or "").strip()

        if not nombre or not motivo:
            respuesta=_core_handoff_prompt(tipo)
        else:
            _core_handoff_request(empresa_id,telefono,"whatsapp",detalles,motivo)
            _core_handoff_upsert(
                empresa_id,telefono,"whatsapp",
                {"estado":"derivado","datos":detalles,"started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat()}
            )
            respuesta=(
                f"Gracias, {nombre} 🙌\n\n"
                "Ya registré tu solicitud y estamos en contacto con el ejecutivo. "
                f"El tiempo estimado de atención es de hasta {CORE_HANDOFF_TIMEOUT_MINUTOS} minutos. "
                "Puedes seguir escribiendo por aquí mientras esperas."
            )

    elif estado_handoff == "ofrecido":
        if _core_es_confirmacion(texto):
            _core_handoff_upsert(
                empresa_id,telefono,"whatsapp",
                {"estado":"recolectando","datos":{},"started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat()}
            )
            respuesta=_core_handoff_prompt(tipo)
        elif _core_es_rechazo(texto):
            _core_handoff_upsert(
                empresa_id,telefono,"whatsapp",
                {"estado":"cerrado","datos":hs.get("datos") or {},"started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat()}
            )
            respuesta="Perfecto. Seguimos por aquí 😊 ¿En qué más te puedo ayudar?"
        else:
            respuesta=(
                "Tengo pendiente tu solicitud de hablar con una persona. "
                "Si quieres continuar, responde *sí*; si prefieres seguir con el asistente, responde *no*."
            )

    elif _core_es_handoff(texto) and _core_si(datos_perfil.get("handoff")):
        # Solicitud explícita: no hacemos una segunda confirmación innecesaria.
        _core_handoff_upsert(
            empresa_id,telefono,"whatsapp",
            {"estado":"recolectando","datos":{"solicitud_original":str(texto or "")},"started_at":datetime.now(pytz.UTC).isoformat()}
        )
        respuesta=_core_handoff_prompt(tipo)

    elif _core_respuesta_no_verificable(texto):
        respuesta=(
            "No tengo una fuente en tiempo real que me permita confirmar el estado actual "
            "de esa persona. Si esa información fue compartida anteriormente, puedo referirme "
            "a ella como información previa, pero no como una confirmación actual."
        )
    else:
        # Si esta empresa conectó Google Calendar y tiene servicios reales configurados,
        # el agente de agenda usa disponibilidad y creación de eventos reales.
        # Sin conexión, conserva el flujo de solicitud/orientación del Core.
        # V2.2.2: agente_previsto / agenda_activa se calcularon antes del handoff
        # para que una intención de agenda explícita no sea consumida por un
        # handoff pendiente de una conversación anterior.

        if negocio_tiene_calendar_real() and (agente_previsto == "agenda" or agenda_activa):
            # Agenda estándar Nexia: mismo flujo probado de Diego, pero con el
            # Calendar, horarios, duración y servicios del empresa_id actual.
            respuesta = _core_procesar_agenda_estandar(empresa_id, telefono, texto, datos_perfil)
            agente_usado = "agenda"
            print(
                "NEXI CORE AGENDA ESTANDAR:",
                empresa_id,
                google_calendar_id_actual(),
                "paso=",
                get_estado(agenda_session_key).get("paso"),
            )
        elif agente_previsto == "agenda":
            # La empresa configuró agenda pero todavía no conectó un Calendar real.
            # Nunca inventamos disponibilidad ni confirmamos reservas.
            respuesta = (
                "📅 Muy pronto podrás agendar directamente desde aquí.\n"
                "En breve integraremos el calendario a nuestros servicios para habilitar la reserva online."
            )
            agente_usado = "agenda"
            print("NEXI CORE AGENDA SIN CALENDAR:", empresa_id)
        else:
            respuesta, agente_usado = _core_orchestrate(empresa_id, texto_core, token=token, canal="whatsapp")

        # V3.4.5: recordar cuando el agente de ecommerce acaba de pedir la referencia.
        nr_pre = normalizar_texto(respuesta)
        if agente_usado == "ecommerce" and (
            "indicame el numero de pedido" in nr_pre
            or "indícame el número de pedido" in respuesta.lower()
            or "correo asociado a la compra" in nr_pre
        ):
            agenda_estado["ecommerce_esperando_referencia"] = True
        elif agente_usado == "ecommerce" and ref_pedido_contextual:
            agenda_estado["ecommerce_esperando_referencia"] = False

        # Si el agente termina ofreciendo handoff, dejamos contexto pendiente para que "sí" tenga sentido.
        nr=normalizar_texto(respuesta)
        if _core_si(datos_perfil.get("handoff")) and any(x in nr for x in (
            "puedo derivarte", "quieres que te derive", "puedo ponerte en contacto",
            "quieres hablar con una persona", "derivarte internamente"
        )):
            _core_handoff_upsert(
                empresa_id,telefono,"whatsapp",
                {"estado":"ofrecido","datos":{"respuesta_oferta":respuesta},"started_at":datetime.now(pytz.UTC).isoformat()}
            )

    respuesta=proteger_respuesta_publica_core(respuesta)
    es_mensaje_espera_handoff = (respuesta == 'Seguimos en contacto con el ejecutivo. Tu solicitud ya fue enviada y tus mensajes están quedando registrados para que pueda revisarlos al continuar la atención.')
    if not es_mensaje_espera_handoff:
        respuesta=preparar_mensaje_saliente_demo(respuesta)

    if token and respuesta:
        _core_log_message(token,empresa_id,"saliente",respuesta)
    if respuesta:
        guardar_mensaje_supabase(telefono, "saliente", respuesta, canal="whatsapp")

    print("NEXI CORE WHATSAPP DEMO:",empresa_id,_normalizar_identificador_demo(telefono,"whatsapp"))
    return respuesta


def _core_demo_answer(empresa_id, texto):
    """Compatibilidad: toda respuesta nueva pasa por el orquestador central."""
    respuesta, _ = _core_orchestrate(empresa_id, texto, canal="compat")
    return respuesta



def _core_log_message(token, empresa_id, direccion, mensaje):
    try:
        r=requests.post(
            f"{SUPABASE_URL}/rest/v1/nexi_core_demo_mensajes",
            headers=_core_headers("return=minimal"),
            json={"onboarding_token":token,"empresa_id":empresa_id,"direccion":direccion,"mensaje":str(mensaje or "")},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
    except Exception as e:
        print("NEXI CORE LOG ERROR:",repr(e))


def core_json(payload,status=200):
    resp=app.response_class(response=json.dumps(payload,ensure_ascii=False),status=status,mimetype="application/json")
    resp.headers["Access-Control-Allow-Origin"]="*"
    resp.headers["Access-Control-Allow-Headers"]="Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"]="GET, POST, OPTIONS"
    return resp


@app.route("/core/onboarding/start",methods=["POST","OPTIONS"])
def core_onboarding_start():
    if request.method=="OPTIONS": return core_json({"ok":True},204)
    try:
        token=uuid.uuid4().hex[:20]
        now=datetime.now(pytz.UTC).isoformat()
        row={"token":token,"version":CORE_ONBOARDING_VERSION,"estado":"onboarding","datos":{},"completado":False,"created_at":now,"updated_at":now}
        r=requests.post(f"{SUPABASE_URL}/rest/v1/nexi_core_onboarding",headers=_core_headers("return=representation"),json=row,timeout=SUPABASE_TIMEOUT);r.raise_for_status()
        return core_json({"ok":True,"token":token,"question":_core_next_question({}),"progress":0},201)
    except Exception as e:
        print("CORE START ERROR:",repr(e)); return core_json({"ok":False,"error":str(e)[:300]},500)


@app.route("/core/onboarding/<token>",methods=["GET","OPTIONS"])
def core_onboarding_get(token):
    if request.method=="OPTIONS": return core_json({"ok":True},204)
    try:
        s=_core_get_session(token)
        if not s:return core_json({"ok":False,"error":"Onboarding no encontrado"},404)
        datos=s.get("datos") or {}; q=_core_next_question(datos); seq=_core_secuencia(datos); done=sum(1 for k in seq if datos.get(k) not in (None,""))
        return core_json({"ok":True,"session":s,"question":q,"progress":round(done/max(1,len(seq))*100),"summary":datos})
    except Exception as e:return core_json({"ok":False,"error":str(e)[:300]},500)


@app.route("/core/onboarding/<token>/answer",methods=["POST","OPTIONS"])
def core_onboarding_answer(token):
    if request.method=="OPTIONS": return core_json({"ok":True},204)
    try:
        s=_core_get_session(token)
        if not s:return core_json({"ok":False,"error":"Onboarding no encontrado"},404)
        datos=dict(s.get("datos") or {}); current=_core_next_question(datos)
        if not current:
            empresa_id=_core_create_company_from_session(s)
            perfil_demo = _core_get_session(token) or {}
            datos_final = perfil_demo.get("datos") or datos
            return core_json({"ok":True,"complete":True,"empresa_id":empresa_id,"summary":datos_final,"portal_acceso":datos_final.get("portal_acceso"),"whatsapp":_core_whatsapp_info(empresa_id)})
        body=request.get_json(silent=True) or {}; value=body.get("answer")
        if value is None:return core_json({"ok":False,"error":"Falta answer"},400)
        key=current["key"]

        if key == "presencia_digital":
            if not isinstance(value, dict):
                return core_json({"ok":False,"error":"La presencia digital debe enviarse como campos estructurados"},400)
            limpio={k:str(v or "").strip() for k,v in value.items() if str(v or "").strip()}
            datos[key]=limpio or {"sin_presencia": "true"}

        elif key == "direccion_fisica" and isinstance(value, dict):
            tiene = bool(value.get("tiene_direccion"))
            if not tiene:
                datos["direccion_fisica"] = {
                    "tiene_direccion": False,
                    "direccion": "",
                    "comuna": "",
                    "referencia": "",
                    "google_maps_url": "",
                }
                datos["atiende_direccion_fisica"] = False
                datos["direccion"] = ""
                datos["comuna"] = ""
                datos["referencia_direccion"] = ""
                datos["google_maps_url"] = ""
            else:
                direccion = str(value.get("direccion") or "").strip()
                comuna = str(value.get("comuna") or "").strip()
                referencia = str(value.get("referencia") or "").strip()
                maps_url = str(value.get("google_maps_url") or "").strip()

                if not direccion:
                    return core_json({"ok":False,"error":"Ingresa la dirección donde atiendes clientes"},400)

                direccion_completa = ", ".join(x for x in (direccion, comuna) if x)
                if maps_url and not re.match(r"^https?://", maps_url, flags=re.IGNORECASE):
                    return core_json({"ok":False,"error":"El enlace de Google Maps debe comenzar con http:// o https://"},400)

                if not maps_url:
                    maps_url = "https://www.google.com/maps/search/?api=1&query=" + quote(
                        direccion_completa, safe=""
                    )

                datos["direccion_fisica"] = {
                    "tiene_direccion": True,
                    "direccion": direccion,
                    "comuna": comuna,
                    "referencia": referencia,
                    "direccion_completa": direccion_completa,
                    "google_maps_url": maps_url,
                }
                datos["atiende_direccion_fisica"] = True
                datos["direccion"] = direccion_completa
                datos["comuna"] = comuna
                datos["referencia_direccion"] = referencia
                datos["google_maps_url"] = maps_url

        elif key == "ecommerce_provider":
            provider = _core_norm(value)
            if provider not in {"jumpseller", "shopify", "otra plataforma", "no tengo tienda online"}:
                return core_json({"ok":False,"error":"Selecciona Jumpseller, Shopify, Otra plataforma o No tengo tienda online"},400)

            datos["ecommerce_provider"] = provider
            if provider in {"jumpseller", "shopify"}:
                # La tienda conectada será la fuente principal de catálogo/precio/stock.
                datos["catalogo_fuente"] = "ecommerce"
                datos["productos_servicios"] = ""
                datos["catalogo_productos_servicios"] = []
            else:
                datos["catalogo_fuente"] = "manual"

        elif key == "productos_servicios" and isinstance(value, (dict, list)):
            items = value.get("items") if isinstance(value, dict) else value
            if not isinstance(items, list):
                return core_json({"ok":False,"error":"Los productos o servicios deben enviarse como una lista"},400)

            catalogo = []
            for item in items[:100]:
                if not isinstance(item, dict):
                    continue
                nombre = str(item.get("nombre") or item.get("name") or "").strip()
                precio = str(item.get("precio") or item.get("price") or "").strip()
                if not nombre:
                    continue
                catalogo.append({"nombre": nombre[:180], "precio": precio[:80]})

            if not catalogo:
                return core_json({"ok":False,"error":"Agrega al menos un producto o servicio"},400)

            datos["catalogo_fuente"] = "manual"
            datos["catalogo_productos_servicios"] = catalogo
            datos[key] = "; ".join(
                f"{x['nombre']} — {x['precio']}" if x.get("precio") else x["nombre"]
                for x in catalogo
            )

        else:
            value=str(value).strip()
            if not value:return core_json({"ok":False,"error":"La respuesta está vacía"},400)
            if key=="email_contacto":
                if not _core_email_valido(value):
                    return core_json({"ok":False,"error":"Ingresa un correo válido, por ejemplo nombre@empresa.cl"},400)
                datos[key]=value.lower()
            else:
                datos[key]=value
        _core_save_session(token,{"datos":datos,"pregunta_actual":key})
        nxt=_core_next_question(datos); seq=_core_secuencia(datos); done=sum(1 for k in seq if datos.get(k) not in (None,""))
        if nxt:
            return core_json({"ok":True,"complete":False,"question":nxt,"progress":round(done/max(1,len(seq))*100),"summary":datos})
        s=_core_get_session(token); empresa_id=_core_create_company_from_session(s)
        return core_json({"ok":True,"complete":True,"empresa_id":empresa_id,"progress":100,"summary":datos,"demo":{"limite_mensajes":DEMO_LIMITE_MENSAJES_DEFAULT,"duracion_horas":DEMO_DURACION_HORAS_DEFAULT},"whatsapp":_core_whatsapp_info(empresa_id)})
    except Exception as e:
        print("CORE ANSWER ERROR:",repr(e)); return core_json({"ok":False,"error":str(e)[:300]},500)


@app.route("/core/demo/<token>/message",methods=["POST","OPTIONS"])
def core_demo_message(token):
    if request.method=="OPTIONS":
        return core_json({"ok":True},204)
    try:
        s=_core_get_session(token)
        if not s or not s.get("empresa_id"):
            return core_json({"ok":False,"error":"Tu prueba todavía no está activa"},409)

        empresa_id=str(s["empresa_id"])
        activar_por_empresa(empresa_id,canal="web",provider="nexi_core")

        body=request.get_json(silent=True) or {}
        texto=str(body.get("message") or "").strip()
        if not texto:
            return core_json({"ok":False,"error":"Falta message"},400)

        control_entrada = consumir_mensaje_entrante_demo()
        if not bool(control_entrada.get("permitido", True)):
            respuesta = mensaje_plan_finalizado(control_entrada)
            return core_json({
                "ok": True,
                "reply": respuesta,
                "plan": estado_suscripcion_empresa(empresa_id),
                "handoff_waiting": False,
                "orchestrated": False,
            })

        if _core_respuesta_no_verificable(texto):
            _core_log_message(token,empresa_id,"entrante",texto)
            respuesta=(
                "No tengo una fuente en tiempo real que me permita confirmar el estado actual "
                "de esa persona, así que no sería correcto inventarlo."
            )
        else:
            respuesta=_core_responder_demo_web(token,empresa_id,texto)

        respuesta=proteger_respuesta_publica_core(respuesta)
        es_mensaje_espera_handoff = (respuesta == 'Seguimos en contacto con el ejecutivo. Tu solicitud ya fue enviada y tus mensajes están quedando registrados para que pueda revisarlos al continuar la atención.')
        if not es_mensaje_espera_handoff:
            respuesta=preparar_mensaje_saliente_demo(respuesta)

        if respuesta:
            _core_log_message(token,empresa_id,"saliente",respuesta)

        plan=estado_suscripcion_empresa(empresa_id)
        return core_json({
            "ok":True,
            "reply":respuesta,
            "plan":plan,
            "handoff_waiting": not bool(respuesta),
            "orchestrated": True,
        })
    except Exception as e:
        print("CORE DEMO MESSAGE ERROR:",repr(e))
        return core_json({"ok":False,"error":str(e)[:300]},500)


@app.route("/core/demo/<token>/status",methods=["GET","OPTIONS"])
def core_demo_status(token):
    if request.method=="OPTIONS":return core_json({"ok":True},204)
    try:
        s=_core_get_session(token)
        if not s:return core_json({"ok":False,"error":"Prueba no encontrada"},404)
        plan=estado_suscripcion_empresa(s.get("empresa_id")) if s.get("empresa_id") else None
        return core_json({"ok":True,"session":s,"plan":plan,"whatsapp":_core_whatsapp_info(s.get("empresa_id"))})
    except Exception as e:return core_json({"ok":False,"error":str(e)[:300]},500)


# ============================================================
# TWILIO WHATSAPP WEBHOOK
# ============================================================

@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    twiml = MessagingResponse()
    try:
        to_numero = re.sub(r"\D", "", str(request.form.get("To") or TWILIO_WHATSAPP_FROM))
        # Conserva la resolución por número receptor como fallback, pero el router
        # superior decide el tenant activo de esta conversación.
        activar_por_canal("whatsapp", "twilio", to_numero)
        telefono = (request.form.get("From") or "").strip()
        texto = (request.form.get("Body") or "").strip()
        interactive_payload = router_payload_interactivo(request.form)
        texto_router = agenda_payload_a_texto(interactive_payload) if interactive_payload else texto
        # V2.3.2: cuando el usuario toca una opción de servicios/horas, Twilio
        # puede enviar en Body el texto visible del ítem y en ButtonPayload el ID real.
        # El motor de agenda entiende el número normalizado del payload; por eso
        # usamos texto_procesado para la lógica y conservamos Body solo para logs.
        payload_logico = str(interactive_payload or "").replace("\\", "").strip().lower()
        texto_procesado = (
            texto_router
            if interactive_payload and payload_logico.startswith("agenda:")
            else texto
        )
        message_id = (request.form.get("MessageSid") or "").strip()

        if interactive_payload and payload_logico.startswith("agenda:"):
            print("NEXI AGENDA PAYLOAD NORMALIZADO:", interactive_payload, "=>", texto_procesado)

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono)
        print("Body:", texto)
        if interactive_payload:
            print("ButtonPayload:", interactive_payload)
        button_text_log = str(request.form.get("ButtonText") or "").strip()
        if button_text_log:
            print("ButtonText:", button_text_log)
        # V2.4.2 diagnóstico de interacciones: algunos clientes Twilio envían
        # la selección en campos distintos o mezclada dentro de Body.
        for _k in ("ButtonPayload", "ButtonText", "ListId", "ListTitle", "InteractiveData"):
            _v = str(request.form.get(_k) or "").strip()
            if _v and _k not in {"ButtonPayload", "ButtonText"}:
                print(f"{_k}:", _v)
        print("MessageSid:", message_id)
        print("=" * 60)

        # Evita respuestas duplicadas ante reintentos de Twilio.
        if message_id:
            with PROCESADOS_LOCK:
                ahora_ts = datetime.now().timestamp()
                viejos = [k for k, ts in PROCESADOS.items() if ahora_ts - ts > 300]
                for k in viejos:
                    PROCESADOS.pop(k, None)
                if message_id in PROCESADOS:
                    return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}
                PROCESADOS[message_id] = ahora_ts

        if not telefono:
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        # V2.4.1: selección de plan ANTES de consumir cuota.
        # Soporta ButtonPayload y también ButtonText/Body como fallback.
        codigo_pago, empresa_pago = _resolver_seleccion_pago_whatsapp(request.form, telefono)
        if codigo_pago:
            if not empresa_pago:
                twiml.message("No pude identificar tu empresa para iniciar el pago. Escribe *MENU* y vuelve a entrar a tu prueba.")
                return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

            if not _pago_empresa_autorizada_whatsapp(telefono, empresa_pago):
                twiml.message("Por seguridad no pude asociar ese pago a tu empresa. Entra a Portal Nexia para continuar.")
                return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

            try:
                checkout = _crear_checkout_whatsapp(empresa_pago, codigo_pago)
                twiml.message(_mensaje_checkout_whatsapp(checkout))
                print("NEXI PAGO WHATSAPP CHECKOUT OK:", empresa_pago, codigo_pago)
            except Exception as e:
                print("NEXI PAGO WHATSAPP ERROR:", repr(e))
                twiml.message("No pude iniciar Mercado Pago en este momento. Intenta nuevamente o realiza el pago desde Portal Nexia.")
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        # V1.8: capa superior. El mismo número puede atender Diego, una demo o
        # cualquier nuevo negocio registrado en nexi_router_destinos.
        route = router_superior_resolver(telefono, texto_router)
        if route.get("accion") == "menu":
            pagina = int(route.get("pagina") or 0)
            # Como MENU acaba de llegar desde el usuario, estamos dentro de la ventana de 24 h
            # y Twilio permite list-picker sin aprobación de plantilla.
            if enviar_twilio_menu_interactivo(telefono, pagina=pagina):
                return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}
            # Fallback seguro si Content API no está disponible.
            twiml.message(route.get("respuesta") or router_menu_superior(telefono, pagina=pagina))
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if route.get("accion") == "mensaje":
            twiml.message(route.get("respuesta") or NEXIA_PRUEBA_URL)
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        router_activar_ruta(route, "twilio")

        # V2.3.1: paginación de listas interactivas de agenda.
        if interactive_payload and interactive_payload.lower().startswith("agenda:"):
            raw_agenda = interactive_payload.lower()
            m_serv = re.fullmatch(r"agenda:servicios_pagina:(\d+)", raw_agenda)
            m_hora = re.fullmatch(r"agenda:horas_pagina:(\d+)", raw_agenda)
            if m_serv or m_hora:
                if str(route.get("motor") or "core").lower() == "legacy":
                    estado_lista = get_estado(telefono)
                else:
                    empresa_lista = str(route.get("empresa_id") or empresa_actual_id() or "").strip()
                    key_lista = f"core:{empresa_lista}:{_normalizar_identificador_demo(telefono, 'whatsapp')}"
                    estado_lista = get_estado(key_lista)
                pagina_lista = int((m_serv or m_hora).group(1))
                enviado = (
                    enviar_twilio_agenda_interactiva(telefono, estado_lista, pagina_servicios=pagina_lista)
                    if m_serv
                    else enviar_twilio_agenda_interactiva(telefono, estado_lista, pagina_horas=pagina_lista)
                )
                if enviado:
                    return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if route.get("accion") == "seleccionado":
            twiml.message(router_bienvenida_contexto(route))
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if str(route.get("motor") or "core").lower() != "legacy":
            demo_access = route.get("demo_access") or {"empresa_id": route.get("empresa_id")}
            if texto_procesado:
                control_entrada = consumir_mensaje_entrante_demo()
                if not bool(control_entrada.get("permitido", True)):
                    respuesta = mensaje_plan_finalizado(control_entrada)
                else:
                    respuesta = _core_responder_demo_whatsapp(demo_access, telefono, texto_procesado)
            else:
                respuesta = router_bienvenida_contexto({**route, "telefono": telefono})
            if respuesta:
                empresa_core = str(route.get("empresa_id") or empresa_actual_id() or "").strip()
                key_core = f"core:{empresa_core}:{_normalizar_identificador_demo(telefono, 'whatsapp')}"
                estado_core = get_estado(key_core)

                if _es_fin_plan_para_pago(respuesta):
                    twiml.message(respuesta)
                    enviar_twilio_pago_interactivo(telefono, empresa_core)
                    return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

                if enviar_twilio_agenda_interactiva(telefono, estado_core):
                    return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}
                twiml.message(respuesta)
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if not texto_procesado:
            respuesta = mensaje_bienvenida()
        else:
            modo_actual = obtener_modo_atencion(telefono, "whatsapp")
            # Conservamos el texto visible en historial, pero procesamos el payload
            # normalizado cuando la entrada proviene de una lista interactiva.
            guardar_mensaje(telefono, "user", texto or texto_procesado)
            guardar_mensaje_supabase(telefono, "entrante", texto or texto_procesado)

            if modo_actual == "ejecutivo":
                print("WHATSAPP MODO EJECUTIVO: solo esta conversación queda con ejecutivo")
                return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

            estado = get_estado(telefono)
            debe_derivar = False

            if quiere_hablar_con_persona(texto_procesado):
                respuesta = mensaje_derivacion_ejecutivo()
                debe_derivar = True
            elif es_menu(texto_procesado):
                reset_estado(telefono)
                respuesta = mensaje_bienvenida()
            elif es_empresa_nexia() and pregunta_contacto_sensible(texto_procesado):
                respuesta = mensaje_contacto_no_publicado()
            elif pregunta_horarios(texto_procesado):
                respuesta = mensaje_horario_no_publicado() if es_empresa_nexia() else mensaje_horarios()
            elif negocio_es_comercial() and (
                estado.get("paso", "inicio").startswith("comercial_")
                or intencion_interes_comercial(texto_procesado)
            ):
                paso_antes = estado.get("paso")
                respuesta = procesar_comercial(estado, texto)
                if estado.get("paso") == "comercial_completo" and paso_antes != "comercial_completo":
                    debe_derivar = True
            elif negocio_usa_reservas() and estado.get("paso") != "inicio":
                respuesta = procesar_agenda(estado, texto_procesado)
            elif pregunta_servicios(texto_procesado):
                respuesta = mostrar_servicios()
            elif negocio_usa_reservas() and (
                detectar_servicio(texto_procesado)
                or corte_ambiguo(texto_procesado)
                or intencion_agendar(texto_procesado)
                or texto_menciona_fecha(texto_procesado)
            ):
                estado["paso"] = "inicio"
                respuesta = procesar_agenda(estado, texto_procesado)
            elif negocio_es_comercial() and detectar_servicio(texto_procesado):
                respuesta = (
                    f"Sí 😊 Ese servicio está disponible. "
                    f"Si te interesa contratarlo, escribe *ME INTERESA* y te hago unas preguntas breves."
                )
            else:
                # Cualquier otra cosa recibe una respuesta natural pero acotada al negocio actual.
                respuesta = respuesta_general(texto_procesado)

        # V67: controla plan/demo antes de guardar y enviar la respuesta automática.
        respuesta = preparar_mensaje_saliente_demo(respuesta)
        # Filtro final de privacidad para Nexia.
        respuesta = proteger_respuesta_publica_nexia(respuesta)
        guardar_mensaje(telefono, "assistant", respuesta)
        guardar_mensaje_supabase(
            telefono,
            "saliente",
            respuesta,
            nombre_contacto=(get_estado(telefono).get("nombre") if telefono else None),
        )
        if 'debe_derivar' in locals() and debe_derivar:
            derivar_a_ejecutivo(
                telefono,
                "whatsapp",
                estado=get_estado(telefono),
                motivo=texto,
            )
        if _es_fin_plan_para_pago(respuesta):
            twiml.message(respuesta)
            enviar_twilio_pago_interactivo(telefono, empresa_actual_id())
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if telefono and enviar_twilio_agenda_interactiva(telefono, get_estado(telefono)):
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}
        twiml.message(respuesta)
        return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

    except Exception as e:
        print("WHATSAPP ERROR:", repr(e))
        import traceback
        print(traceback.format_exc())
        twiml.message(
            f"Disculpa 🙏 Soy el asistente virtual de {cfg('asistente_nombre', DEFAULT_ASISTENTE_NOMBRE)}. "
            "Tuve un problema técnico. Intenta nuevamente en unos segundos."
        )
        return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}


# ============================================================
# TWILIO WHATSAPP - ENVÍO DESDE PORTAL NEXIA
# ============================================================

def enviar_twilio_texto(destino, texto):
    cc=cfg("canal_config",{}) or {};sid=secret_from_env(cc.get("account_sid_env"),TWILIO_ACCOUNT_SID);token=secret_from_env(cc.get("auth_token_env"),TWILIO_AUTH_TOKEN);sender=str(cc.get("sender") or TWILIO_WHATSAPP_FROM or "").strip()
    if not sid or not token or not sender:raise RuntimeError("Falta configuración Twilio de la empresa")
    cliente=TwilioClient(sid,token);destino=re.sub(r"\D","",str(destino or ""));to_value=f"whatsapp:+{destino}";from_value=sender if sender.startswith("whatsapp:") else f"whatsapp:{sender}"
    msg=cliente.messages.create(body=texto,from_=from_value,to=to_value);print("TWILIO PORTAL SEND OK:",empresa_actual_id(),to_value,getattr(msg,"sid",""));return msg


# ============================================================
# GUPSHUP WHATSAPP - ENVÍO + WEBHOOK EN PARALELO
# ============================================================

def enviar_gupshup_texto(destino, texto):
    """Envía un mensaje de sesión de texto usando la API oficial de Gupshup."""
    if not GUPSHUP_API_KEY:
        raise RuntimeError("Falta GUPSHUP_API_KEY en Render")
    if not GUPSHUP_SOURCE:
        raise RuntimeError("Falta GUPSHUP_SOURCE en Render")
    if not GUPSHUP_APP_NAME:
        raise RuntimeError("Falta GUPSHUP_APP_NAME en Render")

    destino = re.sub(r"\D", "", (destino or ""))
    source = re.sub(r"\D", "", (GUPSHUP_SOURCE or ""))
    if not destino:
        raise ValueError("Destino Gupshup vacío")

    payload = {
        "channel": "whatsapp",
        "source": source,
        "destination": destino,
        "src.name": GUPSHUP_APP_NAME,
        "message": json.dumps(
            {
                "type": "text",
                "text": texto,
                "previewUrl": False,
            },
            ensure_ascii=False,
        ),
    }
    headers = {
        "apikey": GUPSHUP_API_KEY,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    r = requests.post(
        GUPSHUP_API_URL,
        headers=headers,
        data=payload,
        timeout=20,
    )

    print("GUPSHUP SEND STATUS:", r.status_code)
    print("GUPSHUP SEND RESPONSE:", r.text[:2000])

    # Gupshup puede devolver JSON de error incluso con cuerpo legible.
    if not r.ok:
        raise RuntimeError(f"Gupshup HTTP {r.status_code}: {r.text[:1000]}")

    try:
        data = r.json()
    except Exception:
        data = {}

    if isinstance(data, dict) and data.get("status") == "error":
        raise RuntimeError(f"Gupshup error: {data}")

    return data


@app.route("/gupshup/webhook", methods=["POST"])
def gupshup_webhook():
    try:
        data = request.get_json(silent=True) or {}
        activar_por_canal("whatsapp", "gupshup", str(data.get("app") or GUPSHUP_APP_NAME))

        print("=" * 60)
        print("GUPSHUP WEBHOOK")
        print(json.dumps(data, ensure_ascii=False))
        print("=" * 60)

        # Gupshup también envía user-events y otros eventos al callback.
        # Solo procesamos mensajes entrantes de usuario.
        if data.get("type") != "message":
            return "OK", 200

        payload = data.get("payload") or {}
        message_id = (payload.get("id") or "").strip()
        telefono = str(payload.get("source") or (payload.get("sender") or {}).get("phone") or "").strip()
        tipo = (payload.get("type") or "").strip().lower()
        contenido = payload.get("payload") or {}

        if tipo == "text":
            texto = (contenido.get("text") or "").strip()
        else:
            texto = ""

        print("GUPSHUP FROM:", telefono)
        print("GUPSHUP TYPE:", tipo)
        print("GUPSHUP BODY:", texto)
        print("GUPSHUP MESSAGE ID:", message_id)

        # Evita respuestas duplicadas ante reintentos del webhook.
        if message_id:
            with PROCESADOS_LOCK:
                ahora_ts = datetime.now().timestamp()
                viejos = [k for k, ts in PROCESADOS.items() if ahora_ts - ts > 300]
                for k in viejos:
                    PROCESADOS.pop(k, None)
                if message_id in PROCESADOS:
                    return "OK", 200
                PROCESADOS[message_id] = ahora_ts

        if not telefono:
            return "OK", 200

        # V1.8: recepción superior compartida para Diego, demos y nuevos negocios.
        if tipo == "text":
            route = router_superior_resolver(telefono, texto)
            if route.get("accion") == "menu":
                enviar_gupshup_texto(telefono, route.get("respuesta") or router_menu_superior(telefono))
                return "OK", 200

            if route.get("accion") == "mensaje":
                enviar_gupshup_texto(telefono, route.get("respuesta") or NEXIA_PRUEBA_URL)
                return "OK", 200

            router_activar_ruta(route, "gupshup")

            if route.get("accion") == "seleccionado":
                enviar_gupshup_texto(telefono, router_bienvenida_contexto(route))
                return "OK", 200

            if str(route.get("motor") or "core").lower() != "legacy":
                demo_access = route.get("demo_access") or {"empresa_id": route.get("empresa_id")}
                control_entrada = consumir_mensaje_entrante_demo()
                if not bool(control_entrada.get("permitido", True)):
                    respuesta = mensaje_plan_finalizado(control_entrada)
                else:
                    respuesta = _core_responder_demo_whatsapp(demo_access, telefono, texto)
                enviar_gupshup_texto(telefono, respuesta)
                return "OK", 200

        # Para multimedia conservamos el tenant activo del router. Si todavía no
        # hay contexto, mostramos recepción en lugar de caer accidentalmente en Diego.
        if tipo != "text":
            route = router_superior_resolver(telefono, "")
            if route.get("accion") == "menu":
                enviar_gupshup_texto(telefono, route.get("respuesta") or router_menu_superior(telefono))
                return "OK", 200
            router_activar_ruta(route, "gupshup")
            respuesta = (
                "Por ahora puedo ayudarte por texto 😊. "
                + ("Escríbeme tu consulta, servicio o la fecha en que quieres agendar."
                   if negocio_usa_reservas()
                   else "Escríbeme tu consulta o el servicio que te interesa.")
            )
        elif not texto:
            respuesta = mensaje_bienvenida()
        else:
            modo_actual = obtener_modo_atencion(telefono, "whatsapp")
            guardar_mensaje(telefono, "user", texto)
            guardar_mensaje_supabase(telefono, "entrante", texto)

            if modo_actual == "ejecutivo":
                print("GUPSHUP MODO EJECUTIVO: solo esta conversación queda con ejecutivo")
                return "OK", 200

            estado = get_estado(telefono)
            debe_derivar = False

            if quiere_hablar_con_persona(texto):
                respuesta = mensaje_derivacion_ejecutivo()
                debe_derivar = True
            elif es_menu(texto):
                reset_estado(telefono)
                respuesta = mensaje_bienvenida()
            elif es_empresa_nexia() and pregunta_contacto_sensible(texto):
                respuesta = mensaje_contacto_no_publicado()
            elif pregunta_horarios(texto):
                respuesta = mensaje_horario_no_publicado() if es_empresa_nexia() else mensaje_horarios()
            elif negocio_es_comercial() and (
                estado.get("paso", "inicio").startswith("comercial_")
                or intencion_interes_comercial(texto)
            ):
                paso_antes = estado.get("paso")
                respuesta = procesar_comercial(estado, texto)
                if estado.get("paso") == "comercial_completo" and paso_antes != "comercial_completo":
                    debe_derivar = True
            elif negocio_usa_reservas() and estado.get("paso") != "inicio":
                respuesta = procesar_agenda(estado, texto)
            elif pregunta_servicios(texto):
                respuesta = mostrar_servicios()
            elif negocio_usa_reservas() and (
                detectar_servicio(texto)
                or corte_ambiguo(texto)
                or intencion_agendar(texto)
                or texto_menciona_fecha(texto)
            ):
                estado["paso"] = "inicio"
                respuesta = procesar_agenda(estado, texto)
            elif negocio_es_comercial() and detectar_servicio(texto):
                respuesta = (
                    "Sí 😊 Ese servicio está disponible. "
                    "Si te interesa contratarlo, escribe *ME INTERESA* y te hago unas preguntas breves."
                )
            else:
                respuesta = respuesta_general(texto)

        # V67: controla plan/demo antes de guardar y enviar la respuesta automática.
        respuesta = preparar_mensaje_saliente_demo(respuesta)
        # Filtro final de privacidad para Nexia.
        respuesta = proteger_respuesta_publica_nexia(respuesta)
        guardar_mensaje(telefono, "assistant", respuesta)
        guardar_mensaje_supabase(
            telefono,
            "saliente",
            respuesta,
            nombre_contacto=(get_estado(telefono).get("nombre") if telefono else None),
        )
        if 'debe_derivar' in locals() and debe_derivar:
            derivar_a_ejecutivo(
                telefono,
                "whatsapp",
                estado=get_estado(telefono),
                motivo=texto,
            )
        enviar_gupshup_texto(telefono, respuesta)
        return "OK", 200

    except Exception as e:
        print("GUPSHUP ERROR:", repr(e))
        import traceback
        print(traceback.format_exc())

        # Devolvemos 200 para evitar una tormenta de reintentos del webhook.
        # El detalle del fallo queda registrado en Render.
        return "OK", 200



# ============================================================
# INSTAGRAM MESSAGING API - META DIRECTO
# ============================================================

def instagram_url(path):
    """Construye una URL de Instagram Graph API usando una versión configurable."""
    version = (INSTAGRAM_GRAPH_VERSION or "").strip().strip("/")
    path = "/" + (path or "").lstrip("/")
    if version:
        return f"{INSTAGRAM_API_BASE}/{version}{path}"
    return f"{INSTAGRAM_API_BASE}{path}"


def verificar_firma_instagram(raw_body):
    """
    Valida X-Hub-Signature-256 cuando INSTAGRAM_APP_SECRET está configurado.
    Si todavía no se configuró el secret, no bloquea el webhook para facilitar
    la puesta en marcha inicial.
    """
    if not INSTAGRAM_APP_SECRET:
        return True

    firma = (request.headers.get("X-Hub-Signature-256") or "").strip()
    if not firma.startswith("sha256="):
        return False

    esperado = "sha256=" + hmac.new(
        INSTAGRAM_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(firma, esperado)


def enviar_instagram_texto(destino_igsid, texto):
    cc=cfg("canal_config",{}) or {};token=secret_from_env(cc.get("access_token_env"),INSTAGRAM_ACCESS_TOKEN);account_id=str(cc.get("sender") or cc.get("identificador_externo") or INSTAGRAM_USER_ID or "").strip()
    if not token or not account_id:raise RuntimeError("Falta configuración Instagram de la empresa")
    url=instagram_url(f"{account_id}/messages");headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"};payload={"recipient":{"id":str(destino_igsid)},"message":{"text":str(texto)}};r=requests.post(url,headers=headers,json=payload,timeout=20);print("INSTAGRAM SEND STATUS:",r.status_code);print("INSTAGRAM SEND RESPONSE:",r.text[:1000]);r.raise_for_status();return True


def recuperar_mensaje_instagram_por_mid(mid):
    """
    Intenta recuperar detalles del mensaje usando el MID recibido en message_edit.

    Nota:
    - Meta no siempre permite consultar directamente el objeto Message por MID.
    - Por eso esta función prueba varias rutas compatibles y deja logs claros.
    - Si ninguna funciona, devuelve None sin botar el webhook.
    """
    if not INSTAGRAM_ACCESS_TOKEN or not mid:
        return None

    headers = {
        "Authorization": f"Bearer {INSTAGRAM_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    intentos = []

    # Intento 1: consultar directamente el MID como objeto Graph.
    intentos.append(
        (
            "direct_mid",
            instagram_url(str(mid)),
            {"fields": "id,message,from,to,created_time"},
        )
    )

    # Intento 2: consultar vía graph.facebook.com sin prefijo de cuenta.
    # Algunas respuestas de Meta usan objetos compatibles con Graph general.
    graph_version = (INSTAGRAM_GRAPH_VERSION or "").strip().strip("/")
    direct_fb_url = f"https://graph.facebook.com/{graph_version}/{mid}" if graph_version else f"https://graph.facebook.com/{mid}"
    intentos.append(
        (
            "facebook_graph_mid",
            direct_fb_url,
            {"fields": "id,message,from,to,created_time"},
        )
    )

    for etiqueta, url, params in intentos:
        try:
            r = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=20,
            )
            print(f"INSTAGRAM MID LOOKUP [{etiqueta}] STATUS:", r.status_code)
            print(f"INSTAGRAM MID LOOKUP [{etiqueta}] RESPONSE:", r.text[:4000])

            if not r.ok:
                continue

            data = r.json() if r.content else {}
            if not isinstance(data, dict):
                continue

            texto = (
                data.get("message")
                or data.get("text")
                or ((data.get("message") or {}).get("text") if isinstance(data.get("message"), dict) else None)
            )

            sender_id = None
            origen = data.get("from")
            if isinstance(origen, dict):
                sender_id = origen.get("id")

            if texto or sender_id:
                username = ""
                if isinstance(origen, dict):
                    username = str(origen.get("username") or "").strip()

                return {
                    "ok": True,
                    "texto": str(texto or "").strip(),
                    "sender_id": str(sender_id or "").strip(),
                    "username": username,
                    "data": data,
                    "fuente": etiqueta,
                }

        except Exception as e:
            print(f"INSTAGRAM MID LOOKUP [{etiqueta}] ERROR:", repr(e))

    return None


def intentar_recuperar_desde_message_edit(entry_id, evento):
    """
    Procesa el extraño evento message_edit que Meta está enviando para mensajes nuevos.
    Devuelve dict con sender_id/texto si logra recuperar algo.
    """
    edit = evento.get("message_edit") or {}
    mid = str(edit.get("mid") or "").strip()
    num_edit = edit.get("num_edit")

    print("INSTAGRAM MESSAGE_EDIT DETECTADO")
    print("INSTAGRAM MESSAGE_EDIT MID:", mid)
    print("INSTAGRAM MESSAGE_EDIT NUM_EDIT:", num_edit)
    print("INSTAGRAM ENTRY ID:", entry_id)

    if not mid:
        return None

    recuperado = recuperar_mensaje_instagram_por_mid(mid)
    if recuperado:
        print("INSTAGRAM MESSAGE_EDIT RECUPERADO:", recuperado.get("fuente"))
        print("INSTAGRAM MESSAGE_EDIT SENDER:", recuperado.get("sender_id"))
        print("INSTAGRAM MESSAGE_EDIT USERNAME:", recuperado.get("username"))
        print("INSTAGRAM MESSAGE_EDIT TEXTO:", recuperado.get("texto"))
        return recuperado

    print("INSTAGRAM MESSAGE_EDIT: no fue posible recuperar el mensaje por MID")
    return None


def _core_responder_instagram(empresa_id, cliente_id, texto, username=None):
    """
    Instagram usa el mismo Nexia Core que WhatsApp:
    - mismo empresa_id
    - mismo orquestador/agentes
    - agenda real
    - ecommerce
    - handoff
    - Portal/Supabase
    El canal y la sesión permanecen separados.
    """
    empresa_id = str(empresa_id or "").strip()
    cliente_id = str(cliente_id or "").strip()
    if not empresa_id:
        raise RuntimeError("Instagram sin empresa_id activo")
    if not cliente_id:
        raise RuntimeError("Instagram sin cliente_id")

    activar_por_empresa(empresa_id, canal="instagram", provider="meta")

    token = _core_token_por_empresa(empresa_id)
    perfil = _core_profile(empresa_id) or {}
    datos_perfil = perfil.get("datos") or {}
    tipo = _core_tipo(datos_perfil.get("tipo_cliente")) or "personal"

    username = str(username or "").strip().lstrip("@")
    nombre_instagram = f"@{username}" if username else None
    session_key = f"core:{empresa_id}:instagram:{cliente_id}"

    # MENU y selección de negocio se resuelven antes, en el router omnicanal.

    # Historial local + Portal.
    guardar_mensaje(session_key, "user", texto, canal="instagram")
    guardar_mensaje_supabase(
        cliente_id,
        "entrante",
        texto,
        nombre_contacto=nombre_instagram,
        canal="instagram",
    )

    if token:
        _core_log_message(token, empresa_id, "entrante", texto)

    # Handoff persistente por canal Instagram.
    hs = _core_handoff_lookup(empresa_id, cliente_id, "instagram")
    estado_handoff = str((hs or {}).get("estado") or "").strip().lower()

    # Agenda real usa una sesión independiente de WhatsApp.
    agenda_estado = get_estado(session_key)
    agenda_activa = str(agenda_estado.get("paso") or "inicio") != "inicio"

    if agenda_activa and pregunta_servicios(texto):
        agenda_estado["paso"] = "servicio"
        agenda_estado["servicio"] = None
        agenda_estado["fecha_hora"] = None
        agenda_estado["horas_ofrecidas"] = []
        agenda_activa = True

    agente_previsto = _core_route_intent(texto, datos_perfil)
    agenda_solicitada = agente_previsto == "agenda"

    # Agenda tiene prioridad sobre handoff aún no ejecutado.
    if estado_handoff in {"recolectando", "ofrecido"} and (agenda_solicitada or agenda_activa):
        _core_handoff_upsert(
            empresa_id,
            cliente_id,
            "instagram",
            {
                "estado":"cerrado",
                "datos":{
                    **((hs or {}).get("datos") or {}),
                    "cierre":"interrumpido_por_agenda",
                },
                "started_at":(hs or {}).get("started_at") or datetime.now(pytz.UTC).isoformat(),
            },
        )
        estado_handoff = "cerrado"

    respuesta = None
    agente_usado = None

    if estado_handoff == "derivado":
        if not _core_handoff_expirado(hs):
            respuesta = (
                "Seguimos en contacto con el ejecutivo. "
                "Tu solicitud ya fue enviada y tus mensajes están quedando registrados."
            )
        else:
            _core_handoff_upsert(
                empresa_id,
                cliente_id,
                "instagram",
                {
                    "estado":"cerrado",
                    "datos":{
                        **(hs.get("datos") or {}),
                        "cierre":"timeout",
                        "timeout_minutos":CORE_HANDOFF_TIMEOUT_MINUTOS,
                    },
                    "started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            respuesta = (
                f"No hemos podido conectarte con una persona dentro de los "
                f"{CORE_HANDOFF_TIMEOUT_MINUTOS} minutos estimados. "
                "Puedo seguir ayudándote por aquí.\n\n"
                + _core_orchestrate(
                    empresa_id,
                    texto,
                    token=token,
                    canal="instagram",
                )[0]
            )

    elif estado_handoff == "recolectando":
        if _core_cancelar_handoff_texto(texto):
            _core_handoff_upsert(
                empresa_id,
                cliente_id,
                "instagram",
                {
                    "estado":"cerrado",
                    "datos":{
                        **(hs.get("datos") or {}),
                        "cierre":"cancelado_usuario",
                    },
                    "started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            respuesta = "Perfecto, cancelé la derivación. Seguimos con el asistente 😊 ¿En qué te puedo ayudar?"
        else:
            detalles = _core_parse_handoff_details(tipo, texto)
            motivo = str(detalles.get("motivo") or "").strip()
            nombre = str(detalles.get("nombre") or "").strip()

            if not nombre or not motivo:
                respuesta = _core_handoff_prompt(tipo)
            else:
                _core_handoff_request(
                    empresa_id,
                    cliente_id,
                    "instagram",
                    detalles,
                    motivo,
                )
                _core_handoff_upsert(
                    empresa_id,
                    cliente_id,
                    "instagram",
                    {
                        "estado":"derivado",
                        "datos":detalles,
                        "started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                    },
                )
                respuesta = (
                    f"Gracias, {nombre} 🙌\n\n"
                    "Ya registré tu solicitud y estamos en contacto con el ejecutivo. "
                    f"El tiempo estimado de atención es de hasta {CORE_HANDOFF_TIMEOUT_MINUTOS} minutos."
                )

    elif estado_handoff == "ofrecido":
        if _core_es_confirmacion(texto):
            _core_handoff_upsert(
                empresa_id,
                cliente_id,
                "instagram",
                {
                    "estado":"recolectando",
                    "datos":{},
                    "started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            respuesta = _core_handoff_prompt(tipo)
        elif _core_es_rechazo(texto):
            _core_handoff_upsert(
                empresa_id,
                cliente_id,
                "instagram",
                {
                    "estado":"cerrado",
                    "datos":hs.get("datos") or {},
                    "started_at":hs.get("started_at") or datetime.now(pytz.UTC).isoformat(),
                },
            )
            respuesta = "Perfecto. Seguimos por aquí 😊 ¿En qué más te puedo ayudar?"
        else:
            respuesta = (
                "Tengo pendiente tu solicitud de hablar con una persona. "
                "Si quieres continuar, responde sí; si prefieres seguir con el asistente, responde no."
            )

    elif _core_es_handoff(texto) and _core_si(datos_perfil.get("handoff")):
        _core_handoff_upsert(
            empresa_id,
            cliente_id,
            "instagram",
            {
                "estado":"recolectando",
                "datos":{"solicitud_original":str(texto or "")},
                "started_at":datetime.now(pytz.UTC).isoformat(),
            },
        )
        respuesta = _core_handoff_prompt(tipo)

    elif _core_respuesta_no_verificable(texto):
        respuesta = (
            "No tengo una fuente en tiempo real que me permita confirmar ese estado actual. "
            "Puedo ayudarte con la información verificada disponible."
        )

    else:
        # Agenda real: misma configuración de empresa, distinto canal.
        if negocio_tiene_calendar_real() and (agente_previsto == "agenda" or agenda_activa):
            # El flujo estándar usa un identificador de sesión. Para Instagram
            # usamos un prefijo estable y evitamos mezclarlo con WhatsApp.
            respuesta = _core_procesar_agenda_estandar(
                empresa_id,
                f"instagram:{cliente_id}",
                texto,
                datos_perfil,
            )
            agente_usado = "agenda"
            print(
                "NEXI CORE INSTAGRAM AGENDA:",
                empresa_id,
                google_calendar_id_actual(),
                "paso=",
                get_estado(session_key).get("paso"),
            )
        elif agente_previsto == "agenda":
            respuesta = (
                "📅 Muy pronto podrás agendar directamente desde aquí.\n"
                "En breve integraremos el calendario a nuestros servicios para habilitar la reserva online."
            )
            agente_usado = "agenda"
        else:
            # Aquí entran Atención, Conocimiento, Ventas, Soporte,
            # Seguimiento y Ecommerce usando exactamente el mismo Core.
            respuesta, agente_usado = _core_orchestrate(
                empresa_id,
                texto,
                token=token,
                canal="instagram",
            )

        # Si el Core ofrece handoff, preservamos el contexto.
        nr = normalizar_texto(respuesta)
        if _core_si(datos_perfil.get("handoff")) and any(
            x in nr
            for x in (
                "puedo derivarte",
                "quieres que te derive",
                "puedo ponerte en contacto",
                "quieres hablar con una persona",
                "derivarte internamente",
            )
        ):
            _core_handoff_upsert(
                empresa_id,
                cliente_id,
                "instagram",
                {
                    "estado":"ofrecido",
                    "datos":{"respuesta_oferta":respuesta},
                    "started_at":datetime.now(pytz.UTC).isoformat(),
                },
            )

    respuesta = proteger_respuesta_publica_core(respuesta)

    es_espera_handoff = respuesta == (
        "Seguimos en contacto con el ejecutivo. "
        "Tu solicitud ya fue enviada y tus mensajes están quedando registrados."
    )
    if not es_espera_handoff:
        respuesta = preparar_mensaje_saliente_demo(respuesta)

    guardar_mensaje(session_key, "assistant", respuesta, canal="instagram")
    guardar_mensaje_supabase(
        cliente_id,
        "saliente",
        respuesta,
        nombre_contacto=nombre_instagram,
        canal="instagram",
    )

    if token and respuesta:
        _core_log_message(token, empresa_id, "saliente", respuesta)

    print(
        "NEXI CORE INSTAGRAM:",
        empresa_id,
        cliente_id,
        "agent=",
        agente_usado or agente_previsto,
    )
    return respuesta


def procesar_texto_instagram(cliente_id, texto, username=None):
    """
    Punto de compatibilidad del webhook existente.
    Mantiene la misma URL/webhook de Meta y entrega el mensaje al Nexia Core
    de la empresa activada por entry_id.
    """
    empresa_id = str(empresa_actual_id() or "").strip()
    if not empresa_id:
        raise RuntimeError("No fue posible resolver la empresa para Instagram")

    return _core_responder_instagram(
        empresa_id,
        cliente_id,
        texto,
        username=username,
    )


@app.route("/instagram/webhook", methods=["GET"])
def instagram_webhook_verificacion():
    """
    Verificación inicial que hace Meta.
    En Meta configura:
      URL: https://TU-SERVICIO.onrender.com/instagram/webhook
      Verify token: el mismo valor de INSTAGRAM_VERIFY_TOKEN
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    print("INSTAGRAM VERIFY:", mode, "challenge:", challenge)

    if mode == "subscribe" and token == INSTAGRAM_VERIFY_TOKEN:
        return str(challenge or ""), 200

    print("INSTAGRAM VERIFY ERROR: token o modo inválido")
    return "Forbidden", 403


@app.route("/instagram/webhook", methods=["POST"])
def instagram_webhook_eventos():
    """
    Recibe eventos de Instagram. Procesa DMs de texto y responde por Meta.
    Ignora ecos del propio bot y eventos que no sean mensajes de usuario.
    """
    try:
        raw_body = request.get_data(cache=True)

        if not verificar_firma_instagram(raw_body):
            print("INSTAGRAM ERROR: firma X-Hub-Signature-256 inválida")
            return "Forbidden", 403

        data = request.get_json(silent=True) or {}

        print("=" * 60)
        print("INSTAGRAM WEBHOOK")
        print(json.dumps(data, ensure_ascii=False)[:10000])
        print("=" * 60)

        if data.get("object") != "instagram":
            return "OK", 200

        for entry in data.get("entry") or []:
            entry_id = str(entry.get("id") or "").strip()
            activar_por_canal("instagram", "meta", entry_id)
            for evento in entry.get("messaging") or []:
                message = evento.get("message") or {}

                # Caso normal documentado por Meta.
                username = ""

                if message:
                    # Ignorar mensajes enviados por nuestra propia app.
                    if message.get("is_echo"):
                        continue

                    message_id = str(message.get("mid") or "").strip()
                    sender_id = str((evento.get("sender") or {}).get("id") or "").strip()
                    texto = str(message.get("text") or "").strip()

                    # El webhook normal no siempre incluye username.
                    # El MID sí lo está devolviendo en Meta, por eso lo consultamos.
                    if message_id:
                        perfil_mid = recuperar_mensaje_instagram_por_mid(message_id)
                        if perfil_mid:
                            username = str(perfil_mid.get("username") or "").strip()

                # Fallback para el comportamiento observado en Instagram API v26:
                # Meta está enviando message_edit incluso para mensajes nuevos.
                elif evento.get("message_edit"):
                    message_id = str((evento.get("message_edit") or {}).get("mid") or "").strip()
                    sender_id = ""
                    texto = ""

                    recuperado = intentar_recuperar_desde_message_edit(
                        str(entry.get("id") or ""),
                        evento,
                    )
                    if recuperado:
                        sender_id = str(recuperado.get("sender_id") or "").strip()
                        username = str(recuperado.get("username") or "").strip()
                        texto = str(recuperado.get("texto") or "").strip()

                else:
                    # Reacciones, seen, postbacks u otros eventos no conversacionales.
                    continue

                print("INSTAGRAM FROM:", sender_id)
                print("INSTAGRAM USERNAME:", username)
                print("INSTAGRAM MESSAGE ID:", message_id)
                print("INSTAGRAM BODY:", texto)

                if sender_id and str(sender_id) == str((cfg("canal_config", {}) or {}).get("identificador_externo") or INSTAGRAM_USER_ID):
                    print("INSTAGRAM EVENTO IGNORADO: sender es la propia cuenta del negocio")
                    continue

                if not sender_id:
                    print("INSTAGRAM EVENTO IGNORADO: no hay sender_id recuperable")
                    continue

                # Evitar respuestas duplicadas ante reintentos de Meta.
                if message_id:
                    clave_procesado = f"instagram:{message_id}"
                    with PROCESADOS_LOCK:
                        ahora_ts = datetime.now().timestamp()
                        viejos = [k for k, ts in PROCESADOS.items() if ahora_ts - ts > 300]
                        for k in viejos:
                            PROCESADOS.pop(k, None)
                        if clave_procesado in PROCESADOS:
                            continue
                        PROCESADOS[clave_procesado] = ahora_ts

                if not texto:
                    respuesta = (
                        "Por ahora puedo ayudarte por texto 😊. "
                        "Escríbeme tu consulta, servicio o la fecha en que quieres agendar."
                    )
                    guardar_mensaje(f"instagram:{sender_id}", "assistant", respuesta, canal="instagram")
                    guardar_mensaje_supabase(
                        sender_id,
                        "saliente",
                        respuesta,
                        nombre_contacto=(f"@{username.lstrip('@')}" if username else None),
                        canal="instagram",
                    )
                    enviar_instagram_texto(sender_id, respuesta)
                    continue

                try:
                    # ------------------------------------------------------------
                    # NEXIA V3.4: MISMA RECEPCIÓN/ROUTER EN WHATSAPP E INSTAGRAM
                    # ------------------------------------------------------------
                    route = router_instagram_resolver(sender_id, texto)

                    if route.get("accion") == "menu":
                        respuesta_menu = str(route.get("respuesta") or router_menu_instagram(sender_id))
                        guardar_mensaje(f"instagram:{sender_id}", "user", texto, canal="instagram")
                        guardar_mensaje_supabase(
                            sender_id,
                            "entrante",
                            texto,
                            nombre_contacto=(f"@{username.lstrip('@')}" if username else None),
                            canal="instagram",
                        )
                        guardar_mensaje(f"instagram:{sender_id}", "assistant", respuesta_menu, canal="instagram")
                        guardar_mensaje_supabase(
                            sender_id,
                            "saliente",
                            respuesta_menu,
                            nombre_contacto=(f"@{username.lstrip('@')}" if username else None),
                            canal="instagram",
                        )
                        enviar_instagram_texto(sender_id, respuesta_menu)
                        continue

                    if route.get("accion") == "mensaje":
                        respuesta_directa = str(route.get("respuesta") or "").strip()
                        if respuesta_directa:
                            enviar_instagram_texto(sender_id, respuesta_directa)
                        continue

                    if route.get("accion") in {"seleccionado", "ruta"}:
                        empresa_route = str(route.get("empresa_id") or "").strip()
                        motor_route = str(route.get("motor") or "core").strip().lower()

                        if not empresa_route:
                            enviar_instagram_texto(sender_id, router_menu_instagram(sender_id))
                            continue

                        # Instagram usa el mismo Nexia Core. Para destinos legacy,
                        # activamos la empresa y respondemos con Core si existe perfil.
                        activar_por_empresa(empresa_route, canal="instagram", provider="meta")

                        if route.get("accion") == "seleccionado":
                            try:
                                cfg_sel = cargar_empresa_config(
                                    empresa_route,
                                    canal="instagram",
                                    provider="meta",
                                )
                                nombre_sel = str(cfg_sel.get("empresa_nombre") or "el negocio").strip()
                            except Exception:
                                nombre_sel = "el negocio"

                            bienvenida = (
                                f"Listo 🙌 Estás conversando con {nombre_sel}.\n"
                                "Puedes escribir tu consulta normalmente."
                            )
                            enviar_instagram_texto(sender_id, bienvenida)

                        # Desde aquí, misma lógica de negocio que WhatsApp/Nexia Core.
                        respuesta = _core_responder_instagram(
                            empresa_route,
                            sender_id,
                            texto if route.get("accion") == "ruta" else "hola",
                            username=username,
                        )
                        if respuesta:
                            enviar_instagram_texto(sender_id, respuesta)
                        continue

                    if obtener_modo_atencion(sender_id, "instagram") == "ejecutivo":
                        nombre_instagram = (
                            f"@{username.lstrip('@')}" if username else None
                        )
                        guardar_mensaje(
                            f"instagram:{sender_id}",
                            "user",
                            texto,
                            canal="instagram",
                        )
                        guardar_mensaje_supabase(
                            sender_id,
                            "entrante",
                            texto,
                            nombre_contacto=nombre_instagram,
                            canal="instagram",
                        )
                        print("INSTAGRAM MODO EJECUTIVO: bot no responde")
                        continue

                    respuesta = procesar_texto_instagram(
                        sender_id,
                        texto,
                        username=username,
                    )
                    enviar_instagram_texto(sender_id, respuesta)
                except Exception as e:
                    print("INSTAGRAM PROCESAR/ENVIAR ERROR:", repr(e))
                    import traceback
                    print(traceback.format_exc())

        # Meta espera una respuesta rápida 200 para no reintentar.
        return "EVENT_RECEIVED", 200

    except Exception as e:
        print("INSTAGRAM WEBHOOK ERROR:", repr(e))
        import traceback
        print(traceback.format_exc())
        # Evitamos tormenta de reintentos; el error queda en Render.
        return "EVENT_RECEIVED", 200



@app.route("/instagram/diagnostico", methods=["GET"])
def instagram_diagnostico():
    """
    Diagnóstico seguro: confirma qué variables existen sin exponer secretos.
    """
    return {
        "ok": True,
        "version": APP_VERSION,
        "instagram_access_token_configurado": bool(INSTAGRAM_ACCESS_TOKEN),
        "instagram_verify_token_configurado": bool(INSTAGRAM_VERIFY_TOKEN),
        "instagram_app_secret_configurado": bool(INSTAGRAM_APP_SECRET),
        "instagram_user_id": INSTAGRAM_USER_ID,
        "instagram_empresa_id_configurado": INSTAGRAM_EMPRESA_ID or None,
        "instagram_graph_version": INSTAGRAM_GRAPH_VERSION,
        "webhook": "/instagram/webhook",
    }, 200


@app.route("/instagram/diagnostico-ruta", methods=["GET"])
def instagram_diagnostico_ruta():
    entry_id = str(request.args.get("entry_id") or INSTAGRAM_USER_ID or "").strip()
    try:
        tenant = resolver_tenant("instagram","meta",entry_id)
        return {
            "ok":True,
            "entry_id":entry_id,
            "empresa_id":tenant.get("empresa_id"),
            "empresa_nombre":tenant.get("empresa_nombre"),
            "provider":tenant.get("provider"),
            "canal":tenant.get("canal"),
        },200
    except Exception as e:
        return {
            "ok":False,
            "entry_id":entry_id,
            "error":str(e),
        },409



@app.route("/instagram/diagnostico-router", methods=["GET"])
def instagram_diagnostico_router():
    sender_id = str(request.args.get("sender_id") or "").strip()
    if not sender_id:
        return {"ok":False,"error":"Falta sender_id"},400
    sesion = router_contexto_obtener(sender_id, canal="instagram")
    opciones = router_opciones_instagram(sender_id)
    return {
        "ok":True,
        "sender_id":sender_id,
        "sesion":sesion,
        "opciones":[
            {
                "n":i+1,
                "id":op.get("id"),
                "item":op.get("item"),
                "empresa_id":op.get("empresa_id"),
                "motor":op.get("motor"),
            }
            for i,op in enumerate(opciones)
        ],
    },200



# ============================================================
# HEALTHCHECK
# ============================================================


def portal_cors_response(response):
    # Permite el dominio principal con y sin www, además del PORTAL_ORIGIN configurado.
    # Esto evita el "Failed to fetch" del navegador cuando el portal está servido
    # desde https://nexia-tech.com pero Render tenía configurado otro host/origen.
    request_origin = str(request.headers.get("Origin") or "").rstrip("/")
    allowed_origins = {
        str(PORTAL_ORIGIN or "").rstrip("/"),
        "https://nexia-tech.com",
        "https://www.nexia-tech.com",
    }
    allowed_origins.discard("")
    if request_origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = request_origin
    elif not request_origin:
        # Requests servidor-a-servidor / pruebas directas.
        response.headers["Access-Control-Allow-Origin"] = str(PORTAL_ORIGIN or "https://nexia-tech.com").rstrip("/")
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Demo-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Vary"] = "Origin"
    return response


def portal_json(payload, status=200):
    from flask import jsonify
    response = jsonify(payload)
    return portal_cors_response(response), status


def portal_usuario_autorizado():
    """
    Valida acceso al Portal. Soporta:
    1) sesión normal Supabase Auth (correo + contraseña);
    2) acceso temporal de prueba sin contraseña mediante X-Demo-Token.

    El token temporal corresponde al token aleatorio del onboarding y solo es
    válido mientras la empresa tenga una demo activa.
    """
    demo_token = str(request.headers.get("X-Demo-Token") or "").strip()
    if demo_token:
        try:
            sesion = _core_get_session(demo_token)
            if not sesion or not sesion.get("completado") or not sesion.get("empresa_id"):
                print("PORTAL DEMO AUTH: token inválido o onboarding incompleto")
                return None
            empresa_id = str(sesion.get("empresa_id") or "").strip()
            plan = estado_suscripcion_empresa(empresa_id)
            tipo_plan = str(plan.get("tipo_plan") or "").lower()
            estado_plan = str(plan.get("estado") or "").lower()
            acceso_conversion = False
            if tipo_plan != "demo":
                # Después de pagar, conservar temporalmente el acceso sin clave solo
                # para que el cliente pueda crear su contraseña del Portal.
                try:
                    hp = backend_headers()
                    rp = requests.get(
                        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
                        headers=hp,
                        params={
                            "select":"id,approved_at,setup_completed,status",
                            "empresa_id":f"eq.{empresa_id}",
                            "status":"eq.approved",
                            "setup_completed":"eq.false",
                            "order":"approved_at.desc",
                            "limit":"1",
                        },
                        timeout=SUPABASE_TIMEOUT,
                    )
                    rows_p = rp.json() if rp.ok and rp.content else []
                    if rows_p:
                        ap = _parse_iso(rows_p[0].get("approved_at"))
                        acceso_conversion = bool(ap and datetime.now(pytz.UTC) - ap.astimezone(pytz.UTC) <= timedelta(hours=24))
                except Exception as e:
                    print("PORTAL CONVERSION AUTH SKIP:", repr(e))
                if not acceso_conversion:
                    print("PORTAL DEMO AUTH: prueba no activa", empresa_id)
                    return None
            elif estado_plan != "activo":
                print("PORTAL DEMO AUTH: prueba no activa", empresa_id)
                return None
            if tipo_plan == "demo":
                fin = _parse_iso(plan.get("demo_fin"))
                if fin and datetime.now(pytz.UTC) >= fin.astimezone(pytz.UTC):
                    print("PORTAL DEMO AUTH: prueba vencida", empresa_id)
                    return None
            datos = sesion.get("datos") or {}

            # V3.4.9: sincronización perezosa de Supabase Auth.
            # Esto cubre demos antiguas creadas antes de V3.4.8 y también
            # repara casos donde la creación inicial de Auth falló.
            email_demo = str(datos.get("email_contacto") or "").strip().lower()
            nombre_demo = str(
                datos.get("nombre_contacto")
                or datos.get("nombre_negocio")
                or "Usuario"
            ).strip()
            if email_demo and "@" in email_demo:
                try:
                    acceso_sync = _portal_crear_o_actualizar_acceso(
                        empresa_id, email_demo, nombre_demo
                    )
                    if not isinstance(datos.get("portal_acceso"), dict):
                        datos["portal_acceso"] = {}
                    datos["portal_acceso"].update({
                        "email": acceso_sync.get("email"),
                        "auto": True,
                        "sincronizado": True,
                    })
                    try:
                        _core_save_session(demo_token, {"datos": datos})
                    except Exception as save_error:
                        print("PORTAL AUTH SYNC SESSION SAVE ERROR:", repr(save_error))
                    print("PORTAL AUTH SYNC OK:", empresa_id, email_demo)
                except Exception as auth_error:
                    # El acceso temporal por demo_token sigue funcionando;
                    # dejamos un log explícito para diagnosticar Auth sin tumbar el Portal.
                    print(
                        "PORTAL AUTH SYNC ERROR:",
                        empresa_id,
                        email_demo,
                        repr(auth_error),
                    )

            perfil = {
                "id": f"demo:{demo_token}",
                "empresa_id": empresa_id,
                "nombre": nombre_demo,
                "email": email_demo,
                "rol": "demo",
                "demo": True,
                "demo_token": demo_token,
                "conversion": acceso_conversion,
            }
            print("PORTAL DEMO AUTH OK:", empresa_id, perfil.get("email"))
            return perfil
        except Exception as e:
            print("PORTAL DEMO AUTH ERROR:", repr(e))
            return None

    # Sesión normal Supabase Auth
    """
    Valida el access token real enviado por portal.html y obtiene el perfil
    del usuario autenticado.

    IMPORTANTE:
    Aquí NO se filtra por empresa_actual_id().
    Primero identificamos al usuario y su empresa real en public.perfiles.
    Después cada endpoint activa y restringe el tenant correspondiente.
    """
    auth = str(request.headers.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        print("PORTAL AUTH: falta Bearer token")
        return None

    jwt = auth.split(" ", 1)[1].strip()
    if not jwt:
        print("PORTAL AUTH: token vacío")
        return None

    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("PORTAL AUTH: Supabase backend no configurado")
        return None

    # 1) Validar token contra Supabase Auth.
    r = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {jwt}",
        },
        timeout=SUPABASE_TIMEOUT,
    )
    if not r.ok:
        print("PORTAL AUTH ERROR /auth/v1/user:", r.status_code, r.text[:500])
        return None

    usuario = r.json() if r.content else {}
    user_id = str(usuario.get("id") or "").strip()
    email_auth = str(usuario.get("email") or "").strip()

    if not user_id:
        print("PORTAL AUTH: Supabase no devolvió user_id")
        return None

    # 2) Buscar el perfil SOLO por id.
    # La propia fila nos dice a qué empresa pertenece el usuario.
    headers = supabase_headers()
    if not headers:
        print("PORTAL AUTH: faltan headers backend")
        return None

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/perfiles",
        headers=headers,
        params={
            "select": "id,empresa_id,nombre,email,rol",
            "id": f"eq.{user_id}",
            "limit": "1",
        },
        timeout=SUPABASE_TIMEOUT,
    )

    if not r.ok:
        print("PORTAL PERFIL ERROR:", r.status_code, r.text[:500])
        return None

    filas = r.json() if r.content else []
    email_norm = email_auth.strip().lower()
    if not filas:
        if email_norm == SUPERADMIN_EMAIL:
            perfil = {"id": user_id, "empresa_id": ADMIN_EMPRESA_ID, "nombre": "Superadmin Nexia", "email": email_auth, "rol": "superadmin"}
            print("PORTAL AUTH: superadmin maestro sin perfil, usando perfil virtual", email_auth)
            return perfil
        print("PORTAL AUTH: usuario válido pero sin perfil:", user_id, email_auth)
        return None

    perfil = dict(filas[0])
    if email_norm == SUPERADMIN_EMAIL:
        perfil["rol"] = "superadmin"
        perfil["email"] = email_auth
        if not perfil.get("empresa_id"):
            perfil["empresa_id"] = ADMIN_EMPRESA_ID

    if not perfil.get("empresa_id"):
        print("PORTAL AUTH: perfil sin empresa_id:", user_id)
        return None

    print(
        "PORTAL AUTH OK:",
        "user_id=", user_id,
        "email=", perfil.get("email") or email_auth,
        "rol=", perfil.get("rol"),
        "empresa_id=", perfil.get("empresa_id"),
    )

    return perfil


def es_superadmin(perfil):
    email = str((perfil or {}).get("email") or "").strip().lower()
    rol = str((perfil or {}).get("rol") or "").strip().lower()
    return rol == "superadmin" or (bool(SUPERADMIN_EMAIL) and email == SUPERADMIN_EMAIL)


def obtener_conversacion_supabase(conversacion_id, perfil_portal=None):
    """
    Obtiene una conversación respetando el alcance del usuario.

    - superadmin: puede acceder a conversaciones de cualquier empresa.
    - otros perfiles: solo a conversaciones de su propia empresa.
    """
    headers = supabase_headers()
    if not headers:
        return None

    params = {
        "select": "id,empresa_id,telefono,nombre_contacto,canal,modo_atencion,ultimo_mensaje,ultima_fecha,created_at",
        "id": f"eq.{conversacion_id}",
        "limit": "1",
    }

    if not es_superadmin(perfil_portal):
        empresa_id = str((perfil_portal or {}).get("empresa_id") or empresa_actual_id()).strip()
        params["empresa_id"] = f"eq.{empresa_id}"

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/conversaciones",
        headers=headers,
        params=params,
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    filas = r.json() if r.content else []
    return enriquecer_estado_conversacion(filas[0]) if filas else None




@app.route("/portal/config-public", methods=["GET", "OPTIONS"])
def portal_config_public():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    return portal_json({
        "ok": True,
        "supabase_url": SUPABASE_URL,
        "supabase_anon_key": SUPABASE_ANON_KEY,
        "app_version": APP_VERSION,
    })


@app.route("/portal/login", methods=["POST", "OPTIONS"])
def portal_login():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip()
    password = str(data.get("password") or "")
    if not email or not password:
        return portal_json({"ok": False, "error": "Ingresa correo y contraseña"}, 400)
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return portal_json({"ok": False, "error": "Supabase Auth no está configurado en el backend"}, 500)
    try:
        r = requests.post(
            f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
            headers={"apikey": SUPABASE_SERVICE_ROLE_KEY, "Content-Type": "application/json"},
            json={"email": email, "password": password},
            timeout=SUPABASE_TIMEOUT,
        )
        if not r.ok:
            detalle = "Correo o contraseña incorrectos"
            try:
                detalle = (r.json() or {}).get("msg") or (r.json() or {}).get("error_description") or detalle
            except Exception:
                pass
            return portal_json({"ok": False, "error": detalle}, 401)
        auth_data = r.json() if r.content else {}
        token = str(auth_data.get("access_token") or "")
        if not token:
            return portal_json({"ok": False, "error": "Supabase no devolvió una sesión válida"}, 502)
        return portal_json({
            "ok": True,
            "access_token": token,
            "expires_in": auth_data.get("expires_in"),
            "token_type": auth_data.get("token_type") or "bearer",
        })
    except Exception as e:
        print("PORTAL LOGIN ERROR:", repr(e))
        return portal_json({"ok": False, "error": "No se pudo iniciar sesión en este momento"}, 502)




def _portal_clave_desde_email(email):
    """
    V3.4.8: clave automática del Portal = parte del correo antes de @.
    Ej.: carlos@gmail.com -> carlos
    Supabase exige una longitud mínima; si el nombre local es demasiado corto,
    se completa únicamente para poder crear la cuenta.
    """
    email = str(email or "").strip().lower()
    local = email.split("@", 1)[0].strip() if "@" in email else ""
    if not local:
        return ""
    # Mantener exactamente el local-part cuando cumple el mínimo práctico.
    if len(local) >= 6:
        return local
    # Fallback técnico excepcional para correos con local-part muy corto.
    return (local + "nexia")[:6]


def _portal_buscar_usuario_auth_por_email(email):
    """Busca un usuario Supabase Auth por email usando la API admin."""
    email = str(email or "").strip().lower()
    if not email:
        return None
    headers_admin = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    # MVP: recorrer páginas pequeñas; normalmente Nexia tiene pocos usuarios.
    for page in range(1, 11):
        r = requests.get(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=headers_admin,
            params={"page": page, "per_page": 100},
            timeout=SUPABASE_TIMEOUT,
        )
        if not r.ok:
            print("PORTAL AUTH USER LOOKUP ERROR:", r.status_code, r.text[:300])
            return None
        payload = r.json() if r.content else {}
        users = payload.get("users", []) if isinstance(payload, dict) else []
        for u in users:
            if str(u.get("email") or "").strip().lower() == email:
                return u
        if len(users) < 100:
            break
    return None


def _portal_crear_o_actualizar_acceso(empresa_id, email, nombre):
    """
    Crea el acceso real Supabase Auth para DEMO y PRODUCCIÓN.
    Clave = local-part del email. Si la cuenta ya existe, actualiza la clave
    y asegura el perfil de la empresa.
    """
    empresa_id = str(empresa_id or "").strip()
    email = str(email or "").strip().lower()
    nombre = str(nombre or "Cliente Nexia").strip()
    password = _portal_clave_desde_email(email)

    if not empresa_id or not email or "@" not in email or not password:
        raise RuntimeError("No se puede crear acceso Portal: empresa/email inválido")

    headers_admin = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    user = _portal_buscar_usuario_auth_por_email(email)
    if user:
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise RuntimeError("Usuario Auth existente sin id")
        ru = requests.put(
            f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
            headers=headers_admin,
            json={
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    **(user.get("user_metadata") or {}),
                    "empresa_id": empresa_id,
                    "nombre": nombre,
                    "nexia_auto_password": True,
                },
            },
            timeout=SUPABASE_TIMEOUT,
        )
        if not ru.ok:
            raise RuntimeError(f"No se pudo actualizar acceso Portal: {ru.status_code} {ru.text[:300]}")
    else:
        ru = requests.post(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers=headers_admin,
            json={
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {
                    "empresa_id": empresa_id,
                    "nombre": nombre,
                    "nexia_auto_password": True,
                },
            },
            timeout=SUPABASE_TIMEOUT,
        )
        if not ru.ok:
            raise RuntimeError(f"No se pudo crear acceso Portal: {ru.status_code} {ru.text[:300]}")
        user = ru.json() if ru.content else {}
        user_id = str(user.get("id") or "").strip()

    if not user_id:
        raise RuntimeError("Supabase no devolvió id de usuario Portal")

    hp = backend_headers()
    rp = requests.post(
        f"{SUPABASE_URL}/rest/v1/perfiles",
        headers={**hp, "Prefer":"resolution=merge-duplicates,return=representation"},
        params={"on_conflict":"id"},
        json={
            "id": user_id,
            "empresa_id": empresa_id,
            "nombre": nombre,
            "email": email,
            "rol": "cliente",
        },
        timeout=SUPABASE_TIMEOUT,
    )
    rp.raise_for_status()

    print("PORTAL AUTO ACCESS OK:", empresa_id, email, "clave_localpart=SI")
    return {
        "user_id": user_id,
        "email": email,
        "password": password,
    }


@app.route("/portal/crear-acceso-pagado", methods=["POST", "OPTIONS"])
def portal_crear_acceso_pagado():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    if not perfil.get("demo_token"):
        return portal_json({"ok": False, "error": "Este acceso no requiere conversión"}, 400)

    empresa_id = str(perfil.get("empresa_id") or "").strip()
    email = str(perfil.get("email") or "").strip().lower()
    data = request.get_json(silent=True) or {}
    password = _portal_clave_desde_email(email)
    if not email or "@" not in email:
        return portal_json({"ok": False, "error": "La prueba no tiene un correo válido asociado"}, 400)
    if not password:
        return portal_json({"ok": False, "error": "No se pudo generar la clave automática del Portal"}, 400)

    plan = estado_suscripcion_empresa(empresa_id)
    if str(plan.get("tipo_plan") or "").lower() not in {"nexia_500", "nexia_1000"}:
        return portal_json({"ok": False, "error": "Primero debes tener un pago aprobado"}, 409)

    nombre = str(perfil.get("nombre") or "Cliente Nexia").strip()
    acceso = _portal_crear_o_actualizar_acceso(empresa_id, email, nombre)
    user_id = acceso["user_id"]
    password = acceso["password"]
    hp = backend_headers()
    requests.patch(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers={**hp, "Prefer":"return=minimal"},
        params={"empresa_id":f"eq.{empresa_id}","status":"eq.approved","setup_completed":"eq.false"},
        json={"setup_completed":True,"updated_at":datetime.now(pytz.UTC).isoformat()},
        timeout=SUPABASE_TIMEOUT,
    )
    return portal_json({"ok": True, "email": email, "password": password, "mensaje":"Acceso permanente creado. La clave corresponde al texto del correo antes de @."}, 201)


@app.route("/portal/me", methods=["GET", "OPTIONS"])
def portal_me():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    return portal_json({
        "ok": True,
        "perfil": perfil,
        "superadmin": es_superadmin(perfil),
        "alcance": "global" if es_superadmin(perfil) else "empresa",
    })



def _portal_codigo_asistente(empresa_id):
    """Código estable para abrir desde Portal el asistente exacto de una empresa."""
    limpio = re.sub(r"[^0-9a-fA-F]", "", str(empresa_id or ""))
    return ("PORTAL" + limpio[:12]).upper()


def _portal_destino_asistente(empresa_id):
    """
    Crea/actualiza un destino de Router para el mismo empresa_id.
    No crea una empresa nueva ni una nueva demo.
    """
    empresa_id = str(empresa_id or "").strip()
    headers = backend_headers()
    if not empresa_id or not headers:
        raise RuntimeError("Empresa/Supabase no configurados")

    er = requests.get(
        f"{SUPABASE_URL}/rest/v1/empresas",
        headers=headers,
        params={"select":"id,nombre,activo", "id":f"eq.{empresa_id}", "limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    er.raise_for_status()
    empresas = er.json() if er.content else []
    if not empresas or not bool(empresas[0].get("activo", True)):
        raise RuntimeError("Empresa no disponible")

    nombre = str(empresas[0].get("nombre") or "Negocio Nexia").strip()
    codigo = _portal_codigo_asistente(empresa_id)
    payload = {
        "codigo": codigo,
        "empresa_id": empresa_id,
        "nombre_publico": nombre,
        "motor": "core",
        "activo": True,
        "visible_menu": False,
        "updated_at": datetime.now(pytz.UTC).isoformat(),
    }
    rr = requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_router_destinos",
        headers={**headers, "Prefer":"resolution=merge-duplicates,return=representation"},
        params={"on_conflict":"codigo"},
        json=payload,
        timeout=SUPABASE_TIMEOUT,
    )
    if _router_tabla_no_disponible(rr):
        raise RuntimeError("Falta instalar nexi_router_destinos")
    rr.raise_for_status()
    return codigo, nombre


@app.route("/portal/mi-asistente", methods=["GET", "OPTIONS"])
def portal_mi_asistente():
    """
    Devuelve un acceso directo al MISMO asistente de la empresa del usuario.
    Funciona para demo activa y para Nexia 500/1000.
    Nunca crea otra empresa ni otra prueba.
    """
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)

    empresa_id = str(perfil.get("empresa_id") or "").strip()
    if not empresa_id:
        return portal_json({"ok": False, "error": "Tu usuario no tiene una empresa asociada"}, 409)

    plan = estado_suscripcion_empresa(empresa_id)
    tipo = str(plan.get("tipo_plan") or "legacy").strip().lower()
    estado = str(plan.get("estado") or "activo").strip().lower()
    limite = int(plan.get("limite_mensajes") or plan.get("limite_respuestas") or 0)
    usados = int(plan.get("mensajes_usados") or plan.get("respuestas_usadas") or 0)
    restantes = max(0, limite - usados) if limite else None

    permitido = estado == "activo" and tipo in {"demo", "nexia_500", "nexia_1000", "legacy"}
    motivo = None

    if tipo in {"nexia_500", "nexia_1000"} and plan.get("vigente") is False:
        permitido = False
        motivo = str(plan.get("motivo_fin") or "tiempo")

    if tipo == "demo":
        fin = _parse_iso(plan.get("demo_fin"))
        if fin and datetime.now(pytz.UTC) >= fin.astimezone(pytz.UTC):
            permitido = False
            motivo = "tiempo"
        elif restantes is not None and restantes <= 0:
            permitido = False
            motivo = "limite"

    if tipo in {"nexia_500", "nexia_1000"} and restantes is not None and restantes <= 0:
        permitido = False
        motivo = "limite"

    if not permitido:
        return portal_json({
            "ok": False,
            "codigo": "ASISTENTE_SIN_CUPO",
            "error": (
                "Tu prueba ya finalizó. Puedes reactivarla o contratar un plan para continuar con el mismo asistente."
                if tipo == "demo"
                else "Tu plan no tiene mensajes disponibles. Recarga mensajes para continuar con el mismo asistente."
            ),
            "plan": plan,
            "motivo": motivo,
        }, 409)

    try:
        codigo, nombre = _portal_destino_asistente(empresa_id)
    except Exception as e:
        print("PORTAL MI ASISTENTE ERROR:", repr(e))
        return portal_json({"ok": False, "error": "No pude preparar el acceso al asistente"}, 500)

    numero = re.sub(r"\D", "", str(TWILIO_WHATSAPP_FROM or ""))
    if not numero:
        return portal_json({"ok": False, "error": "WhatsApp principal no configurado"}, 500)

    texto = f"NEXI {codigo}"
    whatsapp_url = f"https://wa.me/{numero}?text={quote(texto)}"

    return portal_json({
        "ok": True,
        "empresa_id": empresa_id,
        "empresa": nombre,
        "tipo_plan": tipo,
        "estado": estado,
        "mensajes_restantes": restantes,
        "codigo_acceso": codigo,
        "whatsapp_url": whatsapp_url,
        "mensaje": "Abrirás el mismo asistente asociado a tu empresa; no se crea una nueva prueba.",
    })


@app.route("/portal/conversaciones", methods=["GET", "OPTIONS"])
def portal_conversaciones():
    """
    Bandeja de conversaciones del portal.

    superadmin -> todas las empresas
    otros usuarios -> solo su propia empresa
    """
    if request.method == "OPTIONS":
        from flask import make_response
        return portal_cors_response(make_response("", 204))

    perfil_portal = portal_usuario_autorizado()
    if not perfil_portal:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)

    headers = supabase_headers()
    if not headers:
        return portal_json({"ok": False, "error": "Supabase backend no configurado"}, 500)

    try:
        params = {
            "select": "id,empresa_id,telefono,nombre_contacto,ultimo_mensaje,ultima_fecha,created_at,canal,modo_atencion,empresas(nombre)",
            "order": "ultima_fecha.desc.nullslast,created_at.desc",
        }

        if not es_superadmin(perfil_portal):
            params["empresa_id"] = f"eq.{perfil_portal['empresa_id']}"

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/conversaciones",
            headers=headers,
            params=params,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        filas = r.json() if r.content else []
        filas = [enriquecer_estado_conversacion(fila) for fila in filas]

        return portal_json({
            "ok": True,
            "alcance": "global" if es_superadmin(perfil_portal) else "empresa",
            "criterios_estado": {
                "online_minutos": CONVERSACION_ONLINE_MINUTOS,
                "espera_horas": CONVERSACION_ESPERA_HORAS,
            },
            "conversaciones": filas,
        })

    except Exception as e:
        print("PORTAL CONVERSACIONES ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


@app.route("/portal/conversacion/<conversacion_id>/mensajes", methods=["GET", "OPTIONS"])
def portal_mensajes_conversacion(conversacion_id):
    """
    Historial de una conversación con validación de alcance.
    """
    if request.method == "OPTIONS":
        from flask import make_response
        return portal_cors_response(make_response("", 204))

    perfil_portal = portal_usuario_autorizado()
    if not perfil_portal:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)

    try:
        conv = obtener_conversacion_supabase(conversacion_id, perfil_portal)
        if not conv:
            return portal_json({"ok": False, "error": "Conversación no encontrada"}, 404)

        headers = supabase_headers()
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mensajes",
            headers=headers,
            params={
                "select": "id,conversacion_id,direccion,mensaje,fecha,canal",
                "conversacion_id": f"eq.{conversacion_id}",
                "order": "fecha.asc",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        filas = r.json() if r.content else []

        return portal_json({
            "ok": True,
            "empresa_id": conv.get("empresa_id"),
            "mensajes": filas,
        })

    except Exception as e:
        print("PORTAL MENSAJES ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


def _parsear_fecha_iso_segura(valor):
    """
    Convierte una fecha ISO de Supabase a datetime con zona horaria.
    Si no se puede interpretar, retorna None.
    """
    if not valor:
        return None

    texto = str(valor).strip()
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"

    try:
        fecha = datetime.fromisoformat(texto)
    except Exception:
        return None

    if fecha.tzinfo is None:
        fecha = pytz.UTC.localize(fecha)

    return fecha



def estado_conversacion_automatico(conversacion):
    """
    Calcula el estado visual de una conversación según su última actividad.

    Reglas por defecto:
    - online: actividad en los últimos 15 minutos.
    - en_espera: más de 15 minutos y hasta 24 horas.
    - terminada: más de 24 horas sin actividad.

    Los umbrales son configurables desde Render:
    CONVERSACION_ONLINE_MINUTOS
    CONVERSACION_ESPERA_HORAS

    No modifica el modo bot/ejecutivo; es un estado independiente.
    """
    ultima_raw = (
        (conversacion or {}).get("ultima_fecha")
        or (conversacion or {}).get("created_at")
    )

    fecha = _parsear_fecha_iso_segura(ultima_raw)

    if not fecha:
        return {
            "estado": "terminada",
            "estado_label": "Terminada",
            "ultima_actividad": ultima_raw,
            "minutos_inactivo": None,
            "motivo": "No fue posible verificar la última actividad",
        }

    ahora = datetime.now(pytz.UTC)
    fecha_utc = fecha.astimezone(pytz.UTC)
    minutos = max(0, int((ahora - fecha_utc).total_seconds() // 60))

    limite_online = max(1, CONVERSACION_ONLINE_MINUTOS)
    limite_espera = max(limite_online + 1, CONVERSACION_ESPERA_HORAS * 60)

    if minutos <= limite_online:
        estado = "online"
        label = "Online"
        motivo = f"Actividad dentro de los últimos {limite_online} minutos"
    elif minutos <= limite_espera:
        estado = "en_espera"
        label = "En espera"
        motivo = f"Sin actividad reciente; todavía dentro de {CONVERSACION_ESPERA_HORAS} horas"
    else:
        estado = "terminada"
        label = "Terminada"
        motivo = f"Sin actividad por más de {CONVERSACION_ESPERA_HORAS} horas"

    return {
        "estado": estado,
        "estado_label": label,
        "ultima_actividad": fecha_utc.isoformat(),
        "minutos_inactivo": minutos,
        "motivo": motivo,
    }


def enriquecer_estado_conversacion(conversacion):
    """
    Agrega campos de estado automático a una conversación sin alterar
    los datos originales almacenados en Supabase.
    """
    fila = dict(conversacion or {})
    estado = estado_conversacion_automatico(fila)

    fila["estado_conversacion"] = estado["estado"]
    fila["estado_conversacion_label"] = estado["estado_label"]
    fila["minutos_inactivo"] = estado["minutos_inactivo"]
    fila["estado_conversacion_motivo"] = estado["motivo"]

    return fila


def estado_ventana_whatsapp_24h(conversacion_id):
    """
    Verifica de forma conservadora la ventana de atención de WhatsApp.

    Regla inicial:
    - La ventana se abre durante 24 horas desde el ÚLTIMO mensaje ENTRANTE
      recibido desde el usuario.
    - Si no existe un mensaje entrante verificable, se considera CERRADA.
    - Esta función NO envía templates fuera de ventana; solo informa si
      una respuesta libre puede enviarse.

    Retorna:
      {
        "abierta": bool,
        "ultima_entrada": ISO | None,
        "vence": ISO | None,
        "segundos_restantes": int,
        "motivo": str
      }
    """
    headers = supabase_headers()
    if not headers:
        return {
            "abierta": False,
            "ultima_entrada": None,
            "vence": None,
            "segundos_restantes": 0,
            "motivo": "No fue posible verificar Supabase",
        }

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/mensajes",
            headers=headers,
            params={
                "select": "fecha,direccion,canal",
                "conversacion_id": f"eq.{conversacion_id}",
                "direccion": "eq.entrante",
                "canal": "eq.whatsapp",
                "order": "fecha.desc",
                "limit": "1",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()

        filas = r.json() if r.content else []
        if not filas:
            return {
                "abierta": False,
                "ultima_entrada": None,
                "vence": None,
                "segundos_restantes": 0,
                "motivo": "No existe un mensaje entrante de WhatsApp registrado",
            }

        ultima_raw = filas[0].get("fecha")
        ultima = _parsear_fecha_iso_segura(ultima_raw)

        if not ultima:
            return {
                "abierta": False,
                "ultima_entrada": ultima_raw,
                "vence": None,
                "segundos_restantes": 0,
                "motivo": "No fue posible interpretar la fecha del último mensaje entrante",
            }

        ahora = datetime.now(pytz.UTC)
        ultima_utc = ultima.astimezone(pytz.UTC)
        vence = ultima_utc + timedelta(hours=24)
        segundos = max(0, int((vence - ahora).total_seconds()))
        abierta = ahora <= vence

        return {
            "abierta": abierta,
            "ultima_entrada": ultima_utc.isoformat(),
            "vence": vence.isoformat(),
            "segundos_restantes": segundos if abierta else 0,
            "motivo": (
                "Ventana de atención WhatsApp abierta"
                if abierta
                else "Han transcurrido más de 24 horas desde el último mensaje entrante"
            ),
        }

    except Exception as e:
        print("WHATSAPP 24H CHECK ERROR:", repr(e))
        return {
            "abierta": False,
            "ultima_entrada": None,
            "vence": None,
            "segundos_restantes": 0,
            "motivo": "No fue posible verificar la ventana de 24 horas",
        }


@app.route("/portal/modo-atencion", methods=["POST", "OPTIONS"])
def portal_modo_atencion():
    if request.method == "OPTIONS":
        from flask import make_response
        return portal_cors_response(make_response("", 204))

    perfil_portal = portal_usuario_autorizado()
    if not perfil_portal:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)

    data = request.get_json(silent=True) or {}
    conversacion_id = str(data.get("conversacion_id") or "").strip()
    modo = str(data.get("modo") or "").strip().lower()

    if not conversacion_id or modo not in {"bot", "ejecutivo"}:
        return portal_json({"ok": False, "error": "Datos inválidos"}, 400)

    try:
        conv = obtener_conversacion_supabase(conversacion_id, perfil_portal)
        if not conv:
            return portal_json({"ok": False, "error": "Conversación no encontrada"}, 404)

        activar_por_empresa(conv["empresa_id"])
        establecer_modo_atencion(conversacion_id, modo)
        return portal_json({"ok": True, "modo": modo})
    except Exception as e:
        print("PORTAL MODO ATENCION ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


@app.route("/portal/enviar-mensaje", methods=["POST", "OPTIONS"])
def portal_enviar_mensaje():
    if request.method == "OPTIONS":
        from flask import make_response
        return portal_cors_response(make_response("", 204))

    perfil_portal = portal_usuario_autorizado()
    if not perfil_portal:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)

    data = request.get_json(silent=True) or {}
    conversacion_id = str(data.get("conversacion_id") or "").strip()
    mensaje = str(data.get("mensaje") or "").strip()

    if not conversacion_id or not mensaje:
        return portal_json({"ok": False, "error": "Falta conversación o mensaje"}, 400)

    if len(mensaje) > 4000:
        return portal_json({"ok": False, "error": "Mensaje demasiado largo"}, 400)

    try:
        conv = obtener_conversacion_supabase(conversacion_id, perfil_portal)
        if not conv:
            return portal_json({"ok": False, "error": "Conversación no encontrada"}, 404)

        empresa_conv_id = str(conv.get("empresa_id") or "").strip()
        canal = str(conv.get("canal") or "whatsapp").lower()
        destino = str(conv.get("telefono") or "").strip()

        # ============================================================
        # BLOQUEO INICIAL WHATSAPP — VENTANA DE 24 HORAS
        # ============================================================
        # Para respuestas libres enviadas manualmente desde el portal,
        # no permitimos enviar si la ventana de atención de WhatsApp
        # está cerrada. Inicialmente NO se envían templates automáticos.
        #
        # Esto se valida en backend para que no pueda saltarse desde
        # el navegador/portal.
        if canal == "whatsapp":
            ventana = estado_ventana_whatsapp_24h(conversacion_id)

            if not ventana.get("abierta"):
                print(
                    "PORTAL WHATSAPP BLOQUEADO 24H:",
                    "conversacion_id=", conversacion_id,
                    "destino=", destino,
                    "motivo=", ventana.get("motivo"),
                    "ultima_entrada=", ventana.get("ultima_entrada"),
                )

                return portal_json(
                    {
                        "ok": False,
                        "bloqueado": True,
                        "codigo": "WHATSAPP_24H_CERRADA",
                        "error": (
                            "No puedes enviar una respuesta libre por WhatsApp porque "
                            "la ventana de atención de 24 horas está cerrada. "
                            "El cliente debe volver a escribir para abrir una nueva ventana."
                        ),
                        "ventana_24h": ventana,
                    },
                    409,
                )

        canal_cfg = {}
        provider = "meta" if canal == "instagram" else "twilio"
        hb = backend_headers()
        if hb:
            rc = requests.get(f"{SUPABASE_URL}/rest/v1/canales_empresa", headers=hb, params={"select":"*","empresa_id":f"eq.{empresa_conv_id}","canal":f"eq.{canal}","activo":"eq.true","es_principal":"eq.true","limit":"1"}, timeout=SUPABASE_TIMEOUT)
            if rc.ok:
                rows = rc.json() if rc.content else []
                if rows:
                    canal_cfg = rows[0]
                    provider = str(canal_cfg.get("provider") or provider)
        activar_por_empresa(empresa_conv_id, canal=canal, provider=provider, canal_config=canal_cfg)

        # Demos y planes por mensajes: una respuesta manual del ejecutivo consume 1 mensaje.
        plan_actual = estado_suscripcion_empresa(empresa_conv_id)
        if str(plan_actual.get("tipo_plan") or "").lower() in {"demo", "nexia_500", "nexia_1000"}:
            control_portal = consumir_mensaje_demo_atomico()
            if not bool(control_portal.get("permitido", True)):
                return portal_json({
                    "ok": False,
                    "codigo": "PLAN_SIN_MENSAJES",
                    "error": mensaje_plan_finalizado(control_portal),
                    "plan": estado_suscripcion_empresa(empresa_conv_id),
                }, 409)

        # En cuanto un ejecutivo responde desde el portal, el bot deja de intervenir
        # en esta conversación hasta que se reactive manualmente.
        establecer_modo_atencion(conversacion_id, "ejecutivo")

        if canal == "instagram":
            resultado = enviar_instagram_texto(destino, mensaje)
            if resultado is False:
                raise RuntimeError("Instagram no confirmó el envío")
            proveedor = "instagram"

        elif canal == "whatsapp":
            if str(cfg("provider", "twilio")).lower() == "gupshup":
                enviar_gupshup_texto(destino, mensaje)
                proveedor = "gupshup"
            else:
                enviar_twilio_texto(destino, mensaje)
                proveedor = "twilio"

        else:
            return portal_json(
                {"ok": False, "error": f"Canal no soportado: {canal}"},
                400,
            )

        guardar_mensaje_supabase(
            destino,
            "saliente",
            mensaje,
            nombre_contacto=conv.get("nombre_contacto"),
            canal=canal,
        )

        print("PORTAL MENSAJE OK:", canal, destino, proveedor)
        return portal_json({
            "ok": True,
            "canal": canal,
            "proveedor": proveedor,
        })

    except Exception as e:
        print("PORTAL MENSAJE ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 502)



# ============================================================
# PORTAL NEXIA - PLAN / DEMO
# ============================================================

@app.route("/portal/plan", methods=["GET", "OPTIONS"])
def portal_plan():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    empresa_id = str(perfil.get("empresa_id") or "").strip()
    plan = estado_suscripcion_empresa(empresa_id)
    return portal_json({"ok": True, "plan": plan})


@app.route("/portal/admin/demo/<empresa_id>/activar", methods=["POST", "OPTIONS"])
def portal_admin_activar_demo(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    if not portal_admin_autorizado():
        return portal_json({"ok": False, "error": "Administrador Nexia no autorizado"}, 403)
    try:
        data = request.get_json(silent=True) or {}
        identificador = data.get("identificador_cliente") or data.get("telefono")
        canal = str(data.get("canal") or "whatsapp").strip().lower()
        demo = activar_demo_empresa(empresa_id, identificador, canal)
        return portal_json({"ok": True, "demo": demo}, 201)
    except Exception as e:
        print("PORTAL ACTIVAR DEMO ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


@app.route("/portal/admin/plan/<empresa_id>", methods=["GET", "PATCH", "OPTIONS"])
def portal_admin_plan(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    if not portal_admin_autorizado():
        return portal_json({"ok": False, "error": "Administrador Nexia no autorizado"}, 403)
    headers = backend_headers()
    if not headers:
        return portal_json({"ok": False, "error": "Supabase backend no configurado"}, 500)
    try:
        if request.method == "GET":
            return portal_json({"ok": True, "plan": estado_suscripcion_empresa(empresa_id)})
        data = request.get_json(silent=True) or {}
        allowed = {"tipo_plan", "estado", "limite_mensajes", "mensajes_usados", "demo_inicio", "demo_fin", "periodo_inicio", "periodo_fin"}
        payload = {k: data[k] for k in allowed if k in data}
        payload["empresa_id"] = empresa_id
        payload["updated_at"] = datetime.now(pytz.UTC).isoformat()
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/suscripciones_empresa",
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            params={"on_conflict": "empresa_id"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []
        return portal_json({"ok": True, "plan": rows[0] if rows else payload})
    except Exception as e:
        print("PORTAL ADMIN PLAN ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)



# ============================================================
# NEXIA V2.0 - CHECKOUT / PAGOS MERCADO PAGO
# ============================================================

def _mp_headers():
    if not MERCADOPAGO_ACCESS_TOKEN:
        return None
    return {
        "Authorization": f"Bearer {MERCADOPAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _plan_publico(plan):
    return {
        "codigo": plan["codigo"],
        "nombre": plan["nombre"],
        "mensajes": int(plan["mensajes"]),
        "duracion_dias": int(plan.get("duracion_dias") or NEXIA_PLAN_DURACION_DIAS_DEFAULT),
        "precio": int(plan["precio"]),
        "precio_antes": int(plan["precio_antes"]),
        "moneda": plan["moneda"],
    }


def _pago_por_external_reference(external_reference):
    headers = backend_headers()
    if not headers or not external_reference:
        return None
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers=headers,
        params={"select":"*","external_reference":f"eq.{external_reference}","limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    if not r.ok:
        return None
    rows = r.json() if r.content else []
    return rows[0] if rows else None


def _pago_por_payment_id(payment_id):
    headers = backend_headers()
    if not headers or not payment_id:
        return None
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers=headers,
        params={"select":"*","payment_id":f"eq.{payment_id}","limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    if not r.ok:
        return None
    rows = r.json() if r.content else []
    return rows[0] if rows else None


def _actualizar_pago(external_reference, payload):
    headers = backend_headers()
    if not headers:
        raise RuntimeError("Supabase backend no configurado")
    payload = {**payload, "updated_at": datetime.now(pytz.UTC).isoformat()}
    r = requests.patch(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers={**headers, "Prefer":"return=representation"},
        params={"external_reference":f"eq.{external_reference}"},
        json=payload,
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json() if r.content else []
    return rows[0] if rows else payload


def _activar_plan_desde_pago(pago, payment_data):
    """Aplica la bolsa una sola vez y convierte demo -> cliente pagado."""
    if not pago:
        raise RuntimeError("Pago Nexia no encontrado")
    if pago.get("applied_at"):
        return pago

    plan = NEXIA_PLANES.get(str(pago.get("plan_codigo") or ""))
    if not plan:
        raise RuntimeError("Plan Nexia inválido")

    # Verificación fuerte contra lo cobrado por Mercado Pago.
    monto = int(round(float(payment_data.get("transaction_amount") or 0)))
    moneda = str(payment_data.get("currency_id") or "").upper()
    if monto != int(plan["precio"]) or moneda != str(plan["moneda"]).upper():
        raise RuntimeError("Monto o moneda del pago no coincide con el plan")

    empresa_id = str(pago.get("empresa_id") or "").strip()
    if not empresa_id:
        raise RuntimeError("Pago sin empresa_id")

    # Cada compra/renovación abre un período mensual nuevo.
    # El plan termina al consumir su bolsa o al cumplir 30 días, lo que ocurra primero.
    ahora_dt = datetime.now(pytz.UTC)
    periodo_fin_dt = ahora_dt + timedelta(days=int(plan.get("duracion_dias") or NEXIA_PLAN_DURACION_DIAS_DEFAULT))
    ahora = ahora_dt.isoformat()
    periodo_fin = periodo_fin_dt.isoformat()
    nuevo_limite = int(plan["mensajes"])
    usados = 0
    headers = backend_headers()
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/suscripciones_empresa",
        headers={**headers, "Prefer":"resolution=merge-duplicates,return=representation"},
        params={"on_conflict":"empresa_id"},
        json={
            "empresa_id": empresa_id,
            "tipo_plan": plan["codigo"],
            "estado": "activo",
            "limite_mensajes": nuevo_limite,
            "mensajes_usados": usados,
            "periodo_inicio": ahora,
            "periodo_fin": periodo_fin,
            "demo_inicio": None,
            "demo_fin": None,
            "updated_at": ahora,
        },
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()

    payment_id = str(payment_data.get("id") or "").strip()
    return _actualizar_pago(
        pago["external_reference"],
        {
            "payment_id": payment_id or pago.get("payment_id"),
            "status": "approved",
            "status_detail": str(payment_data.get("status_detail") or ""),
            "approved_at": str(payment_data.get("date_approved") or ahora),
            "applied_at": ahora,
            "metadata": payment_data.get("metadata") or pago.get("metadata") or {},
        },
    )


def _verificar_y_procesar_payment(payment_id):
    headers_mp = _mp_headers()
    if not headers_mp:
        raise RuntimeError("Falta MERCADOPAGO_ACCESS_TOKEN en Render")
    r = requests.get(
        f"{MERCADOPAGO_API_BASE}/v1/payments/{payment_id}",
        headers=headers_mp,
        timeout=20,
    )
    r.raise_for_status()
    pdata = r.json() if r.content else {}
    external_reference = str(pdata.get("external_reference") or "").strip()
    pago = _pago_por_external_reference(external_reference)
    if not pago:
        raise RuntimeError("El pago no corresponde a un checkout emitido por Nexia")

    status = str(pdata.get("status") or "").lower()
    patch = {
        "payment_id": str(pdata.get("id") or payment_id),
        "status": status or "unknown",
        "status_detail": str(pdata.get("status_detail") or ""),
        "merchant_order_id": str(pdata.get("order", {}).get("id") or "") or None,
    }
    _actualizar_pago(external_reference, patch)
    if status == "approved":
        pago = _pago_por_external_reference(external_reference)
        return _activar_plan_desde_pago(pago, pdata)
    return _pago_por_external_reference(external_reference)


@app.route("/portal/planes", methods=["GET", "OPTIONS"])
def portal_planes():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    return portal_json({"ok": True, "planes": [_plan_publico(x) for x in NEXIA_PLANES.values()]})


@app.route("/portal/mercadopago/checkout", methods=["POST", "OPTIONS"])
def portal_mercadopago_checkout():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    headers_mp = _mp_headers()
    if not headers_mp:
        return portal_json({"ok": False, "error": "Mercado Pago no está configurado"}, 503)

    data = request.get_json(silent=True) or {}
    codigo = str(data.get("plan") or "").strip().lower()
    plan = NEXIA_PLANES.get(codigo)
    if not plan:
        return portal_json({"ok": False, "error": "Plan inválido"}, 400)

    empresa_id = str(perfil.get("empresa_id") or "").strip()
    email = str(perfil.get("email") or "").strip()
    if not empresa_id:
        return portal_json({"ok": False, "error": "Usuario sin empresa asociada"}, 400)

    external_reference = f"NEXIA:{empresa_id}:{codigo}:{uuid.uuid4().hex[:12]}"
    return_url = f"{PORTAL_ORIGIN}/portal.html?pago=success"
    if perfil.get("demo") and perfil.get("demo_token"):
        return_url += f"&demo_token={perfil.get('demo_token')}"

    payload = {
        "items": [{
            "id": codigo,
            "title": f"{plan['nombre']} - {plan['mensajes']} mensajes",
            "quantity": 1,
            "currency_id": plan["moneda"],
            "unit_price": int(plan["precio"]),
        }],
        "external_reference": external_reference,
        "metadata": {
            "empresa_id": empresa_id,
            "plan_codigo": codigo,
            "mensajes": int(plan["mensajes"]),
        },
        "back_urls": {
            "success": return_url,
            "pending": f"{PORTAL_ORIGIN}/portal.html?pago=pending" + (f"&demo_token={perfil.get('demo_token')}" if perfil.get("demo") else ""),
            "failure": f"{PORTAL_ORIGIN}/portal.html?pago=failure" + (f"&demo_token={perfil.get('demo_token')}" if perfil.get("demo") else ""),
        },
        "auto_return": "approved",
        "notification_url": MERCADOPAGO_WEBHOOK_URL,
        "statement_descriptor": "NEXIA",
    }
    if email:
        payload["payer"] = {"email": email}

    r = requests.post(
        f"{MERCADOPAGO_API_BASE}/checkout/preferences",
        headers=headers_mp,
        json=payload,
        timeout=20,
    )
    if not r.ok:
        print("MERCADOPAGO PREFERENCE ERROR:", r.status_code, r.text[:1200])
        return portal_json({"ok": False, "error": "No fue posible iniciar el pago"}, 502)
    pref = r.json() if r.content else {}

    headers = backend_headers()
    registro = {
        "empresa_id": empresa_id,
        "plan_codigo": codigo,
        "plan_nombre": plan["nombre"],
        "mensajes": int(plan["mensajes"]),
        "monto": int(plan["precio"]),
        "moneda": plan["moneda"],
        "preference_id": str(pref.get("id") or ""),
        "external_reference": external_reference,
        "status": "created",
        "email_cliente": email or None,
        "metadata": {"perfil_demo": bool(perfil.get("demo"))},
        "updated_at": datetime.now(pytz.UTC).isoformat(),
    }
    rr = requests.post(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers={**headers, "Prefer":"return=representation"},
        json=registro,
        timeout=SUPABASE_TIMEOUT,
    )
    rr.raise_for_status()
    return portal_json({
        "ok": True,
        "checkout_url": pref.get("init_point") or pref.get("sandbox_init_point"),
        "preference_id": pref.get("id"),
        "external_reference": external_reference,
    })


@app.route("/webhooks/mercadopago", methods=["POST", "GET"])
def webhook_mercadopago():
    """Mercado Pago puede reenviar eventos; el procesamiento es idempotente."""
    try:
        data = request.get_json(silent=True) or {}
        payment_id = str(
            ((data.get("data") or {}).get("id"))
            or request.args.get("data.id")
            or request.args.get("id")
            or ""
        ).strip()
        tipo = str(data.get("type") or request.args.get("type") or request.args.get("topic") or "").lower()
        # Solo los eventos payment traen un id que podemos verificar en /v1/payments/{id}.
        # merchant_order se ignora para evitar interpretar su id como payment_id.
        if not payment_id or (tipo and tipo != "payment"):
            return "OK", 200
        pago = _verificar_y_procesar_payment(payment_id)
        print("MERCADOPAGO WEBHOOK OK:", payment_id, (pago or {}).get("status"))
        return "OK", 200
    except Exception as e:
        # Devolver 500 permite a Mercado Pago reintentar el webhook.
        print("MERCADOPAGO WEBHOOK ERROR:", repr(e))
        return "ERROR", 500


@app.route("/portal/pago/estado", methods=["GET", "OPTIONS"])
def portal_pago_estado():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    empresa_id = str(perfil.get("empresa_id") or "").strip()
    headers = backend_headers()
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nexi_pagos",
        headers=headers,
        params={"select":"*","empresa_id":f"eq.{empresa_id}","order":"created_at.desc","limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json() if r.content else []
    pago = rows[0] if rows else None
    return portal_json({"ok": True, "pago": pago, "plan": estado_suscripcion_empresa(empresa_id)})


@app.route("/portal/pagos", methods=["GET", "OPTIONS"])
def portal_pagos():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    headers = backend_headers()
    params = {"select":"*","order":"created_at.desc","limit":"100"}
    if not es_superadmin(perfil):
        params["empresa_id"] = f"eq.{perfil.get('empresa_id')}"
    r = requests.get(f"{SUPABASE_URL}/rest/v1/nexi_pagos", headers=headers, params=params, timeout=SUPABASE_TIMEOUT)
    r.raise_for_status()
    return portal_json({"ok": True, "pagos": r.json() if r.content else []})


@app.route("/")
def health():
    return {
        "ok": True,
        "app": "Nexia Core - Multiempresa",
        "version": APP_VERSION,
        "channel": "Twilio WhatsApp + Gupshup WhatsApp + Instagram Meta API",
        "calendar": "Google Calendar",
        "payments": "Mercado Pago Checkout Pro",
        "saas": "multiempresa",
        "prueba_gratuita": f"{DEMO_LIMITE_MENSAJES_DEFAULT} mensajes / {DEMO_DURACION_HORAS_DEFAULT} horas",
        "history": "Supabase",
    }, 200


# ============================================================
# PORTAL ADMIN NEXIA
# ============================================================

def portal_admin_autorizado():
    """
    Autorización del panel administrativo Nexia Core.

    Reglas:
    - superadmin: acceso global a TODAS las empresas, independiente de empresa_id.
    - admin / nexia_admin / administrador: acceso administrativo solo si
      pertenecen a la empresa administrativa definida en ADMIN_EMPRESA_ID.
    - cliente u otros roles: sin acceso administrativo.

    Esto permite mantener una empresa administrativa separada de las empresas
    clientes, por ejemplo:

        Administración General (empresa administrativa)
            ├── Estilista Diego
            ├── Nexia
            └── futuros clientes
    """
    perfil = portal_usuario_autorizado()
    if not perfil:
        print("PORTAL ADMIN AUTH: sin perfil autorizado")
        return None

    rol = str(perfil.get("rol") or "").strip().lower()
    empresa_id = str(perfil.get("empresa_id") or "").strip()
    nexia_id = str(ADMIN_EMPRESA_ID or "").strip()

    # SUPERADMIN GLOBAL
    # No depende de que el perfil pertenezca a una empresa cliente concreta.
    if rol == "superadmin":
        print(
            "PORTAL SUPERADMIN AUTH OK:",
            "email=", perfil.get("email"),
            "rol=", rol,
            "empresa_administrativa=", empresa_id,
        )
        return perfil

    # Roles administrativos tradicionales:
    # siguen restringidos a la empresa administrativa Nexia.
    roles_admin_locales = {"admin", "nexia_admin", "administrador"}

    if rol not in roles_admin_locales:
        print("PORTAL ADMIN AUTH: rol no permitido:", rol)
        return None

    if empresa_id != nexia_id:
        print(
            "PORTAL ADMIN AUTH: empresa no corresponde",
            "perfil_empresa=", empresa_id,
            "nexia_empresa=", nexia_id,
        )
        return None

    print(
        "PORTAL ADMIN AUTH OK:",
        "email=", perfil.get("email"),
        "rol=", rol,
        "empresa_id=", empresa_id,
    )
    return perfil


def admin_json_error(msg, status=400):
    return portal_json({"ok": False, "error": msg}, status)


def _contar_supabase(tabla, filtros=None):
    """
    Cuenta filas usando Prefer: count=exact sin descargar registros.
    """
    headers = dict(supabase_headers() or {})
    if not headers:
        raise RuntimeError("Supabase backend no configurado")

    headers["Prefer"] = "count=exact"
    params = {"select": "id", "limit": "1"}
    for clave, valor in (filtros or {}).items():
        params[clave] = valor

    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/{tabla}",
        headers=headers,
        params=params,
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()

    content_range = r.headers.get("Content-Range", "")
    if "/" in content_range:
        total = content_range.split("/")[-1]
        if total.isdigit():
            return int(total)

    # Fallback si el proxy no devuelve Content-Range.
    datos = r.json() if r.content else []
    return len(datos)


def _estadisticas_empresa(empresa_id, empresa_nombre=None):
    """
    Estadísticas principales de una empresa.
    """
    filtros_conv = {"empresa_id": f"eq.{empresa_id}"}
    filtros_msg = {"empresa_id": f"eq.{empresa_id}"}

    # Algunas instalaciones antiguas pueden no tener empresa_id en mensajes.
    # En ese caso calculamos por conversaciones.
    try:
        recibidos = _contar_supabase(
            "mensajes",
            {**filtros_msg, "direccion": "eq.entrante"},
        )
        enviados = _contar_supabase(
            "mensajes",
            {**filtros_msg, "direccion": "eq.saliente"},
        )
    except Exception:
        headers = supabase_headers()
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/conversaciones",
            headers=headers,
            params={
                "select": "id",
                "empresa_id": f"eq.{empresa_id}",
                "limit": "10000",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        ids = [str(x.get("id")) for x in (r.json() if r.content else []) if x.get("id")]

        recibidos = 0
        enviados = 0
        for conv_id in ids:
            recibidos += _contar_supabase(
                "mensajes",
                {"conversacion_id": f"eq.{conv_id}", "direccion": "eq.entrante"},
            )
            enviados += _contar_supabase(
                "mensajes",
                {"conversacion_id": f"eq.{conv_id}", "direccion": "eq.saliente"},
            )

    conversaciones = _contar_supabase("conversaciones", filtros_conv)

    return {
        "empresa_id": empresa_id,
        "empresa_nombre": empresa_nombre,
        "mensajes_recibidos": recibidos,
        "mensajes_enviados": enviados,
        "mensajes_totales": recibidos + enviados,
        "conversaciones": conversaciones,
    }



@app.route("/portal/handoffs", methods=["GET", "OPTIONS"])
def portal_handoffs():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    try:
        params = {"select": "*", "order": "created_at.desc", "limit": "300"}
        if not es_superadmin(perfil):
            params["empresa_id"] = f"eq.{perfil.get('empresa_id')}"
        r = requests.get(f"{SUPABASE_URL}/rest/v1/nexi_core_handoffs", headers=supabase_headers(), params=params, timeout=SUPABASE_TIMEOUT)
        if r.status_code == 404:
            return portal_json({"ok": True, "handoffs": []})
        r.raise_for_status()
        return portal_json({"ok": True, "handoffs": r.json() if r.content else []})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


def _portal_actualizar_handoff_por_conversacion(conv, estado):
    """Sincroniza, cuando existe, el handoff Core asociado a la conversación."""
    headers = supabase_headers()
    if not headers or not conv:
        return
    ident = _normalizar_identificador_demo(conv.get("telefono"), conv.get("canal") or "whatsapp")
    try:
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/nexi_core_handoffs",
            headers={**headers, "Prefer": "return=minimal"},
            params={"empresa_id": f"eq.{conv.get('empresa_id')}", "identificador": f"eq.{ident}", "canal": f"eq.{conv.get('canal') or 'whatsapp'}", "estado": "in.(pendiente,en_atencion)"},
            json={"estado": estado, "updated_at": datetime.now(pytz.UTC).isoformat()},
            timeout=SUPABASE_TIMEOUT,
        )
    except Exception as e:
        print("PORTAL HANDOFF SYNC WARN:", repr(e))
    try:
        sesion_estado = "derivado" if estado == "pendiente" else ("en_atencion" if estado == "en_atencion" else "cerrado")
        requests.patch(
            f"{SUPABASE_URL}/rest/v1/nexi_core_handoff_sesiones",
            headers={**headers, "Prefer": "return=minimal"},
            params={"empresa_id": f"eq.{conv.get('empresa_id')}", "identificador": f"eq.{ident}", "canal": f"eq.{conv.get('canal') or 'whatsapp'}"},
            json={"estado": sesion_estado, "updated_at": datetime.now(pytz.UTC).isoformat()},
            timeout=SUPABASE_TIMEOUT,
        )
    except Exception as e:
        print("PORTAL HANDOFF SESSION SYNC WARN:", repr(e))


@app.route("/portal/conversacion/<conversacion_id>/tomar", methods=["POST", "OPTIONS"])
def portal_tomar_conversacion(conversacion_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    try:
        conv = obtener_conversacion_supabase(conversacion_id, perfil)
        if not conv:
            return portal_json({"ok": False, "error": "Conversación no encontrada"}, 404)
        activar_por_empresa(conv.get("empresa_id"), canal=conv.get("canal"))
        establecer_modo_atencion(conversacion_id, "ejecutivo")
        _portal_actualizar_handoff_por_conversacion(conv, "en_atencion")
        return portal_json({"ok": True, "modo": "ejecutivo", "estado_handoff": "en_atencion"})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


@app.route("/portal/conversacion/<conversacion_id>/cerrar", methods=["POST", "OPTIONS"])
def portal_cerrar_conversacion(conversacion_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    try:
        conv = obtener_conversacion_supabase(conversacion_id, perfil)
        if not conv:
            return portal_json({"ok": False, "error": "Conversación no encontrada"}, 404)
        activar_por_empresa(conv.get("empresa_id"), canal=conv.get("canal"))
        establecer_modo_atencion(conversacion_id, "bot")
        _portal_actualizar_handoff_por_conversacion(conv, "cerrado")
        return portal_json({"ok": True, "modo": "bot", "estado_handoff": "cerrado"})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)



@app.route("/portal/agenda/eventos", methods=["GET", "OPTIONS"])
def portal_agenda_eventos():
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)

    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)

    try:
        headers = supabase_headers()
        params = {
            "select":"id,empresa_id,telefono,nombre_cliente,servicio,fecha,hora,estado,google_event_id,google_status,google_html_link,inicio_at,fin_at,correo,origen",
            "order":"fecha.asc,hora.asc",
            "limit":"1000",
        }

        if not es_superadmin(perfil):
            params["empresa_id"] = f"eq.{perfil.get('empresa_id')}"

        start = str(request.args.get("start") or "").strip()[:10]
        end = str(request.args.get("end") or "").strip()[:10]
        estado = str(request.args.get("estado") or "").strip()
        servicio = str(request.args.get("servicio") or "").strip()

        if start:
            params["fecha"] = f"gte.{start}"
        if end:
            # FullCalendar entrega end exclusivo; lo dejamos como lt.
            params["and"] = f"(fecha.gte.{start or '1900-01-01'},fecha.lt.{end})"
            params.pop("fecha", None)
        if estado:
            params["estado"] = f"eq.{estado}"
        if servicio:
            params["servicio"] = f"eq.{servicio}"

        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/reservas",
            headers=headers,
            params=params,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() if r.content else []

        events = []
        for row in rows:
            fecha = str(row.get("fecha") or "").strip()
            hora = str(row.get("hora") or "").strip()
            if not fecha:
                continue

            start_iso = row.get("inicio_at")
            end_iso = row.get("fin_at")

            if not start_iso:
                start_iso = f"{fecha}T{(hora[:5] if hora else '00:00')}:00"

            if not end_iso:
                try:
                    base = datetime.fromisoformat(start_iso.replace("Z","+00:00"))
                    if base.tzinfo is None:
                        base = zona_local().localize(base)
                    end_iso = (base + timedelta(minutes=cfg_int("duracion_reserva", DEFAULT_DURACION_RESERVA))).isoformat()
                except Exception:
                    end_iso = None

            estado_row = str(row.get("estado") or "confirmada").lower()
            title = str(row.get("servicio") or "Reserva")
            cliente = str(row.get("nombre_cliente") or row.get("telefono") or "").strip()
            if cliente:
                title += f" · {cliente}"

            events.append({
                "id":str(row.get("id")),
                "title":title,
                "start":start_iso,
                "end":end_iso,
                "editable": bool(row.get("google_event_id")) and estado_row != "cancelada",
                "classNames":[
                    "nexia-event",
                    f"estado-{re.sub(r'[^a-z0-9_-]+','-',estado_row)}"
                ],
                "extendedProps":{
                    "cliente":row.get("nombre_cliente"),
                    "telefono":row.get("telefono"),
                    "correo":row.get("correo"),
                    "servicio":row.get("servicio"),
                    "estado":row.get("estado"),
                    "origen":row.get("origen") or ("Google Calendar" if row.get("google_event_id") else "Nexia"),
                    "google_event_id":row.get("google_event_id"),
                    "google_html_link":row.get("google_html_link"),
                    "google_status":row.get("google_status"),
                    "fecha":row.get("fecha"),
                    "hora":row.get("hora"),
                }
            })

        return portal_json({"ok":True,"events":events})

    except Exception as e:
        print("PORTAL CALENDAR EVENTS ERROR:",repr(e))
        return portal_json({"ok":False,"error":"No pude cargar los eventos del calendario"},500)


@app.route("/portal/agenda", methods=["GET", "OPTIONS"])
def portal_agenda():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    headers = supabase_headers()
    try:
        # Para clientes normales, Agenda se reconcilia con Google Calendar antes de leer.
        if CALENDAR_SYNC_AUTO_PORTAL and not es_superadmin(perfil) and perfil.get("empresa_id"):
            try:
                sincronizar_google_calendar_empresa(str(perfil.get("empresa_id")))
            except Exception as sync_error:
                print("PORTAL AGENDA AUTO SYNC ERROR:", repr(sync_error))

        filtros = {}
        if not es_superadmin(perfil):
            filtros["empresa_id"] = f"eq.{perfil.get('empresa_id')}"
        reservas = []
        solicitudes = []
        r = requests.get(f"{SUPABASE_URL}/rest/v1/reservas", headers=headers, params={"select":"*", "order":"fecha.desc,hora.desc", "limit":"300", **filtros}, timeout=SUPABASE_TIMEOUT)
        if r.ok:
            reservas = r.json() if r.content else []
        r2 = requests.get(f"{SUPABASE_URL}/rest/v1/nexi_core_solicitudes_agenda", headers=headers, params={"select":"*", "order":"created_at.desc", "limit":"300", **filtros}, timeout=SUPABASE_TIMEOUT)
        if r2.ok:
            solicitudes = r2.json() if r2.content else []
        return portal_json({"ok": True, "reservas": reservas, "solicitudes": solicitudes})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


@app.route("/portal/consumo", methods=["GET", "OPTIONS"])
def portal_consumo():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    try:
        headers = supabase_headers()
        params = {"select":"*", "order":"updated_at.desc", "limit":"500"}
        if not es_superadmin(perfil):
            params["empresa_id"] = f"eq.{perfil.get('empresa_id')}"
        r = requests.get(f"{SUPABASE_URL}/rest/v1/suscripciones_empresa", headers=headers, params=params, timeout=SUPABASE_TIMEOUT)
        r.raise_for_status()
        filas = r.json() if r.content else []
        ahora = datetime.now(pytz.UTC)
        for fila in filas:
            limite = int(fila.get("limite_mensajes") or 0)
            usados = int(fila.get("mensajes_usados") or 0)
            fila["mensajes_restantes"] = max(0, limite - usados) if limite else None
            tipo = str(fila.get("tipo_plan") or "").lower()
            fin = _parse_iso(fila.get("periodo_fin") if tipo in {"nexia_500","nexia_1000"} else fila.get("demo_fin"))
            vigente = str(fila.get("estado") or "activo").lower() == "activo"
            if fin and ahora >= fin.astimezone(pytz.UTC):
                vigente = False
                fila["motivo_fin"] = "tiempo"
            elif limite and usados >= limite:
                vigente = False
                fila["motivo_fin"] = "limite"
            fila["vigente"] = vigente
            fila["estado_efectivo"] = "activo" if vigente else "finalizado"
        return portal_json({"ok": True, "planes": filas})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)



# ============================================================
# PORTAL NEXIA - CENTRO DE DEMOS V1.9.1
# ============================================================

@app.route("/portal/demos", methods=["GET", "OPTIONS"])
def portal_demos():
    """Listado de demos visible al superadmin y, si corresponde, a la propia empresa."""
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    headers = backend_headers()
    if not headers:
        return portal_json({"ok": False, "error": "Supabase backend no configurado"}, 500)
    try:
        params = {"select": "*", "tipo_plan": "eq.demo", "order": "updated_at.desc", "limit": "500"}
        if not es_superadmin(perfil):
            params["empresa_id"] = f"eq.{perfil.get('empresa_id')}"
        rp = requests.get(f"{SUPABASE_URL}/rest/v1/suscripciones_empresa", headers=headers, params=params, timeout=SUPABASE_TIMEOUT)
        rp.raise_for_status()
        planes = rp.json() if rp.content else []
        ids = [str(x.get("empresa_id") or "").strip() for x in planes if x.get("empresa_id")]
        if not ids:
            return portal_json({"ok": True, "demos": []})

        # Empresas
        empresas = {}
        re_ = requests.get(f"{SUPABASE_URL}/rest/v1/empresas", headers=headers, params={"select":"id,nombre,activo,created_at", "id": f"in.({','.join(ids)})", "limit":"500"}, timeout=SUPABASE_TIMEOUT)
        if re_.ok:
            empresas = {str(x.get("id")): x for x in (re_.json() if re_.content else [])}

        # Accesos demo (WhatsApp/otros canales)
        accesos = {}
        ra = requests.get(f"{SUPABASE_URL}/rest/v1/demo_accesos", headers=headers, params={"select":"empresa_id,canal,identificador_cliente,activo,inicio,fin,updated_at", "empresa_id": f"in.({','.join(ids)})", "order":"updated_at.desc", "limit":"1000"}, timeout=SUPABASE_TIMEOUT)
        if ra.ok:
            for x in (ra.json() if ra.content else []):
                eid=str(x.get("empresa_id") or "")
                accesos.setdefault(eid, x)

        # Onboarding / datos del creador
        onboarding = {}
        ro = requests.get(f"{SUPABASE_URL}/rest/v1/nexi_core_onboarding", headers=headers, params={"select":"empresa_id,token,datos,created_at,updated_at,completado", "empresa_id": f"in.({','.join(ids)})", "order":"updated_at.desc", "limit":"1000"}, timeout=SUPABASE_TIMEOUT)
        if ro.ok:
            for x in (ro.json() if ro.content else []):
                eid=str(x.get("empresa_id") or "")
                onboarding.setdefault(eid, x)

        ahora = datetime.now(pytz.UTC)
        salida=[]
        for p in planes:
            eid=str(p.get("empresa_id") or "")
            emp=empresas.get(eid,{})
            acc=accesos.get(eid,{})
            onb=onboarding.get(eid,{})
            datos=dict(onb.get("datos") or {})
            usados=int(p.get("mensajes_usados") or p.get("respuestas_usadas") or 0)
            limite=int(p.get("limite_mensajes") or p.get("limite_respuestas") or DEMO_LIMITE_MENSAJES_DEFAULT)
            fin=_parse_iso(p.get("demo_fin"))
            estado=str(p.get("estado") or "activo").lower()
            if estado == "activo" and fin and ahora > fin:
                estado_visual="vencida"
            elif estado == "activo" and limite and usados >= limite:
                estado_visual="agotada"
            else:
                estado_visual=estado
            salida.append({
                "empresa_id": eid,
                "empresa_nombre": emp.get("nombre") or datos.get("nombre_negocio") or "Empresa demo",
                "contacto": datos.get("nombre_contacto"),
                "email": datos.get("email_contacto"),
                "rubro": datos.get("rubro"),
                "asistente": datos.get("nombre_asistente"),
                "canal": acc.get("canal"),
                "identificador_cliente": acc.get("identificador_cliente"),
                "acceso_activo": bool(acc.get("activo")) if acc else False,
                "estado": estado_visual,
                "mensajes_usados": usados,
                "limite_mensajes": limite,
                "mensajes_restantes": max(0, limite-usados),
                "demo_inicio": p.get("demo_inicio"),
                "demo_fin": p.get("demo_fin"),
                "onboarding_token": onb.get("token"),
                "created_at": onb.get("created_at") or emp.get("created_at"),
            })
        return portal_json({"ok": True, "demos": salida})
    except Exception as e:
        print("PORTAL DEMOS ERROR:", repr(e))
        return portal_json({"ok": False, "error": str(e)[:300]}, 500)


@app.route("/portal/admin/demo/<empresa_id>/reactivar", methods=["POST", "OPTIONS"])
def portal_admin_reactivar_demo(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    if not portal_admin_autorizado():
        return portal_json({"ok": False, "error": "Administrador Nexia no autorizado"}, 403)
    try:
        headers=backend_headers()
        identificador=None; canal="whatsapp"
        r=requests.get(f"{SUPABASE_URL}/rest/v1/demo_accesos", headers=headers, params={"select":"identificador_cliente,canal", "empresa_id":f"eq.{empresa_id}", "order":"updated_at.desc", "limit":"1"}, timeout=SUPABASE_TIMEOUT)
        if r.ok:
            rows=r.json() if r.content else []
            if rows:
                identificador=rows[0].get("identificador_cliente")
                canal=rows[0].get("canal") or "whatsapp"
        demo=activar_demo_empresa(empresa_id, identificador, canal)
        return portal_json({"ok":True,"demo":demo})
    except Exception as e:
        print("PORTAL REACTIVAR DEMO ERROR:",repr(e))
        return portal_json({"ok":False,"error":str(e)[:300]},500)


@app.route("/portal/admin/demo/<empresa_id>/desactivar", methods=["POST", "OPTIONS"])
def portal_admin_desactivar_demo(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    if not portal_admin_autorizado():
        return portal_json({"ok": False, "error": "Administrador Nexia no autorizado"}, 403)
    try:
        headers=backend_headers()
        ahora=datetime.now(pytz.UTC).isoformat()
        rp=requests.patch(f"{SUPABASE_URL}/rest/v1/suscripciones_empresa", headers={**headers,"Prefer":"return=minimal"}, params={"empresa_id":f"eq.{empresa_id}"}, json={"estado":"finalizado","demo_fin":ahora,"updated_at":ahora}, timeout=SUPABASE_TIMEOUT)
        rp.raise_for_status()
        ra=requests.patch(f"{SUPABASE_URL}/rest/v1/demo_accesos", headers={**headers,"Prefer":"return=minimal"}, params={"empresa_id":f"eq.{empresa_id}"}, json={"activo":False,"fin":ahora,"updated_at":ahora}, timeout=SUPABASE_TIMEOUT)
        if not ra.ok:
            print("PORTAL DESACTIVAR DEMO ACCESO WARN:",ra.status_code,ra.text[:300])
        return portal_json({"ok":True,"empresa_id":empresa_id,"estado":"finalizado"})
    except Exception as e:
        print("PORTAL DESACTIVAR DEMO ERROR:",repr(e))
        return portal_json({"ok":False,"error":str(e)[:300]},500)

@app.route("/portal/estadisticas", methods=["GET", "OPTIONS"])
def portal_estadisticas():
    """
    Estadísticas del portal.

    Usuario empresa:
      - ve solo sus propios mensajes/conversaciones.

    superadmin:
      - ve el total global de todas las empresas.
      - recibe además un desglose por empresa.
    """
    if request.method == "OPTIONS":
        from flask import make_response
        return portal_cors_response(make_response("", 204))

    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)

    try:
        rol = str(perfil.get("rol") or "").strip().lower()

        if rol != "superadmin":
            empresa_id = str(perfil.get("empresa_id") or "").strip()

            headers = supabase_headers()
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/empresas",
                headers=headers,
                params={
                    "select": "id,nombre",
                    "id": f"eq.{empresa_id}",
                    "limit": "1",
                },
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()
            filas = r.json() if r.content else []
            nombre = filas[0].get("nombre") if filas else None

            datos = _estadisticas_empresa(empresa_id, nombre)

            return portal_json({
                "ok": True,
                "alcance": "empresa",
                "totales": datos,
                "empresas": [datos],
            })

        # SUPERADMIN: suma todas las empresas operativas.
        headers = supabase_headers()
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/empresas",
            headers=headers,
            params={
                "select": "id,nombre,activo",
                "order": "nombre.asc",
                "limit": "1000",
            },
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        empresas = r.json() if r.content else []

        detalle = []
        total_recibidos = 0
        total_enviados = 0
        total_conversaciones = 0

        for empresa in empresas:
            empresa_id = str(empresa.get("id") or "").strip()
            if not empresa_id:
                continue

            estadistica = _estadisticas_empresa(
                empresa_id,
                empresa.get("nombre"),
            )
            detalle.append(estadistica)

            total_recibidos += estadistica["mensajes_recibidos"]
            total_enviados += estadistica["mensajes_enviados"]
            total_conversaciones += estadistica["conversaciones"]

        return portal_json({
            "ok": True,
            "alcance": "global",
            "totales": {
                "mensajes_recibidos": total_recibidos,
                "mensajes_enviados": total_enviados,
                "mensajes_totales": total_recibidos + total_enviados,
                "conversaciones": total_conversaciones,
                "empresas": len(detalle),
            },
            "empresas": detalle,
        })

    except Exception as e:
        print("PORTAL ESTADISTICAS ERROR:", repr(e))
        return portal_json(
            {"ok": False, "error": str(e)[:300]},
            500,
        )


@app.route("/portal/admin/empresas", methods=["GET", "POST", "OPTIONS"])
def portal_admin_empresas():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    if not portal_admin_autorizado():
        return admin_json_error("Administrador Nexia no autorizado", 403)

    headers = backend_headers()
    if not headers:
        return admin_json_error("Supabase backend no configurado", 500)

    try:
        if request.method == "GET":
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/empresas",
                headers=headers,
                params={"select": "id,nombre,activo,created_at", "order": "created_at.desc"},
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()
            return portal_json({"ok": True, "empresas": r.json() if r.content else []})

        data = request.get_json(silent=True) or {}
        nombre = str(data.get("nombre") or "").strip()
        if not nombre:
            return admin_json_error("El nombre de la empresa es obligatorio")

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/empresas",
            headers={**headers, "Prefer": "return=representation"},
            json={"nombre": nombre, "activo": bool(data.get("activo", True))},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        empresa = (r.json() or [None])[0]

        requests.post(
            f"{SUPABASE_URL}/rest/v1/configuracion_bot",
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json={
                "empresa_id": empresa["id"],
                "tipo_negocio": "reservas",
                "descripcion_empresa": "",
                "asistente_nombre": nombre,
                "timezone": "America/Santiago",
                "calendar_id": "primary",
                "hora_apertura": 9,
                "hora_cierre": 18,
                "duracion_reserva": 60,
                "dias_atencion": [0,1,2,3,4,5],
                "modulos": {
                    "ia": True, "reservas": True, "handoff_humano": True,
                    "whatsapp": True, "instagram": True
                },
                "prompt_extra": ""
            },
            timeout=SUPABASE_TIMEOUT,
        )
        return portal_json({"ok": True, "empresa": empresa}, 201)
    except Exception as e:
        print("PORTAL ADMIN EMPRESAS ERROR:", repr(e))
        return admin_json_error("No se pudo procesar la empresa", 500)


@app.route("/portal/admin/empresa/<empresa_id>", methods=["GET", "PATCH", "OPTIONS"])
def portal_admin_empresa(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    if not portal_admin_autorizado():
        return admin_json_error("Administrador Nexia no autorizado", 403)

    headers = backend_headers()
    if not headers:
        return admin_json_error("Supabase backend no configurado", 500)

    try:
        if request.method == "PATCH":
            data = request.get_json(silent=True) or {}
            payload = {}
            if "nombre" in data:
                payload["nombre"] = str(data.get("nombre") or "").strip()
            if "activo" in data:
                payload["activo"] = bool(data.get("activo"))
            if payload:
                r = requests.patch(
                    f"{SUPABASE_URL}/rest/v1/empresas",
                    headers={**headers, "Prefer": "return=representation"},
                    params={"id": f"eq.{empresa_id}"},
                    json=payload,
                    timeout=SUPABASE_TIMEOUT,
                )
                r.raise_for_status()

        er = requests.get(
            f"{SUPABASE_URL}/rest/v1/empresas",
            headers=headers,
            params={"select": "id,nombre,activo,created_at", "id": f"eq.{empresa_id}", "limit": "1"},
            timeout=SUPABASE_TIMEOUT,
        )
        er.raise_for_status()
        empresas = er.json() if er.content else []
        if not empresas:
            return admin_json_error("Empresa no encontrada", 404)

        cr = requests.get(
            f"{SUPABASE_URL}/rest/v1/configuracion_bot",
            headers=headers,
            params={"select": "*", "empresa_id": f"eq.{empresa_id}", "limit": "1"},
            timeout=SUPABASE_TIMEOUT,
        )
        cr.raise_for_status()

        sr = requests.get(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers=headers,
            params={"select": "*", "empresa_id": f"eq.{empresa_id}", "order": "orden.asc"},
            timeout=SUPABASE_TIMEOUT,
        )
        sr.raise_for_status()

        chr_ = requests.get(
            f"{SUPABASE_URL}/rest/v1/canales_empresa",
            headers=headers,
            params={
                "select": "id,empresa_id,canal,provider,identificador_externo,sender,es_principal,activo,app_name,created_at",
                "empresa_id": f"eq.{empresa_id}",
                "order": "created_at.asc"
            },
            timeout=SUPABASE_TIMEOUT,
        )
        chr_.raise_for_status()

        return portal_json({
            "ok": True,
            "empresa": empresas[0],
            "configuracion": (cr.json() if cr.content else [None])[0],
            "servicios": sr.json() if sr.content else [],
            "canales": chr_.json() if chr_.content else []
        })
    except Exception as e:
        print("PORTAL ADMIN EMPRESA ERROR:", repr(e))
        return admin_json_error("No se pudo cargar la empresa", 500)


@app.route("/portal/admin/configuracion/<empresa_id>", methods=["PATCH", "OPTIONS"])
def portal_admin_configuracion(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    if not portal_admin_autorizado():
        return admin_json_error("Administrador Nexia no autorizado", 403)

    headers = backend_headers()
    if not headers:
        return admin_json_error("Supabase backend no configurado", 500)

    try:
        data = request.get_json(silent=True) or {}
        allowed = {
            "tipo_negocio", "descripcion_empresa", "asistente_nombre", "direccion", "telefono_ejecutivo", "correo_ejecutivo",
            "timezone", "calendar_id", "hora_apertura", "hora_cierre",
            "duracion_reserva", "dias_atencion", "modulos", "prompt_extra"
        }
        payload = {k: data[k] for k in allowed if k in data}
        payload["empresa_id"] = empresa_id

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/configuracion_bot",
            headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()

        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)

        rows = r.json() if r.content else []
        return portal_json({"ok": True, "configuracion": rows[0] if rows else payload})
    except Exception as e:
        print("PORTAL ADMIN CONFIG ERROR:", repr(e))
        return admin_json_error("No se pudo guardar la configuración", 500)


@app.route("/portal/admin/servicios/<empresa_id>", methods=["POST", "OPTIONS"])
def portal_admin_servicios_crear(empresa_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    if not portal_admin_autorizado():
        return admin_json_error("Administrador Nexia no autorizado", 403)

    headers = backend_headers()
    if not headers:
        return admin_json_error("Supabase backend no configurado", 500)

    try:
        data = request.get_json(silent=True) or {}
        codigo = str(data.get("codigo") or "").strip()
        nombre = str(data.get("nombre") or "").strip()
        if not codigo or not nombre:
            return admin_json_error("Código y nombre son obligatorios")

        payload = {
            "empresa_id": empresa_id,
            "codigo": codigo,
            "numero": data.get("numero"),
            "nombre": nombre,
            "categoria": str(data.get("categoria") or "Servicios"),
            "precio": int(data.get("precio") or 0),
            "precio_texto": str(data.get("precio_texto") or ""),
            "detalle": str(data.get("detalle") or ""),
            "aliases": data.get("aliases") or [],
            "duracion_minutos": int(data.get("duracion_minutos") or 60),
            "orden": int(data.get("orden") or 100),
            "activo": bool(data.get("activo", True))
        }

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers={**headers, "Prefer": "return=representation"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)

        rows = r.json() if r.content else []
        return portal_json({"ok": True, "servicio": rows[0] if rows else payload}, 201)
    except Exception as e:
        print("PORTAL ADMIN CREAR SERVICIO ERROR:", repr(e))
        return admin_json_error("No se pudo crear el servicio", 500)


@app.route("/portal/admin/servicio/<servicio_id>", methods=["PATCH", "DELETE", "OPTIONS"])
def portal_admin_servicio(servicio_id):
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    if not portal_admin_autorizado():
        return admin_json_error("Administrador Nexia no autorizado", 403)

    headers = backend_headers()
    if not headers:
        return admin_json_error("Supabase backend no configurado", 500)

    try:
        qr = requests.get(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers=headers,
            params={"select": "id,empresa_id", "id": f"eq.{servicio_id}", "limit": "1"},
            timeout=SUPABASE_TIMEOUT,
        )
        qr.raise_for_status()
        found = qr.json() if qr.content else []
        if not found:
            return admin_json_error("Servicio no encontrado", 404)
        empresa_id = found[0]["empresa_id"]

        if request.method == "DELETE":
            r = requests.delete(
                f"{SUPABASE_URL}/rest/v1/servicios",
                headers=headers,
                params={"id": f"eq.{servicio_id}"},
                timeout=SUPABASE_TIMEOUT,
            )
            r.raise_for_status()
            with TENANT_CACHE_LOCK:
                TENANT_CACHE.pop(f"empresa:{empresa_id}", None)
            return portal_json({"ok": True})

        data = request.get_json(silent=True) or {}
        allowed = {
            "codigo","numero","nombre","categoria","precio","precio_texto",
            "detalle","aliases","duracion_minutos","orden","activo"
        }
        payload = {k: data[k] for k in allowed if k in data}

        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers={**headers, "Prefer": "return=representation"},
            params={"id": f"eq.{servicio_id}"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()

        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)

        rows = r.json() if r.content else []
        return portal_json({"ok": True, "servicio": rows[0] if rows else payload})
    except Exception as e:
        print("PORTAL ADMIN SERVICIO ERROR:", repr(e))
        return admin_json_error("No se pudo modificar el servicio", 500)



# ============================================================
# NEXIA V2.9 - CONFIGURACION AUTOGESTION DEL CLIENTE
# ============================================================

def _portal_empresa_propia():
    perfil = portal_usuario_autorizado()
    if not perfil:
        return None, None
    empresa_id = str(perfil.get("empresa_id") or "").strip()
    if not empresa_id:
        return perfil, None
    return perfil, empresa_id


def _portal_perfil_core_empresa(empresa_id):
    r = requests.get(
        f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",
        headers=backend_headers(),
        params={"select":"*", "empresa_id":f"eq.{empresa_id}", "limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json() if r.content else []
    return rows[0] if rows else None


def _portal_config_snapshot(empresa_id):
    headers = backend_headers()

    er = requests.get(
        f"{SUPABASE_URL}/rest/v1/empresas",
        headers=headers,
        params={"select":"id,nombre,activo", "id":f"eq.{empresa_id}", "limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    er.raise_for_status()
    empresas = er.json() if er.content else []
    if not empresas:
        raise RuntimeError("Empresa no encontrada")

    cr = requests.get(
        f"{SUPABASE_URL}/rest/v1/configuracion_bot",
        headers=headers,
        params={"select":"*", "empresa_id":f"eq.{empresa_id}", "limit":"1"},
        timeout=SUPABASE_TIMEOUT,
    )
    cr.raise_for_status()
    configs = cr.json() if cr.content else []
    conf = configs[0] if configs else {}

    perfil_core = _portal_perfil_core_empresa(empresa_id)
    datos = dict((perfil_core or {}).get("datos") or {})

    sr = requests.get(
        f"{SUPABASE_URL}/rest/v1/servicios",
        headers=headers,
        params={
            "select":"*",
            "empresa_id":f"eq.{empresa_id}",
            "order":"orden.asc,created_at.asc",
        },
        timeout=SUPABASE_TIMEOUT,
    )
    sr.raise_for_status()

    return {
        "empresa": empresas[0],
        "configuracion": conf,
        "perfil_core": perfil_core,
        "datos": datos,
        "servicios": sr.json() if sr.content else [],
    }


@app.route("/portal/configuracion-negocio", methods=["GET", "PATCH", "OPTIONS"])
def portal_configuracion_negocio():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)

    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    if not empresa_id:
        return portal_json({"ok":False,"error":"Tu usuario no tiene una empresa asociada"},409)

    headers = backend_headers()
    if not headers:
        return portal_json({"ok":False,"error":"Supabase backend no configurado"},500)

    try:
        if request.method == "GET":
            snap = _portal_config_snapshot(empresa_id)
            return portal_json({"ok":True, **snap})

        data = request.get_json(silent=True) or {}
        ahora = datetime.now(pytz.UTC).isoformat()

        # Empresa
        nombre_negocio = str(data.get("nombre_negocio") or "").strip()
        if nombre_negocio:
            er = requests.patch(
                f"{SUPABASE_URL}/rest/v1/empresas",
                headers={**headers, "Prefer":"return=representation"},
                params={"id":f"eq.{empresa_id}"},
                json={"nombre":nombre_negocio},
                timeout=SUPABASE_TIMEOUT,
            )
            er.raise_for_status()

        # Perfil Core: conserva toda la información previa y reemplaza solo lo editado.
        perfil_core = _portal_perfil_core_empresa(empresa_id)
        datos = dict((perfil_core or {}).get("datos") or {})

        profile_fields = {
            "nombre_negocio", "nombre_asistente", "rubro", "objetivo", "tono",
            "atiende_direccion_fisica", "direccion", "comuna",
            "referencia_direccion", "google_maps_url",
        }
        for key in profile_fields:
            if key in data:
                datos[key] = data.get(key)

        # Dirección: si no tiene atención física no publicar una dirección antigua.
        atiende = bool(data.get("atiende_direccion_fisica", datos.get("atiende_direccion_fisica", False)))
        datos["atiende_direccion_fisica"] = atiende
        if atiende:
            direccion = str(data.get("direccion") if "direccion" in data else datos.get("direccion") or "").strip()
            comuna = str(data.get("comuna") if "comuna" in data else datos.get("comuna") or "").strip()
            referencia = str(data.get("referencia_direccion") if "referencia_direccion" in data else datos.get("referencia_direccion") or "").strip()
            maps = str(data.get("google_maps_url") if "google_maps_url" in data else datos.get("google_maps_url") or "").strip()
            direccion_completa = ", ".join(x for x in (direccion, comuna) if x)
            if direccion and not maps:
                maps = "https://www.google.com/maps/search/?api=1&query=" + quote(direccion_completa, safe="")
            datos["direccion"] = direccion_completa or direccion
            datos["comuna"] = comuna
            datos["referencia_direccion"] = referencia
            datos["google_maps_url"] = maps
        else:
            datos["direccion"] = ""
            datos["comuna"] = ""
            datos["referencia_direccion"] = ""
            datos["google_maps_url"] = ""

        if perfil_core:
            pr = requests.patch(
                f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",
                headers={**headers, "Prefer":"return=representation"},
                params={"empresa_id":f"eq.{empresa_id}"},
                json={"datos":datos,"updated_at":ahora},
                timeout=SUPABASE_TIMEOUT,
            )
        else:
            pr = requests.post(
                f"{SUPABASE_URL}/rest/v1/nexi_core_perfiles",
                headers={**headers, "Prefer":"return=representation"},
                json={
                    "empresa_id":empresa_id,
                    "version":CORE_ONBOARDING_VERSION,
                    "tipo_cliente":"empresa",
                    "datos":datos,
                    "updated_at":ahora,
                    "created_at":ahora,
                },
                timeout=SUPABASE_TIMEOUT,
            )
        pr.raise_for_status()

        # Configuración que utiliza el runtime.
        conf_payload = {"empresa_id": empresa_id}
        mapping = {
            "rubro":"tipo_negocio",
            "descripcion_empresa":"descripcion_empresa",
            "nombre_asistente":"asistente_nombre",
            "email_contacto":"correo_ejecutivo",
            "hora_apertura":"hora_apertura",
            "hora_cierre":"hora_cierre",
            "duracion_reserva":"duracion_reserva",
            "dias_atencion":"dias_atencion",
            "prompt_extra":"prompt_extra",
        }
        for source, target in mapping.items():
            if source in data:
                conf_payload[target] = data.get(source)

        conf_payload["direccion"] = datos.get("direccion") if atiende else ""

        if len(conf_payload) > 1:
            cr = requests.post(
                f"{SUPABASE_URL}/rest/v1/configuracion_bot",
                headers={**headers, "Prefer":"resolution=merge-duplicates,return=representation"},
                params={"on_conflict":"empresa_id"},
                json=conf_payload,
                timeout=SUPABASE_TIMEOUT,
            )
            cr.raise_for_status()

        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)

        snap = _portal_config_snapshot(empresa_id)
        return portal_json({
            "ok":True,
            "mensaje":"Configuración actualizada. Tu asistente ya utilizará los nuevos datos.",
            **snap,
        })

    except Exception as e:
        print("PORTAL CONFIG NEGOCIO ERROR:", repr(e))
        return portal_json({"ok":False,"error":"No se pudo guardar la configuración"},500)


@app.route("/portal/configuracion-negocio/servicios", methods=["POST", "OPTIONS"])
def portal_configuracion_servicio_crear():
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)

    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    if not empresa_id:
        return portal_json({"ok":False,"error":"Tu usuario no tiene una empresa asociada"},409)

    try:
        data = request.get_json(silent=True) or {}
        nombre = str(data.get("nombre") or "").strip()
        if not nombre:
            return portal_json({"ok":False,"error":"El nombre del producto o servicio es obligatorio"},400)

        precio_texto = str(data.get("precio_texto") or "").strip()
        digitos = re.sub(r"\D","",precio_texto)
        precio = int(digitos) if digitos else int(data.get("precio") or 0)
        codigo = str(data.get("codigo") or "").strip()
        if not codigo:
            codigo = "portal_" + uuid.uuid4().hex[:10]

        payload = {
            "empresa_id":empresa_id,
            "codigo":codigo,
            "nombre":nombre[:180],
            "categoria":str(data.get("categoria") or "Productos y servicios")[:100],
            "precio":precio,
            "precio_texto":precio_texto or ("Valor por confirmar" if not precio else str(precio)),
            "detalle":str(data.get("detalle") or "")[:1000],
            "aliases":data.get("aliases") or [],
            "duracion_minutos":int(data.get("duracion_minutos") or 60),
            "orden":int(data.get("orden") or 100),
            "activo":bool(data.get("activo",True)),
        }

        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers={**backend_headers(),"Prefer":"return=representation"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)
        rows=r.json() if r.content else []
        return portal_json({"ok":True,"servicio":rows[0] if rows else payload},201)
    except Exception as e:
        print("PORTAL CONFIG SERVICIO CREAR ERROR:",repr(e))
        return portal_json({"ok":False,"error":"No se pudo crear el producto o servicio"},500)


@app.route("/portal/configuracion-negocio/servicio/<servicio_id>", methods=["PATCH","DELETE","OPTIONS"])
def portal_configuracion_servicio(servicio_id):
    if request.method == "OPTIONS":
        return portal_json({"ok":True},204)

    perfil, empresa_id = _portal_empresa_propia()
    if not perfil:
        return portal_json({"ok":False,"error":"Sesión no autorizada"},401)
    if not empresa_id:
        return portal_json({"ok":False,"error":"Tu usuario no tiene una empresa asociada"},409)

    try:
        headers=backend_headers()
        qr=requests.get(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers=headers,
            params={"select":"id,empresa_id","id":f"eq.{servicio_id}","empresa_id":f"eq.{empresa_id}","limit":"1"},
            timeout=SUPABASE_TIMEOUT,
        )
        qr.raise_for_status()
        rows=qr.json() if qr.content else []
        if not rows:
            return portal_json({"ok":False,"error":"Producto o servicio no encontrado"},404)

        if request.method=="DELETE":
            dr=requests.delete(
                f"{SUPABASE_URL}/rest/v1/servicios",
                headers=headers,
                params={"id":f"eq.{servicio_id}","empresa_id":f"eq.{empresa_id}"},
                timeout=SUPABASE_TIMEOUT,
            )
            dr.raise_for_status()
            with TENANT_CACHE_LOCK:
                TENANT_CACHE.pop(f"empresa:{empresa_id}", None)
            return portal_json({"ok":True})

        data=request.get_json(silent=True) or {}
        payload={}
        for key in ("nombre","categoria","detalle","duracion_minutos","orden","activo"):
            if key in data:
                payload[key]=data[key]

        if "precio_texto" in data:
            precio_texto=str(data.get("precio_texto") or "").strip()
            payload["precio_texto"]=precio_texto or "Valor por confirmar"
            digitos=re.sub(r"\D","",precio_texto)
            payload["precio"]=int(digitos) if digitos else 0

        if "nombre" in payload:
            payload["nombre"]=str(payload["nombre"] or "").strip()[:180]
            if not payload["nombre"]:
                return portal_json({"ok":False,"error":"El nombre no puede quedar vacío"},400)

        ur=requests.patch(
            f"{SUPABASE_URL}/rest/v1/servicios",
            headers={**headers,"Prefer":"return=representation"},
            params={"id":f"eq.{servicio_id}","empresa_id":f"eq.{empresa_id}"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        ur.raise_for_status()
        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)
        updated=ur.json() if ur.content else []
        return portal_json({"ok":True,"servicio":updated[0] if updated else payload})
    except Exception as e:
        print("PORTAL CONFIG SERVICIO ERROR:",repr(e))
        return portal_json({"ok":False,"error":"No se pudo modificar el producto o servicio"},500)


# ============================================================
# NEXIA V2.1 - GOOGLE CALENDAR OAUTH MULTIEMPRESA
# ============================================================
GOOGLE_CALENDAR_REDIRECT_URI = os.getenv(
    "GOOGLE_CALENDAR_REDIRECT_URI",
    f"{PUBLIC_BACKEND_URL}/oauth/google/calendar/callback",
).strip()
GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"


def _google_state_encode(empresa_id, demo_token=None):
    """State OAuth firmado. Conserva el acceso temporal de prueba sin exponerlo sin firma."""
    issued = int(datetime.now(pytz.UTC).timestamp())
    data = {"empresa_id": str(empresa_id), "iat": issued}
    demo_token = str(demo_token or "").strip()
    if demo_token:
        data["demo_token"] = demo_token
    payload = json.dumps(data, separators=(",", ":"))
    raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(str(app.secret_key).encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def _google_state_decode(state):
    try:
        raw, sig = str(state or "").rsplit(".", 1)
        expected = hmac.new(str(app.secret_key).encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padded = raw + "=" * (-len(raw) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        iat = int(data.get("iat") or 0)
        if abs(int(datetime.now(pytz.UTC).timestamp()) - iat) > 900:
            return None
        return data
    except Exception:
        return None


def _portal_empresa_para_integracion(perfil, requested_empresa_id=None):
    if es_superadmin(perfil):
        eid = str(requested_empresa_id or "").strip()
        if not eid:
            raise ValueError("Selecciona una empresa para conectar su Google Calendar")
        return eid
    eid = str((perfil or {}).get("empresa_id") or "").strip()
    if not eid:
        raise ValueError("Tu usuario no tiene una empresa asociada")
    return eid


@app.route("/portal/integraciones/google-calendar", methods=["GET", "OPTIONS"])
def portal_google_calendar_estado():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    try:
        empresa_id = _portal_empresa_para_integracion(perfil, request.args.get("empresa_id"))
        conn = google_calendar_conexion(empresa_id)
        if not conn:
            return portal_json({"ok": True, "conectado": False, "empresa_id": empresa_id})
        return portal_json({
            "ok": True,
            "conectado": True,
            "empresa_id": empresa_id,
            "google_email": conn.get("google_email"),
            "calendar_id": conn.get("calendar_id") or "primary",
            "calendar_nombre": conn.get("calendar_nombre") or "Calendario principal",
            "updated_at": conn.get("updated_at"),
        })
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 400)


@app.route("/portal/integraciones/google-calendar/iniciar", methods=["POST", "OPTIONS"])
def portal_google_calendar_iniciar():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return portal_json({"ok": False, "error": "Google OAuth no está configurado en Render"}, 500)
    try:
        body = request.get_json(silent=True) or {}
        empresa_id = _portal_empresa_para_integracion(perfil, body.get("empresa_id"))
        # Si es una prueba, el token viaja dentro del state firmado y vuelve al callback.
        # Así Google OAuth no obliga al usuario a iniciar sesión con contraseña.
        demo_token_oauth = str(perfil.get("demo_token") or "").strip() if perfil.get("demo") else ""
        state = _google_state_encode(empresa_id, demo_token_oauth)
        params = {
            "client_id": GOOGLE_CLIENT_ID,
            "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(GOOGLE_SCOPES + ["openid", "email"]),
            "access_type": "offline",
            "prompt": "consent",
            # No reutilizar permisos históricos concedidos a este mismo OAuth Client.
            # El flujo multiempresa de Nexia solicita únicamente Calendar + identidad
            # básica (openid/email). Esto evita arrastrar scopes antiguos como Sheets.
            "include_granted_scopes": "false",
            "state": state,
        }
        return portal_json({"ok": True, "url": GOOGLE_OAUTH_AUTH_URL + "?" + urlencode(params)})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 400)


@app.route("/oauth/google/calendar/callback", methods=["GET"])
def oauth_google_calendar_callback():
    code = str(request.args.get("code") or "").strip()
    state = _google_state_decode(request.args.get("state"))
    if not code or not state or not state.get("empresa_id"):
        return redirect(f"{PORTAL_ORIGIN}/portal.html?calendar=error")
    empresa_id = str(state["empresa_id"])
    demo_token_oauth = str(state.get("demo_token") or "").strip()
    try:
        tr = requests.post(
            GOOGLE_OAUTH_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_CALENDAR_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=20,
        )
        tr.raise_for_status()
        tok = tr.json()
        refresh_token = str(tok.get("refresh_token") or "").strip()
        access_token = str(tok.get("access_token") or "").strip()
        if not refresh_token:
            previous = google_calendar_conexion(empresa_id)
            refresh_token = str((previous or {}).get("refresh_token") or "").strip()
        if not refresh_token:
            raise RuntimeError("Google no entregó refresh_token. Revoca el acceso anterior y vuelve a conectar.")

        creds = Credentials(
            token=access_token or None,
            refresh_token=refresh_token,
            token_uri=GOOGLE_OAUTH_TOKEN_URL,
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=GOOGLE_SCOPES,
        )
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        cal = service.calendars().get(calendarId="primary").execute()
        calendar_id = str(cal.get("id") or "primary")
        calendar_nombre = str(cal.get("summary") or "Calendario principal")

        google_email = ""
        try:
            if access_token:
                ui = requests.get(
                    "https://openidconnect.googleapis.com/v1/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"}, timeout=15,
                )
                if ui.ok:
                    google_email = str((ui.json() or {}).get("email") or "")
        except Exception:
            pass

        expires_in = int(tok.get("expires_in") or 3600)
        token_expiry = (datetime.now(pytz.UTC) + timedelta(seconds=expires_in)).isoformat()
        payload = {
            "empresa_id": empresa_id,
            "google_email": google_email or None,
            "calendar_id": calendar_id,
            "calendar_nombre": calendar_nombre,
            "refresh_token": refresh_token,
            "access_token": access_token or None,
            "token_expiry": token_expiry,
            "scopes": GOOGLE_SCOPES,
            "activo": True,
            "updated_at": datetime.now(pytz.UTC).isoformat(),
        }
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/google_calendar_conexiones",
            headers={**backend_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "empresa_id"},
            json=payload,
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        with TENANT_CACHE_LOCK:
            TENANT_CACHE.pop(f"empresa:{empresa_id}", None)
        qs = "calendar=connected"
        if demo_token_oauth:
            qs += "&demo_token=" + quote(demo_token_oauth, safe="")
        return redirect(f"{PORTAL_ORIGIN}/portal.html?{qs}")
    except Exception as e:
        print("GOOGLE CALENDAR OAUTH CALLBACK ERROR:", repr(e))
        qs = "calendar=error"
        if demo_token_oauth:
            qs += "&demo_token=" + quote(demo_token_oauth, safe="")
        return redirect(f"{PORTAL_ORIGIN}/portal.html?{qs}")


@app.route("/portal/integraciones/google-calendar/desconectar", methods=["POST", "OPTIONS"])
def portal_google_calendar_desconectar():
    if request.method == "OPTIONS":
        return portal_json({"ok": True}, 204)
    perfil = portal_usuario_autorizado()
    if not perfil:
        return portal_json({"ok": False, "error": "Sesión no autorizada"}, 401)
    try:
        body = request.get_json(silent=True) or {}
        empresa_id = _portal_empresa_para_integracion(perfil, body.get("empresa_id"))
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/google_calendar_conexiones",
            headers={**backend_headers(), "Prefer": "return=minimal"},
            params={"empresa_id": f"eq.{empresa_id}"},
            json={"activo": False, "updated_at": datetime.now(pytz.UTC).isoformat()},
            timeout=SUPABASE_TIMEOUT,
        )
        r.raise_for_status()
        return portal_json({"ok": True})
    except Exception as e:
        return portal_json({"ok": False, "error": str(e)[:300]}, 400)


if __name__ == "__main__":
    print("APP_VERSION:", APP_VERSION)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
