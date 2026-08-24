import os
import re
import hashlib
import requests
import pytz
from openai import OpenAI

from flask import (
    Flask,
    redirect,
    url_for,
    session,
    request,
    render_template_string,
)

from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient

from datetime import timedelta, datetime
from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from werkzeug.middleware.proxy_fix import ProxyFix

APP_VERSION = "2026-08-24-FINAL-DIEGO-V27-SERVICIO-HORA-DIRECTA"


# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    raise Exception("Falta SECRET_KEY en las variables de entorno.")

app.permanent_session_lifetime = timedelta(days=30)

app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_port=1,
)


# ============================================================
# OPENAI
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

if not OPENAI_API_KEY:
    print("ADVERTENCIA: falta OPENAI_API_KEY.")

client = None

if OPENAI_API_KEY:
    client = OpenAI(
        api_key=OPENAI_API_KEY
    )


# ============================================================
# MODO SIN BASE DE DATOS
# ============================================================

# PostgreSQL deshabilitado temporalmente para esta prueba.
# El flujo de WhatsApp mantiene el estado en memoria y Google Calendar
# sigue siendo la fuente de verdad para disponibilidad y reservas.

DATABASE_URL = None

def db_connect():
    return None

def init_database():
    print("MODO SIN POSTGRESQL: base de datos deshabilitada.")

def obtener_conversacion(cliente_id, canal="web"):
    return None

def crear_conversacion(cliente_id, canal="web"):
    return None

def asegurar_conversacion(cliente_id, canal="web"):
    return None

def guardar_mensaje(cliente_id, canal, role, contenido):
    return None

def actualizar_conversacion_datos(
    cliente_id, canal, nombre=None, telefono=None, correo=None,
    servicio=None, fecha_reserva=None, meet_url=None, estado=None
):
    return None

def guardar_reserva_db(
    cliente_id, canal, datos, inicio, fin, evento_id, meet_url
):
    return None


# ============================================================
# CONFIGURACIÓN DEL NEGOCIO
# ============================================================

ESTILISTA_NOMBRE = os.getenv(
    "ESTILISTA_NOMBRE",
    "Diego"
)

NEGOCIO_NOMBRE = os.getenv(
    "NEGOCIO_NOMBRE",
    "Estilista Diego"
)

TIMEZONE = os.getenv(
    "TIMEZONE",
    "America/Santiago"
)

CALENDAR_ID = os.getenv(
    "GOOGLE_CALENDAR_ID",
    "primary"
)


# ============================================================
# HORARIO
# ============================================================

HORA_APERTURA = 10
HORA_CIERRE = 18

DURACION_RESERVA = 60

DIAS_ATENCION = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
}

HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_CIERRE
    )
)


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {

    "corte_hombre": {
        "numero": 1,
        "nombre": "Corte de cabello hombre",
        "duracion": 60,
        "precio": 17000,
        "precio_texto": "$17.000",
        "detalle": "Incluye perfilado de cejas, lavado de cabello y aplicación de producto.",
    },

    "perfilado_barba": {
        "numero": 2,
        "nombre": "Perfilado de barba",
        "duracion": 60,
        "precio": 10000,
        "precio_texto": "$10.000",
    },

    "base_rizos": {
        "numero": 3,
        "nombre": "Base de rizos permanente",
        "duracion": 60,
        "precio": 65000,
        "precio_texto": "$65.000",
    },

    "mechas_hombre": {
        "numero": 4,
        "nombre": "Mechas",
        "duracion": 60,
        "precio": 70000,
        "precio_texto": "desde $70.000",
    },

    "decoloracion_global": {
        "numero": 5,
        "nombre": "Decoloración global",
        "duracion": 60,
        "precio": 120000,
        "precio_texto": "$120.000",
    },

    "corte_mujer": {
        "numero": 6,
        "nombre": "Corte de cabello mujer",
        "duracion": 60,
        "precio": 30000,
        "precio_texto": "$30.000",
        "detalle": "Incluye lavado de cabello, hidratación y brushing.",
    },

    "masaje_hidratacion": {
        "numero": 7,
        "nombre": "Masaje de hidratación",
        "duracion": 60,
        "precio": 45000,
        "precio_texto": "$45.000",
    },

    "botox_capilar": {
        "numero": 8,
        "nombre": "Botox capilar",
        "duracion": 60,
        "precio": 65000,
        "precio_texto": "desde $65.000",
    },

    "alisado_permanente": {
        "numero": 9,
        "nombre": "Alisado permanente",
        "duracion": 60,
        "precio": 70000,
        "precio_texto": "desde $70.000",
    },

    "retoque_raiz": {
        "numero": 10,
        "nombre": "Retoque de color de raíz",
        "duracion": 60,
        "precio": 50000,
        "precio_texto": "$50.000",
    },

    "bano_color": {
        "numero": 11,
        "nombre": "Baño de color",
        "duracion": 60,
        "precio": 30000,
        "precio_texto": "$30.000",
    },

    "diagnostico_balayage": {
        "numero": 12,
        "nombre": "Diagnóstico capilar gratuito para Balayage",
        "duracion": 60,
        "precio": 0,
        "precio_texto": "Diagnóstico gratuito · Balayage estimado desde $150.000",
        "detalle": "El valor final del Balayage se define después del diagnóstico capilar.",
    },
}

SERVICIO_POR_NUMERO = {
    servicio["numero"]: codigo
    for codigo, servicio in SERVICIOS.items()
}


# ============================================================
# GOOGLE
# ============================================================

GOOGLE_CLIENT_ID = os.getenv(
    "GOOGLE_CLIENT_ID"
)

GOOGLE_CLIENT_SECRET = os.getenv(
    "GOOGLE_CLIENT_SECRET"
)

GOOGLE_REFRESH_TOKEN = os.getenv(
    "GOOGLE_REFRESH_TOKEN"
)

GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://chatbot-laortiga-hddw.onrender.com/callback"
)

SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]


# ============================================================
# GOOGLE FLOW
# ============================================================

def crear_google_flow():

    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri":
                    "https://accounts.google.com/o/oauth2/auth",
                "token_uri":
                    "https://oauth2.googleapis.com/token",
                "redirect_uris": [
                    GOOGLE_REDIRECT_URI
                ],
            }
        },
        scopes=SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )


def obtener_credentials_diego():

    if not GOOGLE_REFRESH_TOKEN:
        raise Exception(
            "Falta GOOGLE_REFRESH_TOKEN."
        )

    return Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=SCOPES,
    )


def obtener_calendar_service():

    credentials = obtener_credentials_diego()

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


# ============================================================
# FECHA / HORA
# ============================================================

def obtener_zona():
    return pytz.timezone(TIMEZONE)


def ahora_local():

    return datetime.now(
        obtener_zona()
    )


def normalizar_texto(texto):

    texto = (
        texto or ""
    ).strip().lower()

    reemplazos = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
        "ñ": "n",
    }

    for a, b in reemplazos.items():
        texto = texto.replace(a, b)

    return texto


DIAS_NOMBRES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


MESES_NOMBRES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]

MESES_MAP = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}


def es_comando_menu(texto):
    texto_n = normalizar_texto(texto)
    comandos = {
        "menu",
        "menu principal",
        "volver al menu",
        "volver al menu principal",
        "inicio",
        "volver",
    }
    return texto_n in comandos


def detectar_mes_solicitado(texto):
    """
    Detecta un mes indicado sin un día específico.
    Ejemplos:
    - "en septiembre"
    - "quiero hora para octubre"

    Devuelve el primer día del mes solicitado, en el año correcto.
    Si el mes de este año ya pasó, usa el año siguiente.
    Si el mensaje contiene un día explícito, devuelve None para que
    detectar_fecha_solicitada() procese la fecha exacta.
    """
    texto_n = normalizar_texto(texto)

    patron_dia_mes = (
        r"\b(?:el\s+)?([0-3]?\d)\s*(?:de\s+)?"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|setiembre|octubre|noviembre|diciembre)\b"
    )
    if re.search(patron_dia_mes, texto_n):
        return None

    ahora = ahora_local()

    for nombre_mes, numero_mes in MESES_MAP.items():
        if re.search(rf"\b{re.escape(nombre_mes)}\b", texto_n):
            anio = ahora.year
            if numero_mes < ahora.month:
                anio += 1

            return obtener_zona().localize(
                datetime(
                    anio,
                    numero_mes,
                    1,
                    0,
                    0,
                    0
                )
            )

    return None


def texto_menciona_fecha_o_mes(texto):
    texto_n = normalizar_texto(texto)

    palabras = [
        "hoy",
        "manana",
        "manan",
        "pasado manana",
        "lunes",
        "martes",
        "miercoles",
        "jueves",
        "viernes",
        "sabado",
        "domingo",
    ] + list(MESES_MAP.keys())

    return any(
        re.search(rf"\b{re.escape(p)}\b", texto_n)
        for p in palabras
    )


def es_dia_atencion(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return fecha.weekday() in DIAS_ATENCION


def formato_fecha_corta(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"{fecha.strftime('%H:%M')}"
    )


def formato_fecha_larga(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    meses = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ]

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day} de "
        f"{meses[fecha.month - 1]} "
        f"a las {fecha.strftime('%H:%M')}"
    )


# ============================================================
# SERVICIOS
# ============================================================

def obtener_servicio(codigo):

    return SERVICIOS.get(
        codigo,
        {
            "nombre": "Servicio",
            "duracion": 60,
            "precio": 0,
            "precio_texto": "Valor a confirmar",
        }
    )


def precio_texto_servicio(servicio):

    if servicio.get("precio_texto"):
        return servicio["precio_texto"]

    precio = servicio.get("precio", 0)

    return (
        f"${precio:,}"
        .replace(",", ".")
    )


def mensaje_menu_principal():

    return (
        f"¡Hola! 👋 Soy el asistente virtual de {ESTILISTA_NOMBRE}.\n\n"
        "Puedo ayudarte con nuestros servicios, precios y con tu reserva 📅.\n\n"
        "¿En qué te puedo ayudar? 😊"
    )


def mostrar_servicios():

    return (
        "Estos son nuestros servicios y precios 👇\n\n"
        "👨 HOMBRE\n"
        "1. Corte de cabello hombre — $17.000\n"
        "   Incluye perfilado de cejas, lavado de cabello y aplicación de producto.\n\n"
        "2. Perfilado de barba — $10.000\n"
        "3. Base de rizos permanente — $65.000\n"
        "4. Mechas — desde $70.000\n"
        "5. Decoloración global — $120.000\n\n"
        "👩 MUJER\n"
        "6. Corte de cabello mujer — $30.000\n"
        "   Incluye lavado de cabello, hidratación y brushing.\n\n"
        "7. Masaje de hidratación — $45.000\n"
        "8. Botox capilar — desde $65.000\n"
        "9. Alisado permanente — desde $70.000\n"
        "10. Retoque de color de raíz — $50.000\n"
        "11. Baño de color — $30.000\n"
        "12. Balayage — valor estimado desde $150.000\n"
        "    Requiere agendar un diagnóstico capilar gratuito para definir el valor final.\n\n"
        "¿Cuál te interesa? Puedes escribir el número o el nombre del servicio.\n"
        "Después puedes decirme el día que quieres venir, por ejemplo: \"próximo miércoles\".\n"
        "Si prefieres salir, escribe SALIR o MENÚ en cualquier momento."
    )


def detectar_servicio_por_numero(texto):

    match = re.fullmatch(
        r"\s*(\d{1,2})\s*",
        texto or ""
    )

    if not match:
        return None

    numero = int(match.group(1))

    return SERVICIO_POR_NUMERO.get(numero)


def detectar_servicio(texto):

    texto_n = normalizar_texto(texto)

    servicio_numero = detectar_servicio_por_numero(texto)

    if servicio_numero:
        return servicio_numero

    # Servicios con nombres suficientemente específicos.
    if "corte" in texto_n and (
        "mujer" in texto_n
        or "dama" in texto_n
        or "femenino" in texto_n
    ):
        return "corte_mujer"

    if "corte" in texto_n and (
        "hombre" in texto_n
        or "varon" in texto_n
        or "masculino" in texto_n
    ):
        return "corte_hombre"

    if "perfilado" in texto_n and "barba" in texto_n:
        return "perfilado_barba"

    if "barba" in texto_n:
        return "perfilado_barba"

    if "rizo" in texto_n or "permanente de rizo" in texto_n:
        return "base_rizos"

    if "mecha" in texto_n:
        return "mechas_hombre"

    if "decoloracion" in texto_n:
        return "decoloracion_global"

    if "masaje" in texto_n and "hidrat" in texto_n:
        return "masaje_hidratacion"

    if "botox" in texto_n:
        return "botox_capilar"

    if "alisado" in texto_n:
        return "alisado_permanente"

    if "retoque" in texto_n and "raiz" in texto_n:
        return "retoque_raiz"

    if "bano de color" in texto_n or ("bano" in texto_n and "color" in texto_n):
        return "bano_color"

    if "balayage" in texto_n:
        return "diagnostico_balayage"

    # "corte" a secas es ambiguo: no asumimos hombre o mujer.
    return None


