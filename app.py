import os
import re
import html
import json
import hmac
import hashlib
from datetime import datetime, timedelta
from threading import Lock
from contextvars import ContextVar

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from werkzeug.middleware.proxy_fix import ProxyFix


APP_VERSION = "2026-09-08-V65-NEXIA-SIN-DIRECCION-TELEFONO-HORARIO"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

DEFAULT_ASISTENTE_NOMBRE = os.getenv("ESTILISTA_NOMBRE", "Diego")
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
NEXIA_EMPRESA_ID = os.getenv("NEXIA_EMPRESA_ID", "1675736f-e605-405a-b7bb-eed29e013dd1").strip()
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
INSTAGRAM_GRAPH_VERSION = os.getenv("INSTAGRAM_GRAPH_VERSION", "v26.0")
INSTAGRAM_API_BASE = os.getenv("INSTAGRAM_API_BASE", "https://graph.instagram.com").rstrip("/")

# Supabase: historial para Portal Nexia.
# IMPORTANTE: SUPABASE_SERVICE_ROLE_KEY va SOLO en Render > Environment.
# Nunca debe ponerse en portal.html ni exponerse en el navegador.
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://nappdpkjtdzwtiuvrrhk.supabase.co").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
DEFAULT_EMPRESA_ID = os.getenv("SUPABASE_EMPRESA_ID", "97be347a-51d6-467d-be49-839a254a4ad0")
NEXIA_EMPRESA_ID = os.getenv("NEXIA_EMPRESA_ID", "0a9de921-9386-441c-b5aa-f432d3f44fe5")
SUPABASE_TIMEOUT = int(os.getenv("SUPABASE_TIMEOUT", "15"))

PORTAL_ORIGIN = os.getenv("PORTAL_ORIGIN", "https://nexia-tech.com").rstrip("/")

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
        base=tenant_default();base.update({"empresa_id":empresa_id,"empresa_nombre":empresas[0].get("nombre") or DEFAULT_NEGOCIO_NOMBRE,"tipo_negocio":conf.get("tipo_negocio") or "reservas","descripcion_empresa":conf.get("descripcion_empresa") or "","asistente_nombre":conf.get("asistente_nombre") or DEFAULT_ASISTENTE_NOMBRE,"direccion":conf.get("direccion") or DEFAULT_DIRECCION_ATENCION,"telefono_ejecutivo":conf.get("telefono_ejecutivo") or DEFAULT_TELEFONO_EJECUTIVO,"correo_ejecutivo":conf.get("correo_ejecutivo") or EJECUTIVO_EMAIL,"timezone":conf.get("timezone") or TIMEZONE,"calendar_id":conf.get("calendar_id") or DEFAULT_CALENDAR_ID,"hora_apertura":conf.get("hora_apertura") if conf.get("hora_apertura") is not None else DEFAULT_HORA_APERTURA,"hora_cierre":conf.get("hora_cierre") if conf.get("hora_cierre") is not None else DEFAULT_HORA_CIERRE,"duracion_reserva":conf.get("duracion_reserva") if conf.get("duracion_reserva") is not None else DEFAULT_DURACION_RESERVA,"dias_atencion":conf.get("dias_atencion") or [0,1,2,3,4,5],"prompt_extra":conf.get("prompt_extra") or "","modulos":conf.get("modulos") or tenant_default()["modulos"],"servicios":servicios or None});_cache_set(key,base)
    if base is None:base=tenant_default();base["empresa_id"]=empresa_id
    out=dict(base);out["canal"]=canal;out["provider"]=provider;out["canal_config"]=dict(canal_config or {});return out