# ============================================================
# DETECTAR FECHA / HORA SOLICITADA EN TEXTO LIBRE
# ============================================================

def detectar_hora_solicitada(texto):
    """
    Interpreta expresiones como:
    - a las 3
    - a las 3:30
    - 15:00
    - 3 pm

    Como el negocio atiende entre 10:00 y 18:00,
    una hora simple como "3" se interpreta como 15:00.
    """

    texto_n = normalizar_texto(texto)

    # 15:00 / 15.30 / a las 15:00
    match = re.search(
        r"(?:a\s+las?\s+)?\b([01]?\d|2[0-3])[:.]([0-5]\d)\b",
        texto_n
    )

    if match:
        hora = int(match.group(1))
        minuto = int(match.group(2))
        return hora, minuto

    # 3 pm / 3:30 pm
    match = re.search(
        r"(?:a\s+las?\s+)?\b(1[0-2]|[1-9])"
        r"(?:[:.]([0-5]\d))?\s*(am|pm)\b",
        texto_n
    )

    if match:
        hora = int(match.group(1))
        minuto = int(match.group(2) or 0)
        periodo = match.group(3)

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

        return hora, minuto

    # "a las 3" / "a la 1"
    match = re.search(
        r"\ba\s+las?\s+(\d{1,2})\b",
        texto_n
    )

    if match:
        hora = int(match.group(1))

        # Dentro del horario del negocio, 1..6 normalmente
        # significa 13:00..18:00.
        if 1 <= hora <= 6:
            hora += 12

        return hora, 0

    return None



def detectar_rango_horario(texto):
    """
    Detecta rangos horarios conversacionales.
    Ejemplos:
    - entre 10 y 12
    - entre las 10 y las 12
    - de 14 a 17
    - entre 3 y 5 pm
    - en la mañana / por la mañana
    - en la tarde / por la tarde

    Devuelve (hora_inicio, hora_fin), ambas inclusivas para los
    horarios de inicio que se mostrarán.
    """
    texto_n = normalizar_texto(texto)

    # Tramos naturales dentro del horario del negocio.
    if (
        "en la manana" in texto_n
        or "por la manana" in texto_n
        or "durante la manana" in texto_n
    ):
        return 10, 12

    if (
        "en la tarde" in texto_n
        or "por la tarde" in texto_n
        or "durante la tarde" in texto_n
    ):
        return 13, 17

    # "entre las 10 y las 12", "de 10 a 12", etc.
    patron = (
        r"\b(?:entre(?:\s+las?)?|de(?:\s+las?)?)\s*"
        r"(\d{1,2})(?::([0-5]\d))?\s*"
        r"(am|pm)?\s*"
        r"(?:y|a|hasta)\s*(?:las?\s*)?"
        r"(\d{1,2})(?::([0-5]\d))?\s*"
        r"(am|pm)?\b"
    )

    match = re.search(patron, texto_n)

    if not match:
        return None

    h1 = int(match.group(1))
    p1 = match.group(3)
    h2 = int(match.group(4))
    p2 = match.group(6)

    def normalizar_hora(h, periodo):
        if periodo == "pm" and h < 12:
            h += 12
        elif periodo == "am" and h == 12:
            h = 0
        return h

    # Si solo el segundo extremo trae am/pm, aplicarlo al primero cuando
    # sea razonable: "entre 3 y 5 pm" -> 15 a 17.
    if not p1 and p2:
        p1 = p2

    h1 = normalizar_hora(h1, p1)
    h2 = normalizar_hora(h2, p2)

    # Dentro del horario del negocio, "3 a 5" se entiende 15 a 17.
    if not p1 and not p2:
        if 1 <= h1 <= 6:
            h1 += 12
        if 1 <= h2 <= 6:
            h2 += 12

    if h1 > h2:
        h1, h2 = h2, h1

    h1 = max(h1, HORA_APERTURA)
    h2 = min(h2, HORA_CIERRE - 1)

    if h1 > h2:
        return None

    return h1, h2


def filtrar_horas_por_rango(horas, rango):
    if not rango:
        return horas

    hora_inicio, hora_fin = rango

    return [
        h for h in horas
        if hora_inicio <= h.hour <= hora_fin
    ]


def detectar_fecha_solicitada(texto, hora_data=None):
    """
    Detecta hoy, mañana, pasado mañana, días de la semana y fechas exactas
    como "2 de septiembre" o "15 enero".
    """

    texto_n = normalizar_texto(texto)
    ahora = ahora_local()
    zona = obtener_zona()

    fecha_base = ahora.replace(
        second=0,
        microsecond=0
    )

    patron_dia_mes = (
        r"\b(?:el\s+)?([0-3]?\d)\s*(?:de\s+)?"
        r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|"
        r"septiembre|setiembre|octubre|noviembre|diciembre)\b"
    )

    match_fecha = re.search(
        patron_dia_mes,
        texto_n
    )

    if match_fecha:

        dia = int(match_fecha.group(1))
        mes = MESES_MAP[match_fecha.group(2)]
        anio = ahora.year

        try:
            candidato = zona.localize(
                datetime(
                    anio,
                    mes,
                    dia,
                    0,
                    0,
                    0
                )
            )
        except ValueError:
            return None

        if candidato.date() < ahora.date():
            anio += 1

            try:
                candidato = zona.localize(
                    datetime(
                        anio,
                        mes,
                        dia,
                        0,
                        0,
                        0
                    )
                )
            except ValueError:
                return None

        return candidato

    if "pasado manana" in texto_n:
        return fecha_base + timedelta(days=2)

    if re.search(r"\bmanan(?:a)?\b", texto_n):
        return fecha_base + timedelta(days=1)

    if "hoy" in texto_n:
        return fecha_base

    dias_map = {
        "lunes": 0,
        "martes": 1,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sabado": 5,
        "domingo": 6,
    }

    for nombre_dia, weekday in dias_map.items():

        if nombre_dia in texto_n:

            diferencia = (
                weekday
                - ahora.weekday()
            ) % 7

            if diferencia == 0 and hora_data:

                hora, minuto = hora_data

                candidato_hoy = ahora.replace(
                    hour=hora,
                    minute=minuto,
                    second=0,
                    microsecond=0
                )

                if candidato_hoy <= ahora:
                    diferencia = 7

            return fecha_base + timedelta(days=diferencia)

    if hora_data:

        hora, minuto = hora_data

        for offset in range(8):

            candidato_fecha = (
                ahora
                + timedelta(days=offset)
            ).replace(
                hour=hora,
                minute=minuto,
                second=0,
                microsecond=0
            )

            if not es_dia_atencion(candidato_fecha):
                continue

            if candidato_fecha <= ahora:
                continue

            return candidato_fecha

    return None


def construir_fecha_hora_solicitada(texto):
    """
    Devuelve un datetime timezone-aware si el mensaje contiene
    una hora interpretable.
    """

    hora_data = detectar_hora_solicitada(
        texto
    )

    if not hora_data:
        return None

    fecha = detectar_fecha_solicitada(
        texto,
        hora_data
    )

    if not fecha:
        return None

    hora, minuto = hora_data

    return fecha.replace(
        hour=hora,
        minute=minuto,
        second=0,
        microsecond=0
    )


# ============================================================
# GOOGLE CALENDAR DISPONIBILIDAD
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion=60
):

    try:

        zona = obtener_zona()

        inicio = inicio.astimezone(zona)

        if not es_dia_atencion(inicio):
            return False

        if inicio.minute != 0:
            return False

        if (
            inicio.hour < HORA_APERTURA
            or inicio.hour >= HORA_CIERRE
        ):
            return False

        fin = inicio + timedelta(
            minutes=duracion
        )

        limite = inicio.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        )

        if fin > limite:
            return False

        service = obtener_calendar_service()

        resultado = (
            service
            .freebusy()
            .query(
                body={
                    "timeMin": inicio.isoformat(),
                    "timeMax": fin.isoformat(),
                    "items": [
                        {
                            "id": CALENDAR_ID
                        }
                    ],
                }
            )
            .execute()
        )

        calendario = (
            resultado
            .get("calendars", {})
            .get(CALENDAR_ID, {})
        )

        busy = calendario.get(
            "busy",
            []
        )

        return len(busy) == 0

    except Exception as e:

        print(
            "ERROR FREEBUSY:",
            repr(e)
        )

        return None


# ============================================================
# 15 PRÓXIMAS HORAS
# ============================================================

def buscar_proximas_15_horas(desde=None):

    """
    Busca las próximas 15 horas disponibles haciendo UNA sola
    consulta a Google Calendar para evitar timeouts de Twilio.

    Si recibe "desde", comienza a buscar desde esa fecha/hora.
    """

    ahora = ahora_local()
    zona = obtener_zona()

    if desde is None:
        desde = ahora
    else:
        desde = desde.astimezone(zona)

    if desde < ahora:
        desde = ahora

    # Revisamos hasta 31 días hacia adelante.
    inicio_rango = desde

    fin_rango = (
        desde
        + timedelta(days=31)
    ).replace(
        hour=23,
        minute=59,
        second=59,
        microsecond=0
    )

    try:

        service = obtener_calendar_service()

        eventos_resultado = (
            service
            .events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=inicio_rango.isoformat(),
                timeMax=fin_rango.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=500,
            )
            .execute()
        )

        eventos = eventos_resultado.get(
            "items",
            []
        )

        print(
            "CALENDAR OK - EVENTOS EN RANGO:",
            len(eventos),
            "DESDE:",
            inicio_rango.isoformat(),
            "HASTA:",
            fin_rango.isoformat(),
        )

    except Exception as e:

        print(
            "ERROR CONSULTANDO CALENDAR PARA DISPONIBILIDAD:",
            repr(e)
        )

        import traceback
        print(traceback.format_exc())

        # None permite distinguir un error técnico de "sin horas disponibles".
        return None


    # Convertimos eventos del calendario en intervalos ocupados.
    ocupados = []

    for evento in eventos:

        start_data = evento.get(
            "start",
            {}
        )

        end_data = evento.get(
            "end",
            {}
        )

        start_str = start_data.get(
            "dateTime"
        )

        end_str = end_data.get(
            "dateTime"
        )

        # Evento con hora específica.
        if start_str and end_str:

            try:

                inicio_evento = (
                    datetime
                    .fromisoformat(
                        start_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                fin_evento = (
                    datetime
                    .fromisoformat(
                        end_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                ocupados.append(
                    (
                        inicio_evento,
                        fin_evento
                    )
                )

            except Exception:

                continue

        # Evento de día completo.
        elif (
            start_data.get("date")
            and end_data.get("date")
        ):

            try:

                inicio_evento = zona.localize(
                    datetime.combine(
                        datetime.fromisoformat(
                            start_data["date"]
                        ).date(),
                        datetime.min.time()
                    )
                )

                fin_evento = zona.localize(
                    datetime.combine(
                        datetime.fromisoformat(
                            end_data["date"]
                        ).date(),
                        datetime.min.time()
                    )
                )

                ocupados.append(
                    (
                        inicio_evento,
                        fin_evento
                    )
                )

            except Exception:

                continue


    resultados = []

    # Generar horas enteras de lunes a sábado,
    # entre 10:00 y 17:00.
    for offset in range(32):

        fecha = (
            desde
            + timedelta(days=offset)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        if not es_dia_atencion(
            fecha
        ):

            continue

        for hora in HORAS_DISPONIBLES:

            inicio = fecha.replace(
                hour=hora,
                minute=0,
                second=0,
                microsecond=0
            )

            if inicio <= ahora:

                continue

            if inicio < desde:

                continue

            fin = (
                inicio
                + timedelta(
                    minutes=DURACION_RESERVA
                )
            )

            # No permitir reservas que terminen después de las 18:00.
            limite = fecha.replace(
                hour=HORA_CIERRE,
                minute=0,
                second=0,
                microsecond=0
            )

            if fin > limite:

                continue

            disponible = True

            for (
                inicio_ocupado,
                fin_ocupado
            ) in ocupados:

                if (
                    inicio < fin_ocupado
                    and fin > inicio_ocupado
                ):

                    disponible = False
                    break

            if disponible:

                resultados.append(
                    inicio
                )

                if len(resultados) >= 15:

                    print(
                        "15 HORAS DISPONIBLES:",
                        [
                            h.isoformat()
                            for h in resultados
                        ]
                    )

                    return resultados

    return resultados


def buscar_horas_disponibles_dia(fecha_obj):
    """
    Devuelve todas las horas enteras disponibles del día solicitado
    dentro del horario 10:00 a 18:00, haciendo una sola consulta
    a Google Calendar.
    """

    zona = obtener_zona()
    ahora = ahora_local()

    fecha_obj = fecha_obj.astimezone(zona)

    inicio_dia = fecha_obj.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    fin_dia = (
        inicio_dia
        + timedelta(days=1)
    )

    if not es_dia_atencion(inicio_dia):
        return []

    try:

        service = obtener_calendar_service()

        eventos_resultado = (
            service
            .events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=inicio_dia.isoformat(),
                timeMax=fin_dia.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            )
            .execute()
        )

        eventos = eventos_resultado.get(
            "items",
            []
        )

    except Exception as e:

        print(
            "ERROR CONSULTANDO DISPONIBILIDAD DEL DIA:",
            repr(e)
        )

        return None


    ocupados = []

    for evento in eventos:

        start_data = evento.get(
            "start",
            {}
        )

        end_data = evento.get(
            "end",
            {}
        )

        start_str = start_data.get(
            "dateTime"
        )

        end_str = end_data.get(
            "dateTime"
        )

        if start_str and end_str:

            try:

                inicio_evento = (
                    datetime
                    .fromisoformat(
                        start_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                fin_evento = (
                    datetime
                    .fromisoformat(
                        end_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                ocupados.append(
                    (
                        inicio_evento,
                        fin_evento
                    )
                )

            except Exception:
                continue

        elif (
            start_data.get("date")
            and end_data.get("date")
        ):

            # Evento de día completo: bloquear todo el día.
            return []


    resultados = []

    for hora in HORAS_DISPONIBLES:

        inicio = inicio_dia.replace(
            hour=hora,
            minute=0,
            second=0,
            microsecond=0
        )

        if inicio <= ahora:
            continue

        fin = inicio + timedelta(
            minutes=DURACION_RESERVA
        )

        limite = inicio_dia.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        )

        if fin > limite:
            continue

        disponible = True

        for (
            inicio_ocupado,
            fin_ocupado
        ) in ocupados:

            if (
                inicio < fin_ocupado
                and fin > inicio_ocupado
            ):

                disponible = False
                break

        if disponible:
            resultados.append(inicio)

    return resultados


def formatear_opciones_horas(horas):

    lineas = []

    for i, hora in enumerate(
        horas,
        start=1
    ):

        lineas.append(
            f"{i}. {formato_fecha_corta(hora)}"
        )

    return "\n".join(lineas)


# ============================================================
# DETECTAR INTENCIONES
# ============================================================

def pregunta_servicios(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "servicios",
        "servicio",
        "precios",
        "precio",
        "cuanto cuesta",
        "cuanto sale",
        "valor",
        "valores",
        "tarifa",
        "lista de precios",
        "que hacen",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


def es_intencion_agendar(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "agendar",
        "agenda",
        "reservar",
        "reserva",
        "reservame",
        "quiero una hora",
        "quiero agendar",
        "quiero reservar",
        "sacar hora",
        "sacar una hora",
        "pedir hora",
        "cita",
        "turno",
        "disponibilidad",
        "horas disponibles",
        "hora disponible",
        "que horas tienes",
        "que hora tienes",
        "tienes hora",
        "tienes horas",
        "hay hora",
        "hay horas",
        "disponible manana",
        "disponible hoy",
        "quiero cortarme",
        "quiero cortar",
        "cortarme el pelo",
        "cortarme el cabello",
        "cortar el pelo",
        "cortar el cabello",
        "quiero un corte",
        "quiero corte",
        "necesito un corte",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


def es_saludo_o_menu(texto):

    texto_n = normalizar_texto(texto)

    return texto_n in {
        "hola",
        "holi",
        "holaa",
        "buenas",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "menu",
        "inicio",
        "volver",
    }


def quiere_reiniciar_conversacion(texto):
    texto_n = normalizar_texto(texto)

    return texto_n in {
        "de nuevo",
        "empezar de nuevo",
        "partir de nuevo",
        "reiniciar",
        "reinicia",
        "comenzar de nuevo",
        "otra vez",
    }


def usuario_no_quiere(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "no quiero",
        "no gracias",
        "gracias no",
        "dejalo",
        "olvidalo",
        "cancelar",
        "cancela",
        "no por ahora",
        "despues",
        "no necesito",
        "salir",
        "quiero salir",
        "prefiero no reservar",
        "no quiero reservar",
        "no quiero agendar",
        "adios",
        "chao",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


def quiere_proximas_fechas(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "proximas",
        "proximas fechas",
        "proximas horas",
        "horas disponibles",
        "fechas disponibles",
        "disponibilidad",
        "que horas tienes",
        "que fechas tienes",
        "cuando tienes hora",
        "cuando hay hora",
        "lo antes posible",
        "primera disponible",
    ]

    return any(p in texto_n for p in patrones)


def quiere_cambiar_servicio(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "otro servicio",
        "otra cosa",
        "cambiar servicio",
        "cambio de servicio",
        "quiero cambiar",
        "ver servicios",
        "mostrar servicios",
        "servicios y precios",
    ]

    return any(p in texto_n for p in patrones)


# ============================================================
# OPENAI - CONVERSACIÓN NATURAL
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    if not client:

        return (
            "¡Hola! 😊 Qué gusto saludarte. "
            "Si quieres, puedo mostrarte los servicios "
            "o ayudarte a reservar una hora."
        )

    system_prompt = f"""
Eres el asistente virtual de {ESTILISTA_NOMBRE}.

Responde en español de Chile, de forma breve, clara y amable.

REGLAS ESTRICTAS:
- Conversa de manera natural, como asistente de recepción de una peluquería/estilista.
- Solo puedes conversar sobre los servicios ofrecidos, sus precios, horarios de atención y agenda/reservas.
- No inventes servicios, precios, promociones, horarios ni disponibilidad.
- Si preguntan algo ajeno al negocio, responde en una frase breve que solo puedes ayudar con servicios, precios, horarios o reservas, y vuelve a conducir la conversación a esos temas.
- No obligues al cliente a usar un menú ni a responder 1 o 2. Puede escribir libremente.
- Primero ayuda a identificar el servicio que le interesa y su precio. Luego invítalo a indicar qué día quiere venir.
- Cuando necesites una fecha, pregunta solamente qué día quiere venir. No des ejemplos de cómo escribir la fecha salvo que el cliente los pida.
- Si el cliente pide disponibilidad o las próximas horas, la aplicación consulta Google Calendar. No repitas instrucciones innecesarias.
- Si el cliente quiere salir, no continuar o cambiar de servicio, respétalo inmediatamente.
- Sé breve, amable y orientado a avanzar la reserva paso a paso.
- No digas frases como "puedes escribirme como te salga natural", no enumeres ejemplos de fechas y no repitas comandos como PRÓXIMAS, OTRO SERVICIO o SALIR salvo que el cliente los pregunte.
- Si el cliente dice "mañana", "próximo miércoles" u otra fecha comprensible, continúa directamente con la consulta de agenda sin pedir que confirme el formato de la fecha.
- Nunca confirmes una hora por tu cuenta. La aplicación consulta la agenda real.
- Nunca hables de APIs, programación, Twilio, bases de datos ni sistemas internos.

SERVICIOS HOMBRE:
1. Corte de cabello hombre — $17.000. Incluye perfilado de cejas, lavado de cabello y aplicación de producto.
2. Perfilado de barba — $10.000.
3. Base de rizos permanente — $65.000.
4. Mechas — desde $70.000.
5. Decoloración global — $120.000.

SERVICIOS MUJER:
6. Corte de cabello mujer — $30.000. Incluye lavado de cabello, hidratación y brushing.
7. Masaje de hidratación — $45.000.
8. Botox capilar — desde $65.000.
9. Alisado permanente — desde $70.000.
10. Retoque de color de raíz — $50.000.
11. Baño de color — $30.000.
12. Balayage — estimado desde $150.000. Requiere diagnóstico capilar gratuito para definir el valor final.

HORARIO:
Lunes a sábado, de 10:00 a 18:00.
La última hora de inicio es a las 17:00.
"""

    mensajes = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    ultimos = historial[-15:]

    for item in ultimos:

        if (
            item.get("role")
            in ("user", "assistant")
            and item.get("content")
        ):

            mensajes.append({
                "role": item["role"],
                "content": item["content"]
            })

    # Evita duplicar el mismo mensaje de usuario
    if not (
        mensajes
        and mensajes[-1]["role"] == "user"
        and mensajes[-1]["content"] == pregunta
    ):

        mensajes.append({
            "role": "user",
            "content": pregunta
        })

    try:

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=mensajes,
        )

        respuesta = (
            response
            .choices[0]
            .message
            .content
            or ""
        ).strip()

        if respuesta:
            return respuesta

        return (
            "¿Te gustaría conocer los servicios "
            "o reservar una hora? 😊"
        )

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        import traceback

        print(
            traceback.format_exc()
        )

        return (
            "Disculpa 🙏 Tuve un problema procesando "
            "el mensaje. Intenta nuevamente."
        )


# ============================================================
# ESTADO DE RESERVA
# ============================================================

def resetear_reserva(estado):

    telefono = (
        estado
        .get("datos_reserva", {})
        .get("telefono")
    )

    estado["modo_agendar"] = False
    estado["paso"] = "inicio"
    estado["horas_ofrecidas"] = []

    estado["datos_reserva"] = {
        "servicio": None,
        "fecha_hora": None,
        "nombre": None,
        "telefono": telefono,
        "correo": None,
        "fecha_preferida": None,
        "mes_desde": None,
            "rango_horario": None,
    }


# ============================================================
# GOOGLE CALENDAR - ATENCIÓN PRESENCIAL
# ============================================================

def crear_evento_diego(
    inicio,
    servicio_codigo,
    nombre_cliente,
    telefono_cliente,
    correo_cliente
):

    try:

        service = obtener_calendar_service()

        servicio = obtener_servicio(
            servicio_codigo
        )

        fin = inicio + timedelta(
            minutes=DURACION_RESERVA
        )

        evento = {

            "summary":
                f"{servicio['nombre']} - {nombre_cliente}",

            "description":
                (
                    "Reserva creada por el "
                    "Asistente Virtual de "
                    f"Estilista {ESTILISTA_NOMBRE}.\n\n"
                    f"Cliente: {nombre_cliente}\n"
                    f"Teléfono: {telefono_cliente}\n"
                    f"Correo: {correo_cliente}\n"
                    f"Servicio: {servicio['nombre']}\n"
                    f"Valor: {precio_texto_servicio(servicio)}\n"
                    f"Duración: {DURACION_RESERVA} minutos\n"
                    "Origen: Asistente Virtual"
                ),

            "start": {
                "dateTime": inicio.isoformat(),
                "timeZone": TIMEZONE,
            },

            "end": {
                "dateTime": fin.isoformat(),
                "timeZone": TIMEZONE,
            },

            "attendees": [
                {
                    "email": correo_cliente,
                    "displayName": nombre_cliente,
                }
            ],

            "extendedProperties": {

                "private": {

                    "cliente":
                        nombre_cliente,

                    "telefono":
                        telefono_cliente,

                    "correo":
                        correo_cliente,

                    "servicio":
                        servicio["nombre"],

                    "origen":
                        (
                            f"Asistente Virtual "
                            f"{ESTILISTA_NOMBRE}"
                        ),
                }
            },
        }

        resultado = (
            service
            .events()
            .insert(
                calendarId=CALENDAR_ID,
                body=evento,
                sendUpdates="all",
            )
            .execute()
        )

        # Atención presencial: no se crea Google Meet.
        meet_url = None

        print(
            "EVENTO GOOGLE CREADO:",
            resultado.get("id")
        )

        return {
            "ok": True,
            "evento_id":
                resultado.get("id"),
            "link":
                resultado.get("htmlLink"),
            "meet_url":
                meet_url,
        }

    except Exception as e:

        print(
            "ERROR GOOGLE EVENT:",
            repr(e)
        )

        return {
            "ok": False,
            "error": str(e)
        }


# ============================================================
# RESERVA SEGURA SIN POSTGRESQL
# ============================================================

def crear_reserva_segura(
    inicio,
    datos,
    cliente_id,
    canal
):

    try:

        # Volver a comprobar disponibilidad real en Google Calendar
        # justo antes de crear la cita.
        disponible = verificar_disponibilidad(
            inicio,
            DURACION_RESERVA
        )

        if disponible is not True:
            return {
                "ok": False,
                "ocupada": True,
            }

        resultado = crear_evento_diego(
            inicio=inicio,
            servicio_codigo=datos["servicio"],
            nombre_cliente=datos["nombre"],
            telefono_cliente=datos["telefono"],
            correo_cliente=datos["correo"],
        )

        if not resultado["ok"]:
            return {
                "ok": False,
                "error": resultado.get("error")
            }

        return {
            "ok": True,
            "evento_id": resultado["evento_id"],
            "meet_url": resultado["meet_url"],
        }

    except Exception as e:
        print(
            "ERROR RESERVA SEGURA SIN DB:",
            repr(e)
        )
        return {
            "ok": False,
            "error": str(e)
        }


# ============================================================
# PROCESAR AGENDA
# ============================================================

def procesar_agenda(
    estado,
    texto,
    cliente_id,
    canal
):

    datos = estado["datos_reserva"]

    texto = (
        texto or ""
    ).strip()

    texto_n = normalizar_texto(texto)

    if texto_n in {"hola", "holi", "holaa", "buenas"} and estado.get("modo_agendar"):
        paso_actual = estado.get("paso")
        if paso_actual == "elegir_fecha":
            return "Hola 😊 ¿Qué día te gustaría venir?"
        if paso_actual == "seleccionar_hora":
            return "Hola 😊 ¿Qué hora prefieres?"

    fecha_exacta_detectada = detectar_fecha_solicitada(
        texto,
        None
    )

    mes_detectado = detectar_mes_solicitado(
        texto
    )

    rango_detectado = detectar_rango_horario(
        texto
    )

    if rango_detectado:
        datos["rango_horario"] = list(rango_detectado)

    if fecha_exacta_detectada and texto_menciona_fecha_o_mes(texto):
        datos["fecha_preferida"] = fecha_exacta_detectada.isoformat()
        datos["mes_desde"] = None

    elif mes_detectado:
        datos["mes_desde"] = mes_detectado.isoformat()
        datos["fecha_preferida"] = None


    # ========================================================
    # REINICIAR / CANCELAR
    # ========================================================

    if quiere_reiniciar_conversacion(texto):
        telefono_guardado = datos.get("telefono")
        resetear_reserva(estado)
        estado["datos_reserva"]["telefono"] = telefono_guardado
        estado["paso"] = "menu_principal"
        return mensaje_menu_principal()

    if usuario_no_quiere(texto):

        resetear_reserva(
            estado
        )

        return (
            "No hay problema  "
            "Cuando quieras conocer los servicios "
            "o reservar una hora con Diego, "
            "aquí estaré. ¡Que estés muy bien! "
        )


    # ========================================================
    # CAMBIAR DE SERVICIO EN CUALQUIER MOMENTO
    # ========================================================

    if quiere_cambiar_servicio(texto):

        telefono_guardado = datos.get("telefono")
        resetear_reserva(estado)
        estado["modo_agendar"] = True
        estado["paso"] = "inicio"
        estado["datos_reserva"]["telefono"] = telefono_guardado

        return (
            "Claro 😊 Podemos cambiar de servicio.\n\n"
            + mostrar_servicios()
        )

    # Si escribe el nombre de otro servicio durante la reserva,
    # cambiamos el servicio sin obligarlo a volver al menú principal.
    if datos.get("servicio") and not re.fullmatch(r"\s*\d{1,2}\s*", texto):
        nuevo_servicio = detectar_servicio(texto)
        if nuevo_servicio and nuevo_servicio != datos.get("servicio"):
            datos["servicio"] = nuevo_servicio
            datos["fecha_hora"] = None
            datos["fecha_preferida"] = None
            datos["mes_desde"] = None
            datos["rango_horario"] = None
            estado["horas_ofrecidas"] = []

            info = obtener_servicio(nuevo_servicio)
            return (
                f"Perfecto, cambiamos a {info['nombre']} 😊\n"
                f"💰 {precio_texto_servicio(info)}\n\n"
                "¿Qué día te gustaría venir?"
            )

    # ========================================================
    # SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        corte_ambiguo = (
            "corte" in texto_n
            or "cortar" in texto_n
            or "cortarme" in texto_n
        ) and (
            "pelo" in texto_n
            or "cabello" in texto_n
            or texto_n in {"corte", "quiero corte", "quiero un corte"}
        )

        if not servicio and texto_n in {"hombre", "varon", "masculino"}:
            servicio = "corte_hombre"

        if not servicio and texto_n in {"mujer", "dama", "femenino"}:
            servicio = "corte_mujer"

        if not servicio and corte_ambiguo:
            return (
                "Perfecto ✂️ ¿El corte es para hombre o mujer?"
            )

        if servicio:

            datos["servicio"] = servicio

            servicio_info = obtener_servicio(
                servicio
            )

            # ====================================================
            # EL CLIENTE YA INDICÓ UNA HORA
            # Ejemplo: "quiero un corte a las 3"
            # ====================================================

            hora_solicitada = (
                construir_fecha_hora_solicitada(
                    texto
                )
            )

            if hora_solicitada:

                if canal == "whatsapp":

                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        (
                            "🔎 Estoy revisando si esa hora "
                            "está disponible en mi agenda. "
                            "Dame un momento 😊"
                        )
                    )

                disponible = verificar_disponibilidad(
                    hora_solicitada,
                    DURACION_RESERVA
                )

                if disponible is None:

                    return (
                        "No pude comprobar la agenda "
                        "en este momento 😕.\n\n"
                        "Intenta nuevamente en unos segundos."
                    )

                if disponible is True:

                    datos["fecha_hora"] = (
                        hora_solicitada.isoformat()
                    )

                    estado["paso"] = "nombre"

                    precio = precio_texto_servicio(
                        servicio_info
                    )

                    return (
                        "¡Sí! Esa hora está disponible 😊\n\n"
                        f"💈 {servicio_info['nombre']}\n"
                        f"💰 {precio}\n"
                        f"📅 {formato_fecha_larga(hora_solicitada)}\n\n"
                        "¿Me indicas tu nombre para continuar "
                        "con la reserva?"
                    )

                # La hora solicitada está ocupada:
                # mostrar alternativas reales.
                if canal == "whatsapp":

                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        (
                            "Esa hora está ocupada. "
                            "Estoy buscando las alternativas "
                            "más próximas 😊"
                        )
                    )

                horas = buscar_proximas_15_horas(desde=hora_solicitada)

                if horas is None:
                    return (
                        "No pude consultar la agenda en este momento 😕.\n\n"
                        "Revisa la conexión con Google Calendar e intenta nuevamente."
                    )

                if not horas:

                    return (
                        f"La hora solicitada para "
                        f"{servicio_info['nombre']} está ocupada "
                        "y por ahora no encontré otras horas "
                        "disponibles."
                    )

                estado["horas_ofrecidas"] = [
                    h.isoformat()
                    for h in horas
                ]

                estado["paso"] = "seleccionar_hora"

                return (
                    f"La hora que pediste "
                    f"({formato_fecha_larga(hora_solicitada)}) "
                    "no está disponible 😕.\n\n"
                    "Estas son las próximas opciones disponibles:\n\n"
                    f"{formatear_opciones_horas(horas)}\n\n"
                    "Respóndeme con el número de la opción "
                    "que prefieras."
                )

            # ====================================================
            # NO INDICÓ HORA: respetar fecha, rango o mes solicitado
            # ====================================================

            if datos.get("fecha_preferida") and datos.get("rango_horario"):

                fecha_preferida = datetime.fromisoformat(
                    datos["fecha_preferida"]
                )

                rango_guardado = tuple(datos["rango_horario"])

                if canal == "whatsapp":
                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        "🔎 Estoy revisando las horas disponibles en ese horario 😊"
                    )

                horas_dia = buscar_horas_disponibles_dia(
                    fecha_preferida
                )

                if horas_dia is None:
                    return (
                        "No pude consultar la agenda en este momento 😕. "
                        "Intenta nuevamente en unos segundos."
                    )

                horas = filtrar_horas_por_rango(
                    horas_dia,
                    rango_guardado
                )

                if not horas:
                    h_ini, h_fin = rango_guardado
                    estado["paso"] = "elegir_fecha"
                    return (
                        f"No tengo horas disponibles entre las {h_ini}:00 "
                        f"y las {h_fin}:00 ese día. "
                        "¿Quieres revisar otro horario?"
                    )

                estado["horas_ofrecidas"] = [
                    h.isoformat() for h in horas
                ]
                estado["paso"] = "seleccionar_hora"

                precio = precio_texto_servicio(servicio_info)

                return (
                    f"💈 {servicio_info['nombre']}\n"
                    f"💰 {precio}\n\n"
                    f"Tengo estas horas disponibles:\n\n"
                    f"{formatear_opciones_horas(horas)}\n\n"
                    "¿Cuál prefieres?"
                )

            if datos.get("fecha_preferida"):

                if canal == "whatsapp":
                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        "🔎 Estoy revisando las horas disponibles para ese día 😊"
                    )

                fecha_preferida = datetime.fromisoformat(
                    datos["fecha_preferida"]
                )

                horas = buscar_horas_disponibles_dia(
                    fecha_preferida
                )

                if horas is None:
                    return (
                        "No pude consultar Google Calendar en este momento 😕.\n\n"
                        "Intenta nuevamente en unos segundos."
                    )

                if not es_dia_atencion(fecha_preferida):
                    return (
                        f"El {DIAS_NOMBRES[fecha_preferida.weekday()]} "
                        f"{fecha_preferida.day}/{fecha_preferida.month} no atendemos.\n\n"
                        "Atendemos de lunes a sábado entre 10:00 y 18:00.\n\n"
                        "Puedes indicarme otra fecha o escribir MENÚ."
                    )

                if not horas:
                    desde = (
                        fecha_preferida
                        + timedelta(days=1)
                    ).replace(
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0
                    )

                    horas = buscar_proximas_15_horas(
                        desde=desde
                    )

                    if horas is None:
                        return (
                            "Ese día está completo y no pude consultar "
                            "las horas siguientes en este momento 😕."
                        )

                    if not horas:
                        return (
                            f"El {fecha_preferida.day}/{fecha_preferida.month} "
                            "está completo y no encontré horas posteriores disponibles."
                        )

                    prefijo = (
                        f"El {fecha_preferida.day}/{fecha_preferida.month} "
                        "está completo 😕.\n\n"
                        "Estas son las próximas 15 horas disponibles desde el día siguiente:\n\n"
                    )

                else:
                    prefijo = (
                        f"Sí 😊 Para el {fecha_preferida.day}/{fecha_preferida.month} "
                        "tengo estas horas disponibles:\n\n"
                    )

            elif datos.get("mes_desde"):

                desde_mes = datetime.fromisoformat(
                    datos["mes_desde"]
                )

                if canal == "whatsapp":
                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        "🔎 Estoy revisando la disponibilidad desde ese mes 😊"
                    )

                horas = buscar_proximas_15_horas(
                    desde=desde_mes
                )

                if horas is None:
                    return (
                        "No pude consultar Google Calendar en este momento 😕.\n\n"
                        "Intenta nuevamente en unos segundos."
                    )

                if not horas:
                    return (
                        f"No encontré horas disponibles desde "
                        f"{MESES_NOMBRES[desde_mes.month - 1]} por ahora."
                    )

                prefijo = (
                    f"Perfecto 😊 Estas son las primeras 15 horas disponibles "
                    f"desde {MESES_NOMBRES[desde_mes.month - 1]}:\n\n"
                )

            else:

                # Flujo conversacional: después de elegir el servicio,
                # primero preguntamos qué día quiere venir. No consultamos
                # Calendar hasta que el cliente indique una fecha o pida
                # explícitamente las próximas horas disponibles.
                estado["paso"] = "elegir_fecha"

                precio = precio_texto_servicio(
                    servicio_info
                )

                estado["paso"] = "elegir_fecha"
                return (
                    f"Perfecto 😊\n\n"
                    f"💈 {servicio_info['nombre']}\n"
                    f"💰 {precio}\n\n"
                    "¿Qué día te gustaría venir?"
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            estado["paso"] = "seleccionar_hora"

            precio = precio_texto_servicio(
                servicio_info
            )

            return (
                f"💈 {servicio_info['nombre']}\n"
                f"💰 {precio}\n\n"
                f"{prefijo}"
                f"{formatear_opciones_horas(horas)}\n\n"
                "Respóndeme con el número de la hora que prefieras.\n"
                "También puedes escribir otra fecha si prefieres."
            )

        return mostrar_servicios()


    # ========================================================
    # ELEGIR FECHA - CONVERSACIÓN NATURAL
    # ========================================================

    if estado["paso"] == "elegir_fecha":

        servicio_info = obtener_servicio(datos["servicio"])

        # Si el cliente entrega una fecha + hora exacta, revisar directamente.
        # Ej.: "mañana a las 11", "miércoles a las 15:00".
        fecha_hora_directa = construir_fecha_hora_solicitada(texto)

        if fecha_hora_directa:
            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando esa hora en la agenda 😊"
                )

            disponible = verificar_disponibilidad(
                fecha_hora_directa,
                DURACION_RESERVA
            )

            if disponible is None:
                return (
                    "No pude consultar la agenda en este momento 😕. "
                    "Intenta nuevamente en unos segundos."
                )

            if disponible is True:
                datos["fecha_hora"] = fecha_hora_directa.isoformat()
                estado["paso"] = "nombre"

                return (
                    "¡Sí! Esa hora está disponible 😊\n\n"
                    f"💈 {servicio_info['nombre']}\n"
                    f"💰 {precio_texto_servicio(servicio_info)}\n"
                    f"📅 {formato_fecha_larga(fecha_hora_directa)}\n\n"
                    "¿Me indicas tu nombre para continuar con la reserva?"
                )

            # Si está ocupada, mostrar alternativas desde esa misma fecha/hora.
            horas = buscar_proximas_15_horas(desde=fecha_hora_directa)

            if horas is None:
                return (
                    "Esa hora no está disponible y no pude consultar "
                    "las alternativas en este momento 😕."
                )

            estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
            estado["paso"] = "seleccionar_hora"

            return (
                "Esa hora no está disponible 😕.\n\n"
                "Tengo estas alternativas:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
            )

        # Si pide un rango, revisar solo horas disponibles dentro de ese intervalo.
        # Si el rango venía en un mensaje anterior ("mañana por la tarde"),
        # también lo conservamos.
        rango_horario = detectar_rango_horario(texto)

        if rango_horario:
            datos["rango_horario"] = list(rango_horario)
            fecha_rango = fecha_exacta_detectada

            if not fecha_rango and datos.get("fecha_preferida"):
                fecha_rango = datetime.fromisoformat(datos["fecha_preferida"])

            if not fecha_rango:
                return "¿Para qué día quieres que revise ese horario?"

            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando las horas disponibles en ese horario 😊"
                )

            horas_dia = buscar_horas_disponibles_dia(fecha_rango)

            if horas_dia is None:
                return (
                    "No pude consultar la agenda en este momento 😕. "
                    "Intenta nuevamente en unos segundos."
                )

            horas_rango = filtrar_horas_por_rango(
                horas_dia,
                rango_horario
            )

            h_ini, h_fin = rango_horario

            if not horas_rango:
                return (
                    f"No tengo horas disponibles entre las {h_ini}:00 "
                    f"y las {h_fin}:00 ese día. "
                    "¿Quieres que revise otro horario?"
                )

            estado["horas_ofrecidas"] = [
                h.isoformat() for h in horas_rango
            ]
            estado["paso"] = "seleccionar_hora"

            return (
                f"Sí 😊 En ese horario tengo disponible:\n\n"
                f"{formatear_opciones_horas(horas_rango)}\n\n"
                "¿Cuál prefieres?"
            )

        # El cliente puede pedir directamente las próximas opciones.
        if quiere_proximas_fechas(texto):

            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy buscando las próximas horas disponibles en la agenda 😊"
                )

            horas = buscar_proximas_15_horas()

            if horas is None:
                return (
                    "No pude consultar la agenda en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

            if not horas:
                return (
                    "Por ahora no encontré horas disponibles. "
                    "Puedes indicarme otra fecha para revisar."
                )

            estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
            estado["paso"] = "seleccionar_hora"

            return (
                f"Para {servicio_info['nombre']}, estas son las próximas horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "Respóndeme con el número de la opción que prefieras.\n"
                "También puedes escribir otra fecha si prefieres."
            )

        # Mes sin día específico, por ejemplo "en septiembre".
        if mes_detectado:

            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando la disponibilidad desde ese mes 😊"
                )

            horas = buscar_proximas_15_horas(desde=mes_detectado)

            if horas is None:
                return (
                    "No pude consultar la agenda en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

            if not horas:
                return (
                    f"No encontré horas disponibles desde "
                    f"{MESES_NOMBRES[mes_detectado.month - 1]} por ahora. "
                    "Puedes indicarme otra fecha."
                )

            estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
            estado["paso"] = "seleccionar_hora"

            return (
                f"Perfecto 😊 Estas son las primeras horas disponibles desde "
                f"{MESES_NOMBRES[mes_detectado.month - 1]}:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "Respóndeme con el número que prefieras o escribe otra fecha."
            )

        # Día natural: mañana, próximo miércoles, 2 de septiembre, etc.
        if fecha_exacta_detectada and texto_menciona_fecha_o_mes(texto):

            fecha_consultada = fecha_exacta_detectada

            # Si había un rango guardado previamente, respetarlo.
            if datos.get("rango_horario"):
                rango_guardado = tuple(datos["rango_horario"])

                if canal == "whatsapp":
                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        "🔎 Estoy revisando las horas disponibles en ese horario 😊"
                    )

                horas_dia = buscar_horas_disponibles_dia(
                    fecha_consultada
                )

                if horas_dia is None:
                    return (
                        "No pude consultar la agenda en este momento 😕. "
                        "Intenta nuevamente en unos segundos."
                    )

                horas_rango = filtrar_horas_por_rango(
                    horas_dia,
                    rango_guardado
                )

                if horas_rango:
                    estado["horas_ofrecidas"] = [
                        h.isoformat() for h in horas_rango
                    ]
                    estado["paso"] = "seleccionar_hora"

                    return (
                        f"Tengo estas horas disponibles:\n\n"
                        f"{formatear_opciones_horas(horas_rango)}\n\n"
                        "¿Cuál prefieres?"
                    )

                h_ini, h_fin = rango_guardado
                return (
                    f"No tengo horas disponibles entre las {h_ini}:00 "
                    f"y las {h_fin}:00 ese día. "
                    "¿Quieres revisar otro horario?"
                )

            if not es_dia_atencion(fecha_consultada):
                return (
                    f"Ese {DIAS_NOMBRES[fecha_consultada.weekday()]} no atendemos.\n\n"
                    "Atendemos de lunes a sábado, de 10:00 a 18:00. "
                    "¿Qué otro día te acomoda?"
                )

            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando las horas disponibles para ese día 😊"
                )

            horas = buscar_horas_disponibles_dia(fecha_consultada)

            if horas is None:
                return (
                    "No pude consultar la agenda en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

            if not horas:
                desde = (
                    fecha_consultada + timedelta(days=1)
                ).replace(hour=0, minute=0, second=0, microsecond=0)

                horas = buscar_proximas_15_horas(desde=desde)

                if horas is None:
                    return (
                        "Ese día está completo y no pude consultar las fechas siguientes 😕. "
                        "Intenta nuevamente en unos segundos."
                    )

                if not horas:
                    return (
                        "Ese día está completo y por ahora no encontré horas posteriores. "
                        "Puedes indicarme otra fecha."
                    )

                estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
                estado["paso"] = "seleccionar_hora"

                return (
                    f"El {fecha_consultada.day}/{fecha_consultada.month} está completo 😕.\n\n"
                    "Estas son las próximas opciones disponibles:\n\n"
                    f"{formatear_opciones_horas(horas)}\n\n"
                    "Respóndeme con el número que prefieras o escribe otra fecha."
                )

            estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
            estado["paso"] = "seleccionar_hora"

            return (
                f"Sí 😊 Para el {fecha_consultada.day}/{fecha_consultada.month} "
                "tengo estas horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "Respóndeme con el número de la hora que prefieras.\n"
                "También puedes escribir otra fecha si prefieres."
            )

        return "¿Qué día te gustaría venir?"

    # ========================================================
    # HORA
    # ========================================================

    if estado["paso"] == "seleccionar_hora":

        servicio_info = obtener_servicio(datos["servicio"])

        # También puede escribir directamente una nueva fecha y hora.
        fecha_hora_directa = construir_fecha_hora_solicitada(texto)

        if fecha_hora_directa:
            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando esa hora en la agenda 😊"
                )

            disponible = verificar_disponibilidad(
                fecha_hora_directa,
                DURACION_RESERVA
            )

            if disponible is None:
                return "No pude consultar la agenda en este momento 😕."

            if disponible:
                datos["fecha_hora"] = fecha_hora_directa.isoformat()
                estado["paso"] = "nombre"
                return (
                    "¡Sí! Esa hora está disponible 😊\n\n"
                    f"📅 {formato_fecha_larga(fecha_hora_directa)}\n\n"
                    "¿Me indicas tu nombre?"
                )

            horas = buscar_proximas_15_horas(desde=fecha_hora_directa)
            if horas is None:
                return "Esa hora no está disponible y no pude consultar alternativas 😕."

            estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
            return (
                "Esa hora no está disponible 😕.\n\n"
                "Estas son las alternativas más próximas:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
            )

        # También puede acotar por rango: "entre las 10 y las 12".
        rango_horario = detectar_rango_horario(texto)

        if rango_horario:
            datos["rango_horario"] = list(rango_horario)
            fecha_rango = detectar_fecha_solicitada(texto, None)

            if not (
                fecha_rango
                and texto_menciona_fecha_o_mes(texto)
            ):
                fecha_rango = None

            if not fecha_rango and datos.get("fecha_preferida"):
                fecha_rango = datetime.fromisoformat(datos["fecha_preferida"])

            # Si ya le mostramos opciones, usar el día de la primera opción.
            if (
                not fecha_rango
                and estado.get("horas_ofrecidas")
            ):
                fecha_rango = datetime.fromisoformat(
                    estado["horas_ofrecidas"][0]
                )

            if not fecha_rango:
                return "¿Para qué día quieres que revise ese horario?"

            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando ese intervalo en la agenda 😊"
                )

            horas_dia = buscar_horas_disponibles_dia(fecha_rango)

            if horas_dia is None:
                return "No pude consultar la agenda en este momento 😕."

            horas_rango = filtrar_horas_por_rango(
                horas_dia,
                rango_horario
            )

            h_ini, h_fin = rango_horario

            if not horas_rango:
                return (
                    f"No tengo horas disponibles entre las {h_ini}:00 "
                    f"y las {h_fin}:00 ese día. "
                    "¿Quieres que revise otro horario?"
                )

            estado["horas_ofrecidas"] = [
                h.isoformat() for h in horas_rango
            ]

            return (
                "En ese intervalo tengo disponible:\n\n"
                f"{formatear_opciones_horas(horas_rango)}\n\n"
                "¿Cuál prefieres?"
            )

        # ====================================================
        # EL CLIENTE PUEDE CAMBIAR DE DÍA EN LENGUAJE NATURAL
        # Ejemplos:
        # "mañana no hay?"
        # "¿y el viernes?"
        # "tienes disponibilidad el miércoles?"
        # ====================================================

        fecha_consultada = detectar_fecha_solicitada(
            texto,
            None
        )

        mes_consultado = detectar_mes_solicitado(
            texto
        )

        texto_n = normalizar_texto(texto)

        if mes_consultado:

            if canal == "whatsapp":
                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    "🔎 Estoy revisando la disponibilidad desde ese mes 😊"
                )

            horas_mes = buscar_proximas_15_horas(
                desde=mes_consultado
            )

            if horas_mes is None:
                return (
                    "No pude consultar Google Calendar en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

            if not horas_mes:
                return (
                    f"No encontré horas disponibles desde "
                    f"{MESES_NOMBRES[mes_consultado.month - 1]} por ahora."
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas_mes
            ]

            datos["mes_desde"] = mes_consultado.isoformat()
            datos["fecha_preferida"] = None

            return (
                f"Estas son las primeras 15 horas disponibles desde "
                f"{MESES_NOMBRES[mes_consultado.month - 1]}:\n\n"
                f"{formatear_opciones_horas(horas_mes)}\n\n"
                "Respóndeme con el número de la hora que prefieras.\n"
                "También puedes indicar una fecha exacta, por ejemplo "
                "\"2 de septiembre\", o escribir MENÚ."
            )

        menciona_dia = texto_menciona_fecha_o_mes(
            texto
        )

        if (
            fecha_consultada
            and menciona_dia
        ):

            if canal == "whatsapp":

                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    (
                        "🔎 Estoy revisando la disponibilidad "
                        "de ese día en mi agenda. "
                        "Dame un momento 😊"
                    )
                )

            horas_dia = buscar_horas_disponibles_dia(
                fecha_consultada
            )

            if horas_dia is None:

                return (
                    "No pude comprobar la agenda "
                    "en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

            if not es_dia_atencion(
                fecha_consultada
            ):

                return (
                    f"El {DIAS_NOMBRES[fecha_consultada.weekday()]} "
                    "no atendemos.\n\n"
                    "Atendemos de lunes a sábado "
                    "entre 10:00 y 18:00.\n\n"
                    "¿Qué otro día quieres revisar?"
                )

            if not horas_dia:

                siguiente_dia = (
                    fecha_consultada
                    + timedelta(days=1)
                ).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0
                )

                if canal == "whatsapp":

                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        (
                            "Ese día está completo. "
                            "Estoy buscando las próximas "
                            "horas disponibles 😊"
                        )
                    )

                horas_siguientes = buscar_proximas_15_horas(
                    desde=siguiente_dia
                )

                if horas_siguientes is None:
                    return (
                        "No pude consultar las horas siguientes en Google Calendar 😕.\n\n"
                        "Intenta nuevamente en unos segundos."
                    )

                if not horas_siguientes:

                    return (
                        f"Para el "
                        f"{DIAS_NOMBRES[fecha_consultada.weekday()]} "
                        f"{fecha_consultada.day}/{fecha_consultada.month} "
                        "no tengo horas disponibles 😕.\n\n"
                        "Tampoco encontré horas disponibles "
                        "en los días siguientes por ahora."
                    )

                estado["horas_ofrecidas"] = [
                    h.isoformat()
                    for h in horas_siguientes
                ]

                estado["paso"] = "seleccionar_hora"

                return (
                    f"Para el "
                    f"{DIAS_NOMBRES[fecha_consultada.weekday()]} "
                    f"{fecha_consultada.day}/{fecha_consultada.month} "
                    "no tengo horas disponibles 😕.\n\n"
                    "Estas son las próximas 15 horas disponibles "
                    "desde el día siguiente:\n\n"
                    f"{formatear_opciones_horas(horas_siguientes)}\n\n"
                    "Respóndeme con el número de la hora "
                    "que prefieras, del 1 al 15."
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas_dia
            ]

            return (
                f"Sí 😊 Para el "
                f"{DIAS_NOMBRES[fecha_consultada.weekday()]} "
                f"{fecha_consultada.day}/{fecha_consultada.month} "
                "tengo estas horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas_dia)}\n\n"
                "Respóndeme con el número de la hora "
                "que prefieras."
            )

        match = re.fullmatch(
            r"\s*(\d{1,2})\s*",
            texto
        )

        if not match:

            return (
                "¿Qué hora prefieres?"
            )

        numero = int(
            match.group(1)
        )

        horas_guardadas = (
            estado
            .get(
                "horas_ofrecidas",
                []
            )
        )

        if (
            numero < 1
            or numero > len(horas_guardadas)
        ):

            return (
                f"Elige un número entre 1 y "
                f"{len(horas_guardadas)}, por favor ."
            )

        fecha_hora = datetime.fromisoformat(
            horas_guardadas[numero - 1]
        )

        # ====================================================
        # SEGUNDA COMPROBACIÓN EN GOOGLE CALENDAR
        # ====================================================

        if canal == "whatsapp":

            enviar_mensaje_progreso_twilio(
                cliente_id,
                (
                    "🔎 Perfecto. Estoy verificando nuevamente "
                    "esa hora en la agenda antes de continuar 😊"
                )
            )

        disponible = verificar_disponibilidad(
            fecha_hora,
            DURACION_RESERVA
        )

        if disponible is None:

            return (
                "No pude comprobar la agenda "
                "en este momento .\n\n"
                "Intenta nuevamente en unos segundos."
            )

        if not disponible:

            horas = buscar_proximas_15_horas()

            if horas is None:
                return (
                    "Esa hora ya no está disponible y no pude actualizar "
                    "la agenda en este momento 😕. Intenta nuevamente."
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Esa hora acaba de ocuparse .\n\n"
                "Actualicé las horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
            )

        datos["fecha_hora"] = (
            fecha_hora.isoformat()
        )

        estado["paso"] = "nombre"

        return (
            "¡Perfecto! 😊\n\n"
            "La hora sigue disponible:\n"
            f"📅 {formato_fecha_larga(fecha_hora)}\n\n"
            "Ahora necesito tus datos para cerrar la cita.\n\n"
            "¿Me indicas tu nombre?"
        )


    # ========================================================
    # NOMBRE
    # ========================================================

    if estado["paso"] == "nombre":

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? "
            )

        datos["nombre"] = texto

        actualizar_conversacion_datos(
            cliente_id,
            canal,
            nombre=texto
        )

        if (
            canal == "whatsapp"
            and datos.get("telefono")
        ):

            actualizar_conversacion_datos(
                cliente_id,
                canal,
                telefono=datos["telefono"]
            )

            estado["paso"] = "correo"

            return (
                f"Perfecto, {texto} 😊\n\n"
                "Ya tengo tu número de WhatsApp.\n\n"
                "¿Cuál es tu correo electrónico?\n\n"
                "Lo usaremos para enviarte la invitación "
                "de Google Calendar con los datos de tu cita presencial."
            )

        estado["paso"] = "telefono"

        return (
            f"Perfecto, {texto} 😊\n\n"
            "¿Cuál es tu número de teléfono? "
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if estado["paso"] == "telefono":

        # En WhatsApp/Twilio el número ya viene identificado
        # automáticamente en el campo From.
        if (
            canal == "whatsapp"
            and datos.get("telefono")
        ):

            actualizar_conversacion_datos(
                cliente_id,
                canal,
                telefono=datos["telefono"]
            )

            estado["paso"] = "correo"

            return (
                "Perfecto 😊\n\n"
                "Ya tengo tu número de WhatsApp.\n\n"
                "¿Cuál es tu correo electrónico?\n\n"
                "Lo usaremos para enviarte la invitación "
                "de Google Calendar con los datos de tu cita presencial."
            )

        telefono_limpio = re.sub(
            r"[^\d+]",
            "",
            texto
        )

        if len(
            re.sub(
                r"\D",
                "",
                telefono_limpio
            )
        ) < 8:

            return (
                "¿Me indicas un número de teléfono "
                "válido, por favor? "
            )

        datos["telefono"] = telefono_limpio

        actualizar_conversacion_datos(
            cliente_id,
            canal,
            telefono=telefono_limpio
        )

        estado["paso"] = "correo"

        return (
            "Perfecto 😊\n\n"
            "¿Cuál es tu correo electrónico?\n\n"
            "Lo usaremos para enviarte la invitación "
            "de Google Calendar con los datos de tu cita presencial."
        )


    # ========================================================
    # CORREO
    # ========================================================

    if estado["paso"] == "correo":

        correo = texto.lower().strip()

        patron_correo = (
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        )

        if not re.match(
            patron_correo,
            correo
        ):

            return (
                "Parece que el correo no está correcto .\n\n"
                "Escríbelo nuevamente, por ejemplo:\n"
                "nombre@gmail.com"
            )

        datos["correo"] = correo

        actualizar_conversacion_datos(
            cliente_id,
            canal,
            correo=correo
        )

        estado["paso"] = "confirmar"

        if canal == "whatsapp":

            enviar_mensaje_progreso_twilio(
                cliente_id,
                (
                    "📅 Gracias. Estoy haciendo la última validación "
                    "en la agenda y cerrando tu cita. Dame un momento 😊"
                )
            )

        return completar_reserva(
            estado,
            cliente_id,
            canal
        )


    return completar_reserva(
        estado,
        cliente_id,
        canal
    )


# ============================================================
# COMPLETAR RESERVA
# ============================================================

def completar_reserva(
    estado,
    cliente_id,
    canal
):

    datos = estado["datos_reserva"]

    if not datos["servicio"]:

        estado["paso"] = "servicio"

        return mostrar_servicios()

    if not datos["fecha_hora"]:

        estado["paso"] = "seleccionar_hora"

        horas = buscar_proximas_15_horas()

        if horas is None:
            return (
                "No pude consultar la agenda en este momento 😕. "
                "Intenta nuevamente en unos segundos."
            )

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Estas son las próximas 15 horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "Respóndeme con el número de la hora que prefieras."
        )

    if not datos["nombre"]:

        estado["paso"] = "nombre"

        return (
            "¿Me indicas tu nombre? "
        )

    if not datos["telefono"]:

        estado["paso"] = "telefono"

        return (
            "¿Cuál es tu número de teléfono? "
        )

    if not datos["correo"]:

        estado["paso"] = "correo"

        return (
            "¿Cuál es tu correo electrónico? "
        )

    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )

    # ========================================================
    # RESERVA SEGURA
    # ========================================================

    resultado = crear_reserva_segura(
        inicio=inicio,
        datos=datos,
        cliente_id=cliente_id,
        canal=canal
    )

    if resultado.get("ocupada"):

        datos["fecha_hora"] = None

        estado["paso"] = "seleccionar_hora"

        horas = buscar_proximas_15_horas()

        if horas is None:
            return (
                "No pude actualizar la agenda en este momento 😕. "
                "Intenta nuevamente en unos segundos."
            )

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Justo esa hora acaba de ocuparse 😕.\n\n"
            "Volví a consultar la agenda y estas son las "
            "próximas 15 horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "Respóndeme con el número de la nueva hora "
            "que prefieras."
        )

    if not resultado["ok"]:

        print(
            "ERROR RESERVANDO:",
            resultado.get("error")
        )

        return (
            "No pude completar la reserva "
            "en este momento .\n\n"
            "Intenta nuevamente en unos segundos."
        )

    servicio = obtener_servicio(
        datos["servicio"]
    )

    meet_url = resultado.get(
        "meet_url"
    )

    actualizar_conversacion_datos(
        cliente_id,
        canal,
        nombre=datos["nombre"],
        telefono=datos["telefono"],
        correo=datos["correo"],
        servicio=servicio["nombre"],
        fecha_reserva=inicio,
        meet_url=meet_url,
        estado="reserva_confirmada"
    )

    fecha_texto = formato_fecha_larga(
        inicio
    )

    # ========================================================
    # GUARDAR MENSAJE DE CONFIRMACIÓN
    # ========================================================

    # Antes de resetear guardamos el estado.
    telefono_guardar = datos["telefono"]

    resetear_reserva(
        estado
    )

    estado["datos_reserva"]["telefono"] = (
        telefono_guardar
    )

    precio = precio_texto_servicio(
        servicio
    )

    respuesta = (
        " ¡Reserva confirmada!\n\n"
        f" Servicio: {servicio['nombre']}\n"
        f" Valor: {precio}\n"
        f" Cliente: {datos['nombre']}\n"
        f" Teléfono: {datos['telefono']}\n"
        f" Correo: {datos['correo']}\n"
        f" {fecha_texto}\n\n"
        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"
    )

    respuesta += (
        "La invitación de Google Calendar fue enviada "
        "al correo indicado.\n\n"
    )

    respuesta += (
        "La atención dura 1 hora.\n\n"
        "📍 Dirección de atención:\n"
        "2 Norte 280\n\n"
        "💳 Datos de transferencia:\n"
        "Nombre: Diego\n"
        "RUT: 18.149.067-5\n"
        "Banco: BancoEstado\n"
        "Tipo de cuenta: Cuenta Vista\n"
        "N° de cuenta: 18149067\n\n"
        "¡Te esperamos! 😊"
    )

    return respuesta