def resolver_tenant(canal,provider,identificador_externo):
    canal=str(canal or "").lower().strip();provider=str(provider or "").lower().strip();externo=str(identificador_externo or "").strip()
    if canal=="whatsapp":externo=re.sub(r"\D","",externo)
    headers=backend_headers()
    if not headers:
        out=tenant_default();out["canal"]=canal;out["provider"]=provider;return out
    key=f"route:{canal}:{provider}:{externo}";row=_cache_get(key)
    if row is None:
        r=requests.get(f"{SUPABASE_URL}/rest/v1/canales_empresa",headers=headers,params={"select":"*","canal":f"eq.{canal}","provider":f"eq.{provider}","identificador_externo":f"eq.{externo}","activo":"eq.true","limit":"1"},timeout=SUPABASE_TIMEOUT);r.raise_for_status();rows=r.json() if r.content else []
        if not rows:
            out=tenant_default();out["canal"]=canal;out["provider"]=provider;return out
        row=rows[0];_cache_set(key,row)
    return cargar_empresa_config(row["empresa_id"],canal=canal,provider=provider,canal_config=row)

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
    """Protección de información pública específica para Nexia Tech."""
    return str(empresa_actual_id() or "").strip() == NEXIA_EMPRESA_ID


def mensaje_horario_no_publicado():
    return (
        "La atención de Nexia se gestiona directamente por este chat 😊. "
        "Déjame tu consulta y te ayudo por aquí."
    )


def proteger_respuesta_publica_nexia(texto):
    """
    Evita que Nexia exponga por WhatsApp/Instagram dirección, teléfono o horario
    aunque algún dato antiguo siga configurado en Supabase o variables de entorno.
    """
    if not es_empresa_nexia():
        return texto

    salida = str(texto or "")
    sensibles = [
        str(cfg("direccion", "") or "").strip(),
        str(cfg("telefono_ejecutivo", "") or "").strip(),
        str(DEFAULT_DIRECCION_ATENCION or "").strip(),
        str(DEFAULT_TELEFONO_EJECUTIVO or "").strip(),
    ]
    for valor in sensibles:
        if valor:
            salida = salida.replace(valor, "")

    # Limpieza de líneas que pudieran haber quedado etiquetadas tras una respuesta IA.
    lineas = []
    for linea in salida.splitlines():
        t = normalizar_texto(linea)
        if any(k in t for k in ("direccion:", "direccion configurada:", "telefono:", "telefono de ejecutivo:", "horario configurado:")):
            continue
        lineas.append(linea)
    return "\n".join(lineas).strip()


def normalizar_telefono(valor):
    valor = (valor or "").strip()
    return valor[len("whatsapp:"):] if valor.startswith("whatsapp:") else valor


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
    "https://www.googleapis.com/auth/spreadsheets",
]


def google_credentials():
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        raise RuntimeError("Faltan credenciales de Google Calendar en Render")
    return Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=GOOGLE_SCOPES,
    )


def calendar_service():
    return build("calendar", "v3", credentials=google_credentials(), cache_discovery=False)


def es_dia_atencion(fecha):
    return fecha.astimezone(zona_local()).weekday() in set(cfg("dias_atencion", [0,1,2,3,4,5]))


def eventos_ocupados(inicio_rango, fin_rango):
    service = calendar_service()
    data = service.events().list(
        calendarId=cfg("calendar_id", DEFAULT_CALENDAR_ID),
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
                "origen": "whatsapp_asistente_diego",
            }
        },
    }
    try:
        resultado = calendar_service().events().insert(
            calendarId=cfg("calendar_id", DEFAULT_CALENDAR_ID),
            body=body,
            sendUpdates="all",
        ).execute()
        return {"ok": True, "evento_id": resultado.get("id")}
    except Exception as e:
        print("GOOGLE CALENDAR CREAR EVENTO ERROR:", repr(e))
        return {"ok": False, "error": repr(e)}


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
# GOOGLE SHEETS OPCIONAL - LOG DE CONVERSACIONES
# ============================================================

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Conversaciones")
SHEETS_LOCK = Lock()
SHEETS_READY = False