# ============================================================
# SESIONES WHATSAPP
# ============================================================

WA_SESSIONS = {}

PROCESSED_MSG_IDS = {}

DEDUP_TTL_SECONDS = 120


def get_wa_session(wa_id):

    if wa_id not in WA_SESSIONS:

        WA_SESSIONS[wa_id] = {

            "historial": [],

            "modo_agendar": False,

            "paso": "menu_principal",

            "horas_ofrecidas": [],

            "datos_reserva": {

                "servicio": None,
                "fecha_hora": None,
                "nombre": None,
                "telefono": wa_id,
                "correo": None,
                "fecha_preferida": None,
                "mes_desde": None,
            "rango_horario": None,
            },
        }

    return WA_SESSIONS[wa_id]


# ============================================================
# TWILIO / WHATSAPP
# ============================================================

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886"
)

if (
    TWILIO_WHATSAPP_FROM
    and not TWILIO_WHATSAPP_FROM.startswith("whatsapp:")
):
    TWILIO_WHATSAPP_FROM = (
        "whatsapp:" + TWILIO_WHATSAPP_FROM
    )


twilio_client = None

if (
    TWILIO_ACCOUNT_SID
    and TWILIO_AUTH_TOKEN
):
    try:
        twilio_client = TwilioClient(
            TWILIO_ACCOUNT_SID,
            TWILIO_AUTH_TOKEN
        )
    except Exception as e:
        print(
            "ERROR INICIALIZANDO TWILIO CLIENT:",
            repr(e)
        )


def enviar_mensaje_progreso_twilio(
    telefono_twilio,
    texto
):
    """
    Envía un mensaje inmediato mientras el webhook continúa
    procesando la búsqueda de disponibilidad.
    """

    if not twilio_client:
        return False

    if not telefono_twilio:
        return False

    try:

        destino = telefono_twilio

        if not destino.startswith("whatsapp:"):
            destino = "whatsapp:" + destino

        twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=destino,
            body=texto
        )

        print(
            "MENSAJE PROGRESO TWILIO ENVIADO:",
            destino
        )

        return True

    except Exception as e:

        print(
            "ERROR MENSAJE PROGRESO TWILIO:",
            repr(e)
        )

        return False


def normalizar_telefono_twilio(valor):
    """
    Twilio entrega:
    whatsapp:+56912345678

    Para guardar la reserva usamos:
    +56912345678
    """
    valor = (valor or "").strip()

    if valor.startswith("whatsapp:"):
        return valor[len("whatsapp:"):]

    return valor


# ============================================================
# HOME
# ============================================================

@app.route("/")
def home():

    return redirect(
        url_for("chat")
    )


# ============================================================
# CHAT WEB
# ============================================================

@app.route(
    "/chat",
    methods=["GET", "POST"]
)
def chat():

    session.permanent = True

    cliente_id = (
        session.get("cliente_id")
    )

    if not cliente_id:

        cliente_id = (
            "web_"
            + hashlib.sha256(
                os.urandom(32)
            ).hexdigest()[:30]
        )

        session["cliente_id"] = cliente_id


    if "historial" not in session:

        session["historial"] = [

            {
                "role":
                    "assistant",

                "content":
                    (
                        "¡Hola!  "
                        "Soy el Asistente Virtual "
                        "de Estilista Diego \n\n"
                        "¿Cómo estás?"
                    ),
            }
        ]

        guardar_mensaje(
            cliente_id,
            "web",
            "assistant",
            session["historial"][0]["content"]
        )


    if "modo_agendar" not in session:
        session["modo_agendar"] = False

    if "paso" not in session:
        session["paso"] = "inicio"

    if "horas_ofrecidas" not in session:
        session["horas_ofrecidas"] = []

    if "datos_reserva" not in session:

        session["datos_reserva"] = {
            "servicio": None,
            "fecha_hora": None,
            "nombre": None,
            "telefono": None,
            "correo": None,
        }


    if request.method == "POST":

        pregunta = (
            request.form
            .get(
                "pregunta",
                ""
            )
            .strip()
        )

        if pregunta:

            session["historial"].append({
                "role":
                    "user",
                "content":
                    pregunta,
            })

            guardar_mensaje(
                cliente_id,
                "web",
                "user",
                pregunta
            )


            # =================================================
            # AGENDA ACTIVA
            # =================================================

            if session.get(
                "modo_agendar",
                False
            ):

                estado = {

                    "modo_agendar":
                        True,

                    "paso":
                        session.get(
                            "paso",
                            "inicio"
                        ),

                    "horas_ofrecidas":
                        session.get(
                            "horas_ofrecidas",
                            []
                        ),

                    "datos_reserva":
                        session["datos_reserva"],
                }

                respuesta = procesar_agenda(
                    estado,
                    pregunta,
                    cliente_id,
                    "web"
                )

                session["modo_agendar"] = (
                    estado["modo_agendar"]
                )

                session["paso"] = (
                    estado["paso"]
                )

                session["horas_ofrecidas"] = (
                    estado["horas_ofrecidas"]
                )

                session["datos_reserva"] = (
                    estado["datos_reserva"]
                )


            # =================================================
            # INICIAR AGENDA
            # =================================================

            elif es_intencion_agendar(
                pregunta
            ):

                session["modo_agendar"] = True
                session["paso"] = "inicio"

                estado = {

                    "modo_agendar":
                        True,

                    "paso":
                        "inicio",

                    "horas_ofrecidas":
                        [],

                    "datos_reserva":
                        session["datos_reserva"],
                }

                respuesta = procesar_agenda(
                    estado,
                    pregunta,
                    cliente_id,
                    "web"
                )

                session["paso"] = (
                    estado["paso"]
                )

                session["horas_ofrecidas"] = (
                    estado["horas_ofrecidas"]
                )

                session["datos_reserva"] = (
                    estado["datos_reserva"]
                )


            # =================================================
            # SERVICIOS
            # =================================================

            elif pregunta_servicios(
                pregunta
            ):

                respuesta = mostrar_servicios()


            # =================================================
            # OPENAI
            # =================================================

            else:

                respuesta = responder_openai(
                    session["historial"],
                    pregunta
                )


            session["historial"].append({
                "role":
                    "assistant",
                "content":
                    respuesta,
            })

            guardar_mensaje(
                cliente_id,
                "web",
                "assistant",
                respuesta
            )

            session.modified = True


    return render_template_string(
        TEMPLATE,
        historial=session["historial"]
    )