def guardar_mensaje(telefono, rol, mensaje, canal="whatsapp"):
    global SHEETS_READY
    if not GOOGLE_SHEET_ID:
        return
    try:
        svc = build("sheets", "v4", credentials=google_credentials(), cache_discovery=False)
        with SHEETS_LOCK:
            if not SHEETS_READY:
                meta = svc.spreadsheets().get(
                    spreadsheetId=GOOGLE_SHEET_ID, fields="sheets.properties.title"
                ).execute()
                titulos = [x.get("properties", {}).get("title") for x in meta.get("sheets", [])]
                if GOOGLE_SHEET_TAB not in titulos:
                    svc.spreadsheets().batchUpdate(
                        spreadsheetId=GOOGLE_SHEET_ID,
                        body={"requests": [{"addSheet": {"properties": {"title": GOOGLE_SHEET_TAB}}}]},
                    ).execute()
                    svc.spreadsheets().values().update(
                        spreadsheetId=GOOGLE_SHEET_ID,
                        range=f"'{GOOGLE_SHEET_TAB}'!A1:F1",
                        valueInputOption="RAW",
                        body={"values": [["FechaHora", "Canal", "ClienteID", "Telefono", "Rol", "Mensaje"]]},
                    ).execute()
                SHEETS_READY = True
            svc.spreadsheets().values().append(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"'{GOOGLE_SHEET_TAB}'!A:F",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[ahora_local().isoformat(), canal, telefono, normalizar_telefono(telefono), rol, mensaje]]},
            ).execute()
    except Exception as e:
        print("GOOGLE SHEETS LOG ERROR:", repr(e))


# ============================================================
# OPENAI OPCIONAL: RESPUESTA LIBRE, PERO SOLO DENTRO DEL NEGOCIO
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


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


def intencion_agendar(texto):
    if not negocio_usa_reservas():
        return False
    t = normalizar_texto(texto)
    return any(x in t for x in (
        "agendar", "reservar", "reserva", "hora", "cita", "turno",
        "disponibilidad", "quiero cortarme", "quiero un corte", "sacar hora",
    ))


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

    portal_url = f"{PORTAL_ORIGIN}/portal.html"

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
          Abrir Portal Nexia
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


def procesar_agenda(estado, texto):
    t = normalizar_texto(texto)

    if es_cancelar(texto):
        telefono = estado["telefono"]
        reset_estado(telefono)
        return "No hay problema 😊. Cuando quieras agendar una hora con Diego, escríbeme nuevamente."

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
            f"📧 {estado['correo']}\n"
            f"📍 {cfg('direccion', DEFAULT_DIRECCION_ATENCION)}\n\n"
            "Si todo está correcto, escribe *CONFIRMAR*."
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

        reset_estado(telefono)
        return (
            "✅ *¡Reserva confirmada!*\n\n"
            f"✂️ Servicio: {servicio['nombre']}\n"
            f"💰 Valor: {servicio['precio_texto']}\n"
            f"📅 {fecha_txt}\n"
            f"👤 {nombre}\n"
            f"📧 {correo}\n"
            f"📍 {cfg('direccion', DEFAULT_DIRECCION_ATENCION)}\n"
            f"⏱️ Duración: {cfg_int('duracion_reserva', DEFAULT_DURACION_RESERVA)} minutos\n\n"
            "No necesitas realizar ningún pago para agendar. ¡Te esperamos! 😊"
        )

    estado["paso"] = "inicio"
    return mensaje_bienvenida()


# ============================================================
# TWILIO WHATSAPP WEBHOOK
# ============================================================