# ============================================================
# WHATSAPP / TWILIO WEBHOOK
# ============================================================

@app.route(
    "/whatsapp/webhook",
    methods=["POST"]
)
@app.route(
    "/webhook/whatsapp",
    methods=["POST"]
)
def whatsapp_webhook():

    try:

        telefono_twilio = (
            request.form
            .get(
                "From",
                ""
            )
            .strip()
        )

        text = (
            request.form
            .get(
                "Body",
                ""
            )
            .strip()
        )

        msg_id = (
            request.form
            .get(
                "MessageSid",
                ""
            )
            .strip()
        )

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono_twilio)
        print("Body:", text)
        print("MessageSid:", msg_id)
        print("=" * 60)

        twiml = MessagingResponse()

        if not telefono_twilio:

            twiml.message(
                "No pude identificar tu número de WhatsApp."
            )

            return (
                str(twiml),
                200,
                {
                    "Content-Type":
                        "application/xml; charset=utf-8"
                }
            )

        if not text:

            twiml.message(
                "Por ahora puedo ayudarte por mensaje de texto 😊."
            )

            return (
                str(twiml),
                200,
                {
                    "Content-Type":
                        "application/xml; charset=utf-8"
                }
            )


        # ====================================================
        # DEDUPLICACIÓN TWILIO
        # ====================================================

        ahora_timestamp = (
            datetime.now().timestamp()
        )

        if msg_id:

            for old_id in list(
                PROCESSED_MSG_IDS.keys()
            ):

                if (
                    ahora_timestamp
                    - PROCESSED_MSG_IDS[old_id]
                    > DEDUP_TTL_SECONDS
                ):

                    del PROCESSED_MSG_IDS[
                        old_id
                    ]

            if msg_id in PROCESSED_MSG_IDS:

                # Twilio puede reintentar webhooks.
                # Devolvemos TwiML vacío para no responder dos veces.
                return (
                    str(twiml),
                    200,
                    {
                        "Content-Type":
                            "application/xml; charset=utf-8"
                    }
                )

            PROCESSED_MSG_IDS[
                msg_id
            ] = ahora_timestamp


        # ====================================================
        # SESIÓN POR NÚMERO
        # ====================================================

        cliente_id = telefono_twilio

        telefono_cliente = (
            normalizar_telefono_twilio(
                telefono_twilio
            )
        )

        estado = get_wa_session(
            cliente_id
        )

        # Twilio ya nos entrega el teléfono del cliente.
        # No necesitamos volver a pedirlo durante la reserva.
        estado[
            "datos_reserva"
        ][
            "telefono"
        ] = telefono_cliente

        estado["historial"].append({
            "role":
                "user",
            "content":
                text,
        })

        guardar_mensaje(
            cliente_id,
            "whatsapp",
            "user",
            text
        )


        # ====================================================
        # PROCESAR CON LA LÓGICA ORIGINAL
        # ====================================================

        print(
            "ESTADO WHATSAPP ANTES DE PROCESAR:",
            {
                "modo_agendar": estado.get("modo_agendar"),
                "paso": estado.get("paso"),
                "servicio": estado.get("datos_reserva", {}).get("servicio"),
                "horas_guardadas": len(estado.get("horas_ofrecidas", [])),
            }
        )

        texto_n = normalizar_texto(text)

        # MENÚ reinicia el flujo conversacional sin perder el teléfono.
        if es_comando_menu(text):

            resetear_reserva(estado)
            estado["paso"] = "menu_principal"
            respuesta = mensaje_menu_principal()

        # El cliente puede cerrar la conversación en cualquier momento.
        elif usuario_no_quiere(text):

            resetear_reserva(estado)
            estado["paso"] = "menu_principal"
            respuesta = (
                "No hay problema 😊. Cuando quieras revisar servicios, precios "
                "o reservar una hora con Diego, aquí estaré. ¡Que estés muy bien!"
            )

        # Si ya está armando una reserva, mantenemos el contexto.
        elif estado["modo_agendar"]:

            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        # Consultas directas por servicios/precios.
        elif pregunta_servicios(text):

            estado["paso"] = "servicios_mostrados"
            respuesta = mostrar_servicios()

        # Si nombra un servicio, comenzamos la reserva directamente.
        elif detectar_servicio(text):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"
            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        # Puede pedir una reserva o incluso comenzar diciendo una fecha
        # como "próximo miércoles". La fecha se conserva mientras elegimos servicio.
        elif es_intencion_agendar(text) or texto_menciona_fecha_o_mes(text):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"
            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        # Saludos reciben una apertura natural, sin obligar a usar menú 1/2.
        elif es_saludo_o_menu(text):

            estado["paso"] = "menu_principal"
            respuesta = mensaje_menu_principal()

        # El resto pasa por OpenAI, limitado estrictamente a servicios,
        # precios, horarios y agenda. Si el tema es ajeno, redirige brevemente.
        else:

            respuesta = responder_openai(
                estado["historial"],
                text
            )


        estado["historial"].append({
            "role":
                "assistant",
            "content":
                respuesta,
        })

        guardar_mensaje(
            cliente_id,
            "whatsapp",
            "assistant",
            respuesta
        )

        twiml.message(
            respuesta
        )

        return (
            str(twiml),
            200,
            {
                "Content-Type":
                    "application/xml; charset=utf-8"
            }
        )

    except Exception as e:

        print(
            "TWILIO WHATSAPP ERROR:",
            repr(e)
        )

        import traceback
        print(
            traceback.format_exc()
        )

        twiml = MessagingResponse()

        twiml.message(
            "Disculpa 🙏 Estoy teniendo un problema técnico. "
            "Intenta nuevamente en unos segundos."
        )

        return (
            str(twiml),
            200,
            {
                "Content-Type":
                    "application/xml; charset=utf-8"
            }
        )


# ============================================================
# ADMIN PASSWORD
# ============================================================

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD"
)


def admin_autorizado():

    return session.get(
        "admin_auth",
        False
    )


@app.route(
    "/admin",
    methods=["GET", "POST"]
)
def admin():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if (
            ADMIN_PASSWORD
            and password == ADMIN_PASSWORD
        ):

            session["admin_auth"] = True

            return redirect(
                url_for(
                    "admin_conversaciones"
                )
            )

        return render_template_string(
            ADMIN_LOGIN_TEMPLATE,
            error="Contraseña incorrecta."
        )

    if admin_autorizado():

        return redirect(
            url_for(
                "admin_conversaciones"
            )
        )

    return render_template_string(
        ADMIN_LOGIN_TEMPLATE,
        error=""
    )


# ============================================================
# ADMIN CONVERSACIONES
# ============================================================

@app.route(
    "/admin/conversaciones"
)
def admin_conversaciones():

    if not admin_autorizado():
        return redirect(url_for("admin"))

    return (
        "Panel de conversaciones deshabilitado temporalmente en la versión sin base de datos.",
        200
    )


# ============================================================
# DETALLE CONVERSACIÓN
# ============================================================

@app.route(
    "/admin/conversaciones/<int:conversation_id>"
)
def admin_conversacion_detalle(conversation_id):

    if not admin_autorizado():
        return redirect(url_for("admin"))

    return (
        "Detalle de conversaciones deshabilitado temporalmente en la versión sin base de datos.",
        200
    )


# ============================================================
# LOGOUT ADMIN
# ============================================================

@app.route(
    "/admin/logout"
)
def admin_logout():

    session.pop(
        "admin_auth",
        None
    )

    return redirect(
        url_for("admin")
    )