@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    twiml = MessagingResponse()
    try:
        to_numero = re.sub(r"\D", "", str(request.form.get("To") or TWILIO_WHATSAPP_FROM))
        activar_por_canal("whatsapp", "twilio", to_numero)
        telefono = (request.form.get("From") or "").strip()
        texto = (request.form.get("Body") or "").strip()
        message_id = (request.form.get("MessageSid") or "").strip()

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono)
        print("Body:", texto)
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

        if not texto:
            respuesta = mensaje_bienvenida()
        else:
            modo_actual = obtener_modo_atencion(telefono, "whatsapp")
            guardar_mensaje(telefono, "user", texto)
            guardar_mensaje_supabase(telefono, "entrante", texto)

            if modo_actual == "ejecutivo":
                print("WHATSAPP MODO EJECUTIVO: solo esta conversación queda con ejecutivo")
                return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

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
                    f"Sí 😊 Ese servicio está disponible. "
                    f"Si te interesa contratarlo, escribe *ME INTERESA* y te hago unas preguntas breves."
                )
            else:
                # Cualquier otra cosa recibe una respuesta natural pero acotada al negocio actual.
                respuesta = respuesta_general(texto)

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

        # Por ahora el bot conversa por texto. Si llega imagen/audio/documento,
        # respondemos indicando que escriba el mensaje para mantener el flujo estable.
        if tipo != "text":
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


def procesar_texto_instagram(cliente_id, texto, username=None):
    """
    Reutiliza la misma lógica conversacional del bot de Diego.
    La sesión se separa de WhatsApp mediante el prefijo 'instagram:'.

    Si Meta entrega username, se guarda como nombre_contacto para que
    Portal Nexia muestre @usuario en lugar del IGSID numérico.
    """
    session_id = f"instagram:{cliente_id}"
    username = str(username or "").strip().lstrip("@")
    nombre_instagram = f"@{username}" if username else None

    guardar_mensaje(session_id, "user", texto, canal="instagram")
    guardar_mensaje_supabase(
        cliente_id,
        "entrante",
        texto,
        nombre_contacto=nombre_instagram,
        canal="instagram",
    )

    estado = get_estado(session_id)
    debe_derivar = False

    if quiere_hablar_con_persona(texto):
        respuesta = mensaje_derivacion_ejecutivo()
        debe_derivar = True
    elif es_menu(texto):
        reset_estado(session_id)
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

    guardar_mensaje(session_id, "assistant", respuesta, canal="instagram")
    guardar_mensaje_supabase(
        cliente_id,
        "saliente",
        respuesta,
        nombre_contacto=nombre_instagram or estado.get("nombre"),
        canal="instagram",
    )
    if debe_derivar:
        derivar_a_ejecutivo(
            cliente_id,
            "instagram",
            estado=estado,
            motivo=texto,
        )
    return respuesta


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
        "instagram_graph_version": INSTAGRAM_GRAPH_VERSION,
        "webhook": "/instagram/webhook",
    }, 200


# ============================================================
# HEALTHCHECK
# ============================================================


def portal_cors_response(response):
    response.headers["Access-Control-Allow-Origin"] = PORTAL_ORIGIN
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    response.headers["Vary"] = "Origin"
    return response


def portal_json(payload, status=200):
    from flask import jsonify
    response = jsonify(payload)
    return portal_cors_response(response), status


def portal_usuario_autorizado():
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
    if not filas:
        print("PORTAL AUTH: usuario válido pero sin perfil:", user_id, email_auth)
        return None

    perfil = filas[0]

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
    return str((perfil or {}).get("rol") or "").strip().lower() == "superadmin"


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


@app.route("/")
def health():
    return {
        "ok": True,
        "app": "Nexia Core - Multiempresa",
        "version": APP_VERSION,
        "channel": "Twilio WhatsApp + Gupshup WhatsApp + Instagram Meta API",
        "calendar": "Google Calendar",
        "payments": "disabled",
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
      pertenecen a la empresa administrativa definida en NEXIA_EMPRESA_ID.
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
    nexia_id = str(NEXIA_EMPRESA_ID or "").strip()

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


if __name__ == "__main__":
    print("APP_VERSION:", APP_VERSION)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