# ============================================================
# GOOGLE LOGIN
# ============================================================

@app.route(
    "/admin/login"
)
def admin_login():

    try:

        flow = crear_google_flow()

        authorization_url, state = (
            flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent"
            )
        )

        session.permanent = True

        session["google_oauth_state"] = state

        session["google_code_verifier"] = (
            flow.code_verifier
        )

        session.modified = True

        return redirect(
            authorization_url
        )

    except Exception as e:

        print(
            "GOOGLE LOGIN ERROR:",
            repr(e)
        )

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Error iniciando Google OAuth",
            mensaje=str(e)
        )


# ============================================================
# GOOGLE CALLBACK
# ============================================================

@app.route(
    "/callback"
)
def callback():

    error = request.args.get(
        "error"
    )

    if error:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Google rechazó la autorización",
            mensaje=f"Google respondió: {error}"
        )

    code = request.args.get(
        "code"
    )

    if not code:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Falta código OAuth",
            mensaje="Google no entregó el parámetro code."
        )

    try:

        state = session.get(
            "google_oauth_state"
        )

        code_verifier = session.get(
            "google_code_verifier"
        )

        if not state:

            raise Exception(
                "Se perdió la sesión OAuth. "
                "Vuelve a iniciar desde /admin/login."
            )

        if not code_verifier:

            raise Exception(
                "Se perdió el code_verifier OAuth. "
                "Vuelve a iniciar desde /admin/login."
            )

        flow = crear_google_flow()

        flow.state = state
        flow.code_verifier = code_verifier

        authorization_response = request.url

        if not authorization_response.startswith(
            "https://"
        ):

            authorization_response = (
                "https://"
                + request.host
                + request.full_path
            )

        flow.fetch_token(
            authorization_response=
                authorization_response
        )

        credentials = flow.credentials

        if not credentials:

            raise Exception(
                "Google no entregó credenciales."
            )

        refresh_token = (
            credentials.refresh_token
        )

        if not refresh_token:

            return render_template_string(
                ERROR_TEMPLATE,
                titulo="Google no entregó refresh token",
                mensaje=(
                    "Google autorizó la aplicación, "
                    "pero no entregó refresh_token. "
                    "Vuelve a /admin/login."
                )
            )

        session.pop(
            "google_oauth_state",
            None
        )

        session.pop(
            "google_code_verifier",
            None
        )

        session.modified = True

        return render_template_string(
            TOKEN_TEMPLATE,
            token=refresh_token
        )

    except Exception as e:

        print(
            "GOOGLE CALLBACK ERROR:",
            repr(e)
        )

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Error autenticando con Google",
            mensaje=str(e)
        )


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/logout"
)
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# ============================================================
# TEMPLATE CHAT
# ============================================================

TEMPLATE = """
<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<meta name="viewport"
      content="width=device-width, initial-scale=1">

<title>
Asistente Virtual de Estilista Diego
</title>

<style>

* {
    box-sizing: border-box;
}

body {

    margin: 0;

    font-family:
        Arial,
        sans-serif;

    background:
        #f3f4f6;
}

#chat-container {

    position: fixed;

    bottom: 20px;

    right: 20px;

    width: 370px;

    height: 560px;

    background: white;

    border-radius: 18px;

    box-shadow:
        0 10px 40px
        rgba(0,0,0,.18);

    display: flex;

    flex-direction: column;

    overflow: hidden;
}

#chat-header {

    padding: 18px;

    background: #111827;

    color: white;
}

.name {

    font-weight: bold;

    font-size: 16px;
}

.subtitle {

    font-size: 12px;

    opacity: .7;

    margin-top: 4px;
}

#chat-messages {

    flex: 1;

    overflow-y: auto;

    padding: 15px;

    background: #f9fafb;
}

.msg {

    max-width: 84%;

    margin-bottom: 10px;

    padding: 10px 13px;

    border-radius: 16px;

    white-space: pre-wrap;

    line-height: 1.4;

    font-size: 14px;
}

.bot {

    background: #111827;

    color: white;

    margin-right: auto;
}

.user {

    background: #e5e7eb;

    color: #111827;

    margin-left: auto;
}

#chat-input-form {

    display: flex;

    padding: 8px;

    border-top: 1px solid #ddd;
}

#chat-input {

    flex: 1;

    border: none;

    outline: none;

    padding: 12px;
}

button {

    border: none;

    background: #111827;

    color: white;

    padding: 0 18px;

    border-radius: 10px;

    cursor: pointer;
}

</style>

</head>

<body>

<div id="chat-container">

<div id="chat-header">

<div class="name">
 Asistente Virtual de Estilista Diego
</div>

<div class="subtitle">
Lunes a sábado · 10:00 a 18:00
</div>

</div>

<div id="chat-messages">

{% for m in historial %}

<div class="msg
{% if m['role'] == 'user' %}
user
{% else %}
bot
{% endif %}
">

{{ m['content'] | e }}

</div>

{% endfor %}

</div>

<form
id="chat-input-form"
method="POST"
>

<input
id="chat-input"
name="pregunta"
placeholder="Escribe tu mensaje..."
autocomplete="off"
required
>

<button type="submit">
➤
</button>

</form>

</div>

<script>

window.onload = function() {

    const box =
        document.getElementById(
            "chat-messages"
        );

    box.scrollTop =
        box.scrollHeight;

};

</script>

</body>

</html>
"""


# ============================================================
# ADMIN LOGIN TEMPLATE
# ============================================================

ADMIN_LOGIN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Administrador</title>

<style>

body {
    font-family: Arial;
    background: #f3f4f6;
    display:flex;
    align-items:center;
    justify-content:center;
    min-height:100vh;
}

.box {
    background:white;
    padding:35px;
    border-radius:16px;
    width:350px;
    box-shadow:0 10px 30px rgba(0,0,0,.15);
}

input {
    width:100%;
    padding:12px;
    margin:10px 0;
    border:1px solid #ddd;
    border-radius:8px;
}

button {
    width:100%;
    padding:12px;
    border:0;
    border-radius:8px;
    background:#111827;
    color:white;
}

.error {
    color:#b91c1c;
}

</style>

</head>

<body>

<div class="box">

<h2> Conversaciones</h2>

<p>
Panel privado de Estilista Diego
</p>

{% if error %}
<p class="error">
{{ error }}
</p>
{% endif %}

<form method="POST">

<input
type="password"
name="password"
placeholder="Contraseña"
required
>

<button>
Entrar
</button>

</form>

</div>

</body>

</html>
"""


# ============================================================
# ADMIN CONVERSACIONES TEMPLATE
# ============================================================

ADMIN_CONVERSACIONES_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Conversaciones</title>

<style>

body {
    font-family:Arial;
    background:#f3f4f6;
    margin:0;
    padding:30px;
}

.container {
    max-width:1200px;
    margin:auto;
}

.top {
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:25px;
}

.card {
    background:white;
    border-radius:14px;
    padding:18px;
    margin-bottom:12px;
    box-shadow:0 4px 15px rgba(0,0,0,.08);
}

a {
    color:#111827;
    text-decoration:none;
}

.badge {
    display:inline-block;
    padding:5px 9px;
    border-radius:8px;
    background:#e5e7eb;
    font-size:12px;
}

.logout {
    color:#b91c1c;
}

</style>

</head>

<body>

<div class="container">

<div class="top">

<h1>
 Conversaciones
</h1>

<a class="logout"
href="/admin/logout">
Cerrar sesión
</a>

</div>

{% if not conversaciones %}

<div class="card">
No hay conversaciones todavía.
</div>

{% endif %}

{% for c in conversaciones %}

<div class="card">

<h3>

<a href="/admin/conversaciones/{{ c['id'] }}">

{% if c['nombre'] %}
{{ c['nombre'] }}
{% else %}
Cliente {{ c['cliente_id'] }}
{% endif %}

</a>

</h3>

<p>

<span class="badge">
{{ c['canal'] }}
</span>

{% if c['estado'] %}

<span class="badge">
{{ c['estado'] }}
</span>

{% endif %}

</p>

<p>

 {{ c['telefono'] or '-' }}

<br>

 {{ c['correo'] or '-' }}

<br>

 {{ c['servicio'] or '-' }}

</p>

{% if c['fecha_reserva'] %}

<p>
 {{ c['fecha_reserva'] }}
</p>

{% endif %}

<p>
 Actualizado:
{{ c['updated_at'] }}
</p>

</div>

{% endfor %}

</div>

</body>

</html>
"""


# ============================================================
# ADMIN DETALLE TEMPLATE
# ============================================================

ADMIN_DETALLE_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Conversación</title>

<style>

body {
    font-family:Arial;
    background:#f3f4f6;
    margin:0;
    padding:25px;
}

.container {
    max-width:850px;
    margin:auto;
}

.card {
    background:white;
    border-radius:14px;
    padding:20px;
    margin-bottom:20px;
}

.message {
    padding:12px;
    margin:10px 0;
    border-radius:12px;
    white-space:pre-wrap;
}

.user {
    background:#e5e7eb;
    margin-left:60px;
}

.assistant {
    background:#111827;
    color:white;
    margin-right:60px;
}

.small {
    font-size:12px;
    opacity:.7;
}

a {
    color:#111827;
}

</style>

</head>

<body>

<div class="container">

<p>
<a href="/admin/conversaciones">
← Volver a conversaciones
</a>
</p>

<div class="card">

<h2>
 Conversación #{{ conversacion['id'] }}
</h2>

<p>
 <b>{{ conversacion['nombre'] or 'Sin nombre' }}</b>
</p>

<p>
 {{ conversacion['telefono'] or '-' }}
</p>

<p>
 {{ conversacion['correo'] or '-' }}
</p>

<p>
 {{ conversacion['servicio'] or '-' }}
</p>

{% if conversacion['fecha_reserva'] %}

<p>
 {{ conversacion['fecha_reserva'] }}
</p>

{% endif %}

{% if conversacion['meet_url'] %}

<p>

<a href="{{ conversacion['meet_url'] }}"
target="_blank">
Abrir evento de Google Calendar
</a>
</p>

{% endif %}

</div>

<div class="card">

<h2>
Conversación
</h2>

{% for m in mensajes %}

<div class="message
{% if m['role'] == 'user' %}
user
{% else %}
assistant
{% endif %}
">

{{ m['contenido'] }}

<div class="small">
{{ m['created_at'] }}
</div>

</div>

{% endfor %}

</div>

</div>

</body>

</html>
"""


# ============================================================
# GOOGLE TOKEN TEMPLATE
# ============================================================

TOKEN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Google Calendar autorizado</title>

<style>

body {
    font-family:Arial;
    max-width:850px;
    margin:50px auto;
    padding:20px;
    background:#f5f5f5;
}

.box {
    background:white;
    padding:30px;
    border-radius:15px;
}

textarea {
    width:100%;
    height:120px;
    margin-top:15px;
}

.success {
    color:#087f23;
}

</style>

</head>

<body>

<div class="box">

<h1 class="success">
 Google Calendar autorizado
</h1>

<p>
La autorización fue completada correctamente.
</p>

<p>
Copia este valor en Render como:
</p>

<b>
GOOGLE_REFRESH_TOKEN
</b>

<textarea readonly>{{ token }}</textarea>

<h3>
En Render:
</h3>

<ol>

<li>Environment</li>

<li>GOOGLE_REFRESH_TOKEN</li>

<li>Pega el token</li>

<li>Guarda</li>

<li>Espera el deploy</li>

</ol>

<p>
 No compartas este token.
</p>

</div>

</body>

</html>
"""


# ============================================================
# ERROR TEMPLATE
# ============================================================

ERROR_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Error</title>

<style>

body {
    font-family:Arial;
    max-width:800px;
    margin:50px auto;
    padding:20px;
}

.box {
    padding:30px;
    border-radius:15px;
    background:#fff3f3;
    border:1px solid #ffcccc;
}

pre {
    white-space:pre-wrap;
}

</style>

</head>

<body>

<div class="box">

<h1>
 {{ titulo }}
</h1>

<pre>{{ mensaje }}</pre>

<hr>

<a href="/admin/login">
Volver a iniciar autorización con Google
</a>

</div>

</body>

</html>
"""


# ============================================================
# INICIALIZAR BASE DE DATOS
# ============================================================

try:
    init_database()
except Exception as e:
    print(
        "ERROR INIT DATABASE:",
        repr(e)
    )


# ============================================================
# ARRANQUE
# ============================================================

print("APP_VERSION:", APP_VERSION)
print("WHATSAPP: TWILIO + LOGICA COMPLETA DE RESERVAS")

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            5000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=(
            os.getenv("FLASK_ENV")
            == "development"
        )
    )
