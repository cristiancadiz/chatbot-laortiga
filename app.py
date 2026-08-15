import os
import re
import json
import time
import requests
import pytz
import dateparser
import openai

from flask import (
    Flask,
    redirect,
    url_for,
    session,
    request,
    render_template_string,
)

from datetime import timedelta, datetime
from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from werkzeug.middleware.proxy_fix import ProxyFix


# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    raise Exception("Falta SECRET_KEY en las variables de entorno.")

app.permanent_session_lifetime = timedelta(days=30)


# ============================================================
# RENDER / HTTPS
# ============================================================

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

if not OPENAI_API_KEY:
    raise Exception("Falta OPENAI_API_KEY.")

client = openai.OpenAI(
    api_key=OPENAI_API_KEY
)


# ============================================================
# NEGOCIO
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

DIAS_ATENCION = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
}

HORA_APERTURA = 10
HORA_CIERRE = 18

# Las reservas comienzan solamente en horas exactas.
HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_CIERRE + 1
    )
)

# ============================================================
# IMPORTANTE:
# TODA RESERVA DURA 1 HORA.
# ============================================================

DURACION_RESERVA = 60


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {

    "corte": {
        "nombre": "Corte de cabello",
        "duracion": DURACION_RESERVA,
        "precio": None,
    },

    "corte_barba": {
        "nombre": "Corte + barba",
        "duracion": DURACION_RESERVA,
        "precio": None,
    },

    "barba": {
        "nombre": "Arreglo de barba",
        "duracion": DURACION_RESERVA,
        "precio": None,
    },

    "corte_nino": {
        "nombre": "Corte de niño",
        "duracion": DURACION_RESERVA,
        "precio": None,
    },

    "perfilado": {
        "nombre": "Perfilado",
        "duracion": DURACION_RESERVA,
        "precio": None,
    },

    "otro": {
        "nombre": "Otro servicio",
        "duracion": DURACION_RESERVA,
        "precio": None,
    },
}


# ============================================================
# GOOGLE OAUTH
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
    "https://chatbot-laortiga-9.onrender.com/callback"
)

if not GOOGLE_CLIENT_ID:
    raise Exception("Falta GOOGLE_CLIENT_ID.")

if not GOOGLE_CLIENT_SECRET:
    raise Exception("Falta GOOGLE_CLIENT_SECRET.")


SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]


# ============================================================
# WHATSAPP
# ============================================================

WHATSAPP_TOKEN = os.getenv(
    "WHATSAPP_TOKEN"
)

WHATSAPP_PHONE_NUMBER_ID = os.getenv(
    "WHATSAPP_PHONE_NUMBER_ID"
)

WHATSAPP_VERIFY_TOKEN = os.getenv(
    "WHATSAPP_VERIFY_TOKEN"
)

WA_SESSIONS = {}

PROCESSED_MSG_IDS = {}

DEDUP_TTL_SECONDS = 120


# ============================================================
# GOOGLE FLOW
# ============================================================

def crear_google_flow():

    flow = Flow.from_client_config(

        {
            "web": {

                "client_id":
                    GOOGLE_CLIENT_ID,

                "client_secret":
                    GOOGLE_CLIENT_SECRET,

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

    return flow


# ============================================================
# WHATSAPP ENVÍO
# ============================================================

def wa_send_text(
    to_number,
    text
):

    if (
        not WHATSAPP_TOKEN
        or not WHATSAPP_PHONE_NUMBER_ID
    ):
        return None

    url = (
        "https://graph.facebook.com/v20.0/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

    headers = {
        "Authorization":
            f"Bearer {WHATSAPP_TOKEN}",

        "Content-Type":
            "application/json",
    }

    payload = {

        "messaging_product":
            "whatsapp",

        "to":
            to_number,

        "type":
            "text",

        "text": {
            "body":
                (text or "")[:3900]
        },
    }

    try:

        return requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20
        )

    except Exception as e:

        print("Error WhatsApp:", e)

        return None


# ============================================================
# SESIÓN WHATSAPP
# ============================================================

def nueva_reserva(
    telefono=None
):

    return {

        "servicio": None,

        "fecha_hora": None,

        "nombre": None,

        "telefono": telefono,
    }


def get_wa_session(
    wa_id
):

    if wa_id not in WA_SESSIONS:

        WA_SESSIONS[wa_id] = {

            "historial": [],

            "modo_agendar": False,

            "datos_reserva":
                nueva_reserva(wa_id),
        }

    return WA_SESSIONS[wa_id]


# ============================================================
# GOOGLE CREDENTIALS
# ============================================================

def obtener_credentials_diego():

    if not GOOGLE_REFRESH_TOKEN:

        raise Exception(
            "Falta GOOGLE_REFRESH_TOKEN."
        )

    return Credentials(

        token=None,

        refresh_token=
            GOOGLE_REFRESH_TOKEN,

        token_uri=
            "https://oauth2.googleapis.com/token",

        client_id=
            GOOGLE_CLIENT_ID,

        client_secret=
            GOOGLE_CLIENT_SECRET,

        scopes=SCOPES,
    )


# ============================================================
# GOOGLE CALENDAR
# ============================================================

def obtener_calendar_service():

    credentials = (
        obtener_credentials_diego()
    )

    return build(

        "calendar",

        "v3",

        credentials=credentials,

        cache_discovery=False,
    )


# ============================================================
# FECHA / HORA
# ============================================================

def parse_fecha_hora(
    texto
):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        ahora = datetime.now(
            zona
        )

        resultado = dateparser.parse(

            texto,

            languages=["es"],

            settings={

                "PREFER_DATES_FROM":
                    "future",

                "RETURN_AS_TIMEZONE_AWARE":
                    True,

                "TIMEZONE":
                    TIMEZONE,

                "TO_TIMEZONE":
                    TIMEZONE,

                "RELATIVE_BASE":
                    ahora,

                "DATE_ORDER":
                    "DMY",
            },
        )

        if not resultado:
            return None

        if resultado.tzinfo is None:

            resultado = zona.localize(
                resultado
            )

        else:

            resultado = resultado.astimezone(
                zona
            )

        return resultado

    except Exception as e:

        print("Error fecha:", e)

        return None


# ============================================================
# EXTRACCIÓN DE HORA
# ============================================================

def extraer_hora(texto):

    if not texto:
        return None

    texto = texto.lower().strip()

    # 15:00 / 15.00
    match = re.search(
        r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b",
        texto
    )

    if match:

        return (
            int(match.group(1)),
            int(match.group(2))
        )

    # 3 pm / 3 p.m. / 3 de la tarde
    match = re.search(
        r"\b(1[0-2]|[1-9])\s*(?:h|hrs?|horas?)?\s*"
        r"(am|pm|a\.m\.|p\.m\.)\b",
        texto
    )

    if match:

        hora = int(match.group(1))

        periodo = (
            match.group(2)
            .replace(".", "")
        )

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

        return hora, 0

    # 3 de la tarde / 3 de la noche
    match = re.search(
        r"\b(1[0-2]|[1-9])\s*"
        r"(?:de\s+la\s+)?"
        r"(mañana|tarde|noche)\b",
        texto
    )

    if match:

        hora = int(match.group(1))

        periodo = match.group(2)

        if periodo in ("tarde", "noche"):
            if hora < 12:
                hora += 12

        return hora, 0

    return None


# ============================================================
# FECHA + HORA INTELIGENTE
# ============================================================

def interpretar_fecha_hora(
    texto
):

    zona = pytz.timezone(
        TIMEZONE
    )

    ahora = datetime.now(
        zona
    )

    resultado = parse_fecha_hora(
        texto
    )

    hora_extraida = extraer_hora(
        texto
    )

    if not resultado:

        # Si solamente escribió una hora,
        # usamos hoy.
        if hora_extraida:

            hora, minuto = hora_extraida

            resultado = ahora.replace(
                hour=hora,
                minute=minuto,
                second=0,
                microsecond=0
            )

            if resultado <= ahora:

                resultado += timedelta(
                    days=1
                )

        else:

            return None

    if hora_extraida:

        hora, minuto = hora_extraida

        resultado = resultado.replace(

            hour=hora,

            minute=minuto,

            second=0,

            microsecond=0
        )

    return resultado.astimezone(
        zona
    )


# ============================================================
# DÍA DE ATENCIÓN
# ============================================================

def es_dia_atencion(
    fecha
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha = fecha.astimezone(
        zona
    )

    return fecha.weekday() in DIAS_ATENCION


# ============================================================
# HORA DE ATENCIÓN
# ============================================================

def es_hora_atencion(
    fecha
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha = fecha.astimezone(
        zona
    )

    return (

        fecha.weekday() in DIAS_ATENCION

        and fecha.minute == 0

        and fecha.second == 0

        and HORA_APERTURA
        <= fecha.hour
        <= HORA_CIERRE
    )


# ============================================================
# TEXTO HORARIO
# ============================================================

def horario_atencion_texto():

    return (
        "lunes a sábado, "
        "de 10:00 a 18:00 hrs"
    )


# ============================================================
# SERVICIO
# ============================================================

def detectar_servicio(
    texto
):

    texto = (
        texto or ""
    ).lower()

    if (
        ("corte" in texto)
        and
        ("barba" in texto)
    ):
        return "corte_barba"

    if (
        "niño" in texto
        or "nino" in texto
    ):
        return "corte_nino"

    if "barba" in texto:
        return "barba"

    if (
        "perfilado" in texto
        or "perfil" in texto
    ):
        return "perfilado"

    if (
        "corte" in texto
        or "cortarme" in texto
        or "cortarme el pelo" in texto
    ):
        return "corte"

    return None


def obtener_servicio(
    codigo
):

    return SERVICIOS.get(
        codigo,
        SERVICIOS["otro"]
    )


# ============================================================
# DISPONIBILIDAD GOOGLE
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion=DURACION_RESERVA
):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        inicio = inicio.astimezone(
            zona
        )

        if not es_hora_atencion(
            inicio
        ):
            return False

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
        )

        # La reserva no puede terminar
        # después de las 19:00.
        if (
            fin.hour > HORA_CIERRE
            and fin.minute > 0
        ):
            return False

        service = (
            obtener_calendar_service()
        )

        resultado = (
            service
            .freebusy()
            .query(
                body={

                    "timeMin":
                        inicio.isoformat(),

                    "timeMax":
                        fin.isoformat(),

                    "items": [
                        {
                            "id":
                                CALENDAR_ID
                        }
                    ],
                }
            )
            .execute()
        )

        calendario = (
            resultado
            .get("calendars", {})
            .get(
                CALENDAR_ID,
                {}
            )
        )

        bloques = (
            calendario.get(
                "busy",
                []
            )
        )

        return len(bloques) == 0

    except Exception as e:

        print(
            "Calendar error:",
            repr(e)
        )

        return None


# ============================================================
# PRÓXIMAS HORAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
    cantidad=5,
    dias_maximos=14,
    desde_hora=None
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha_inicial = (
        fecha_inicial.astimezone(
            zona
        )
    )

    ahora = datetime.now(
        zona
    )

    resultados = []

    for offset in range(
        dias_maximos
    ):

        fecha = (
            fecha_inicial
            + timedelta(
                days=offset
            )
        )

        fecha = fecha.replace(

            hour=0,

            minute=0,

            second=0,

            microsecond=0
        )

        if not es_dia_atencion(
            fecha
        ):
            continue

        hora_inicio = (
            HORA_APERTURA
        )

        if (
            offset == 0
            and fecha.date() == ahora.date()
        ):

            if ahora.minute > 0:

                hora_inicio = (
                    ahora.hour + 1
                )

            else:

                hora_inicio = (
                    ahora.hour
                )

            hora_inicio = max(
                hora_inicio,
                HORA_APERTURA
            )

        if (
            desde_hora is not None
            and offset == 0
        ):

            hora_inicio = max(
                hora_inicio,
                desde_hora
            )

        for hora in HORAS_DISPONIBLES:

            if hora < hora_inicio:
                continue

            inicio = fecha.replace(

                hour=hora,

                minute=0,

                second=0,

                microsecond=0
            )

            if inicio <= ahora:
                continue

            disponible = (
                verificar_disponibilidad(
                    inicio,
                    DURACION_RESERVA
                )
            )

            if disponible:

                resultados.append(
                    inicio
                )

                if len(resultados) >= cantidad:
                    return resultados

    return resultados


# ============================================================
# FORMATO FECHA
# ============================================================

def formato_fecha(
    fecha
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha = fecha.astimezone(
        zona
    )

    dias = [

        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
        "domingo",
    ]

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

        f"{dias[fecha.weekday()]} "
        f"{fecha.day} de "
        f"{meses[fecha.month - 1]} "
        f"a las "
        f"{fecha.strftime('%H:%M')}"
    )


def formato_horas(
    horas
):

    if not horas:
        return ""

    return "\n".join(

        f"• {h.strftime('%H:%M')}"
        for h in horas
    )


# ============================================================
# DETECTAR CANCELACIÓN DE RESERVA
# ============================================================

def quiere_cancelar_reserva(
    texto
):

    texto = (
        texto or ""
    ).lower().strip()

    frases = [

        "cancelar reserva",
        "cancela reserva",
        "cancelar",
        "cancela",
        "olvídalo",
        "olvidalo",
        "mejor después",
        "mejor despues",
        "ya no",
        "no quiero reservar",
        "no quiero agendar",
        "dejémoslo",
        "dejemoslo",
        "salir",
    ]

    return any(
        frase in texto
        for frase in frases
    )


# ============================================================
# DETECTAR INTENCIÓN DE AGENDAR
# ============================================================

def es_intencion_agendar(
    texto
):

    texto = (
        texto or ""
    ).lower()

    frases = [

        "quiero agendar",
        "quiero reservar",
        "quiero una hora",
        "necesito una hora",
        "quiero pedir hora",
        "puedo reservar",
        "puedo agendar",
        "tienes hora",
        "tienes disponibilidad",
        "hay hora",
        "hay disponibilidad",
        "reservar una hora",
        "agendar una hora",
        "sacar hora",
        "pedir hora",
        "quiero cortarme",
        "quiero corte",
        "quiero barba",
        "quiero cortarme el pelo",
        "me quiero cortar",
    ]

    return any(
        frase in texto
        for frase in frases
    )


# ============================================================
# CREAR EVENTO
# ============================================================

def crear_evento_diego(

    inicio,
    servicio_codigo,
    nombre_cliente,
    telefono_cliente
):

    try:

        service = (
            obtener_calendar_service()
        )

        servicio = obtener_servicio(
            servicio_codigo
        )

        # TODAS LAS RESERVAS = 1 HORA
        duracion = DURACION_RESERVA

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
        )

        evento = {

            "summary":
                f"{servicio['nombre']} - "
                f"{nombre_cliente}",

            "description":
                (
                    "Reserva creada por "
                    "Asistente Virtual de "
                    f"Estilista {ESTILISTA_NOMBRE}.\n\n"

                    f"Cliente: {nombre_cliente}\n"
                    f"Teléfono: {telefono_cliente}\n"
                    f"Servicio: {servicio['nombre']}\n"
                    f"Duración: {duracion} minutos\n"
                    "Origen: WhatsApp / Asistente Virtual"
                ),

            "start": {

                "dateTime":
                    inicio.isoformat(),

                "timeZone":
                    TIMEZONE,
            },

            "end": {

                "dateTime":
                    fin.isoformat(),

                "timeZone":
                    TIMEZONE,
            },

            "extendedProperties": {

                "private": {

                    "cliente":
                        nombre_cliente,

                    "telefono":
                        telefono_cliente,

                    "servicio":
                        servicio["nombre"],

                    "origen":
                        "Asistente Virtual Diego",
                }
            },
        }

        resultado = (
            service
            .events()
            .insert(
                calendarId=
                    CALENDAR_ID,

                body=evento
            )
            .execute()
        )

        return {

            "ok":
                True,

            "evento_id":
                resultado.get("id"),

            "link":
                resultado.get("htmlLink"),
        }

    except Exception as e:

        print(
            "Error creando evento:",
            repr(e)
        )

        return {

            "ok":
                False,

            "error":
                str(e)
        }


# ============================================================
# PROCESAR RESERVA
# ============================================================

def procesar_reserva(
    estado,
    texto
):

    datos = estado[
        "datos_reserva"
    ]

    texto = (
        texto or ""
    ).strip()

    if quiere_cancelar_reserva(
        texto
    ):

        estado["modo_agendar"] = False

        telefono = datos.get(
            "telefono"
        )

        estado[
            "datos_reserva"
        ] = nueva_reserva(
            telefono
        )

        return (
            "Claro 😊 Dejamos la reserva "
            "para otro momento.\n\n"
            "Cuando quieras agendar con Diego, "
            "solo dime y te ayudo."
        )


    # ========================================================
    # SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if servicio:

            datos["servicio"] = servicio

            return (

                "Perfecto ✂️\n\n"

                "¿Qué día y a qué hora "
                "te gustaría venir?\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )

        return (

            "Claro 😊 ¿Qué servicio te gustaría "
            "reservar?\n\n"

            "• Corte de cabello\n"
            "• Corte + barba\n"
            "• Arreglo de barba\n"
            "• Corte de niño\n"
            "• Perfilado"
        )


    # ========================================================
    # FECHA / HORA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = interpretar_fecha_hora(
            texto
        )

        if not fecha:

            return (

                "Claro 😊 Dime el día y la hora "
                "que te acomoda.\n\n"

                "Por ejemplo:\n"
                "• mañana a las 10\n"
                "• el lunes a las 3 pm\n"
                "• el 17 a las 15:00\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )

        zona = pytz.timezone(
            TIMEZONE
        )

        fecha = fecha.astimezone(
            zona
        )


        # ====================================================
        # DOMINGO
        # ====================================================

        if not es_dia_atencion(
            fecha
        ):

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            if proximas:

                return (

                    "El domingo no atendemos 😕.\n\n"

                    "Pero puedo ofrecerte estas "
                    "próximas horas:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                "El domingo no tenemos atención 😕.\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # SOLO HORAS EXACTAS
        # ====================================================

        if fecha.minute != 0:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            return (

                "Las reservas comienzan en horas "
                "exactas 🕐.\n\n"

                "Por ejemplo: 10:00, 11:00, "
                "12:00, 13:00, 14:00, 15:00, "
                "16:00, 17:00 o 18:00.\n\n"

                + (
                    "Las próximas disponibles son:\n\n"
                    + formato_horas(proximas)
                    + "\n\n¿Cuál te acomoda?"
                    if proximas
                    else
                    "¿Qué hora exacta te gustaría?"
                )
            )


        # ====================================================
        # FUERA DE HORARIO
        # ====================================================

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour > HORA_CIERRE
        ):

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            if proximas:

                return (

                    "Ese horario está fuera de "
                    "nuestro horario de atención 😕.\n\n"

                    f"Atendemos "
                    f"{horario_atencion_texto()}.\n\n"

                    "Estas son las próximas horas "
                    "disponibles:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (
                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # HORA PASADA
        # ====================================================

        ahora = datetime.now(
            zona
        )

        if fecha <= ahora:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            if proximas:

                return (

                    "Esa hora ya pasó 😕.\n\n"

                    "Te puedo ofrecer estas "
                    "próximas horas:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (
                "Esa hora ya pasó 😕. "
                "Dime otro día y horario."
            )


        # ====================================================
        # CALENDAR
        # ====================================================

        disponible = (
            verificar_disponibilidad(
                fecha,
                DURACION_RESERVA
            )
        )

        if disponible is None:

            return (

                "No pude consultar la agenda "
                "de Diego en este momento 😕.\n\n"

                "Intenta nuevamente en unos segundos."
            )


        # ====================================================
        # OCUPADA
        # ====================================================

        if not disponible:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5,
                    desde_hora=fecha.hour
                )
            )

            if proximas:

                return (

                    "Esa hora ya está ocupada 😕.\n\n"

                    "Pero encontré estas próximas "
                    "horas disponibles:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál prefieres?"
                )

            return (

                "Esa hora está ocupada 😕.\n\n"

                "No encontré otra hora cercana. "
                "¿Quieres probar otro día?"
            )


        # ====================================================
        # GUARDAR
        # ====================================================

        datos["fecha_hora"] = (
            fecha.isoformat()
        )

        return (

            "¡Perfecto! 🙌\n\n"

            f"Hay disponibilidad el "
            f"{formato_fecha(fecha)}.\n\n"

            "¿Me indicas tu nombre?"
        )


    # ========================================================
    # NOMBRE
    # ========================================================

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? 😊"
            )

        datos["nombre"] = texto


        # ====================================================
        # TELÉFONO WHATSAPP AUTOMÁTICO
        # ====================================================

        if datos.get("telefono"):

            inicio = datetime.fromisoformat(
                datos["fecha_hora"]
            )

            disponible = (
                verificar_disponibilidad(
                    inicio,
                    DURACION_RESERVA
                )
            )

            if disponible is None:

                return (
                    "No pude comprobar nuevamente "
                    "la agenda 😕. Intenta nuevamente."
                )

            if not disponible:

                datos["fecha_hora"] = None

                return (

                    "Justo esa hora se ocupó 😕.\n\n"

                    "Dime otra hora y vuelvo "
                    "a revisar la agenda."
                )

            resultado = (
                crear_evento_diego(

                    inicio=
                        inicio,

                    servicio_codigo=
                        datos["servicio"],

                    nombre_cliente=
                        datos["nombre"],

                    telefono_cliente=
                        datos["telefono"],
                )
            )

            if not resultado["ok"]:

                print(
                    resultado["error"]
                )

                return (
                    "No pude completar la reserva "
                    "en este momento 😕."
                )

            nombre = datos["nombre"]

            telefono = datos["telefono"]

            fecha_texto = formato_fecha(
                inicio
            )

            servicio_nombre = (
                obtener_servicio(
                    datos["servicio"]
                )["nombre"]
            )

            estado["datos_reserva"] = (
                nueva_reserva(
                    telefono
                )
            )

            estado["modo_agendar"] = False

            return (

                "✅ ¡Reserva confirmada!\n\n"

                f"✂️ Servicio: {servicio_nombre}\n"
                f"👤 Cliente: {nombre}\n"
                f"📞 Teléfono: {telefono}\n"
                f"📅 {fecha_texto}\n\n"

                f"Tu hora quedó agendada "
                f"directamente en la agenda de "
                f"{ESTILISTA_NOMBRE}.\n\n"

                "¡Te esperamos! 🙌"
            )

        return (
            f"Perfecto, {texto} 👍\n\n"
            "¿Cuál es tu número de teléfono?"
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto


    # ========================================================
    # SEGUNDA VERIFICACIÓN
    # ========================================================

    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )

    disponible = (
        verificar_disponibilidad(
            inicio,
            DURACION_RESERVA
        )
    )

    if disponible is None:

        return (
            "No pude comprobar nuevamente "
            "la disponibilidad 😕."
        )

    if not disponible:

        datos["fecha_hora"] = None

        return (

            "Justo esa hora se ocupó 😕.\n\n"

            "Dime otra hora y vuelvo "
            "a revisar."
        )


    # ========================================================
    # CREAR EVENTO
    # ========================================================

    resultado = crear_evento_diego(

        inicio=
            inicio,

        servicio_codigo=
            datos["servicio"],

        nombre_cliente=
            datos["nombre"],

        telefono_cliente=
            datos["telefono"],
    )

    if not resultado["ok"]:

        print(
            resultado["error"]
        )

        return (
            "No pude completar la reserva "
            "en este momento 😕."
        )


    nombre = datos["nombre"]

    telefono = datos["telefono"]

    fecha_texto = formato_fecha(
        inicio
    )

    servicio_nombre = (
        obtener_servicio(
            datos["servicio"]
        )["nombre"]
    )


    # ========================================================
    # LIMPIAR
    # ========================================================

    estado["datos_reserva"] = (
        nueva_reserva(
            telefono
        )
    )

    estado["modo_agendar"] = False


    return (

        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: {servicio_nombre}\n"
        f"👤 Cliente: {nombre}\n"
        f"📞 Teléfono: {telefono}\n"
        f"📅 {fecha_texto}\n\n"

        f"Tu hora quedó agendada "
        f"directamente en la agenda de "
        f"{ESTILISTA_NOMBRE}.\n\n"

        "¡Te esperamos! 🙌"
    )


# ============================================================
# OPENAI — CONVERSACIÓN NATURAL
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""
Eres el Asistente Virtual de {NEGOCIO_NOMBRE}.

Tu nombre es Asistente Virtual de Estilista {ESTILISTA_NOMBRE}.

Tu objetivo comercial es ayudar a los clientes a
agendar una hora con {ESTILISTA_NOMBRE}, pero NO debes
forzar el agendamiento.

La conversación debe sentirse natural, humana y fluida,
similar a conversar con ChatGPT.

IMPORTANTE:

Al comienzo el cliente puede hablar libremente contigo.

Puede decir:
- hola
- buenos días
- cómo estás
- cómo estás?
- qué haces
- conversar
- hacer una pregunta
- preguntar algo relacionado con cabello
- preguntar algo completamente general

Debes responder naturalmente.

Ejemplos:

Cliente:
"Hola"

Respuesta:
"¡Hola! 👋 Qué gusto saludarte. ¿Cómo estás?"

Cliente:
"¿Cómo estás?"

Respuesta:
"¡Muy bien, gracias por preguntar! 😊 ¿Y tú, cómo estás?"

Cliente:
"Bien"

Respuesta natural:
"¡Qué bueno! 🙌 Me alegra. Cuéntame, ¿en qué te puedo ayudar?"

NO respondas siempre:
"¿Qué te gustaría hacer?"

NO repitas respuestas.

NO digas que tienes un problema técnico simplemente porque
el cliente está conversando.

NO debes iniciar inmediatamente una reserva solo porque
el cliente saludó.

Cuando el cliente manifieste interés en cortarse el pelo,
barba o reservar una hora, puedes conducirlo suavemente
hacia la reserva.

Ejemplo:

Cliente:
"Quiero cortarme el pelo"

Puedes responder:

"¡De una! ✂️ Si quieres, puedo ayudarte a buscar una hora
con Diego. Atendemos de lunes a sábado, de 10:00 a 18:00.
¿Qué día te acomodaría?"

Si el cliente quiere reservar, el sistema de reserva
se encargará de consultar la disponibilidad real.

No inventes horas disponibles.

No afirmes que una hora está disponible si no fue comprobada
por Google Calendar.

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Domingo:
NO hay atención.

Las reservas comienzan en horas exactas:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00
18:00

Cada reserva ocupa 1 hora en Google Calendar.

SERVICIOS:

- Corte de cabello
- Corte + barba
- Arreglo de barba
- Corte de niño
- Perfilado
- Otro servicio

La agenda pertenece exclusivamente a Diego.

El cliente NO necesita Google Calendar.

Nunca pidas al cliente iniciar sesión en Google.

DATOS DE RESERVA:

El sistema obtiene:
- servicio
- fecha
- hora
- nombre
- teléfono

Si estás conversando normalmente, NO pidas esos datos
sin motivo.

ESTILO:

Habla en español de Chile.

Sé:
- amable
- cercano
- natural
- profesional
- breve
- conversacional

Puedes utilizar emojis de forma moderada.

No digas que eres una IA salvo que el cliente pregunte
directamente.

No inventes información sobre Diego.

Si el cliente pregunta algo que no sabes, dilo de forma
natural.

Si el cliente quiere terminar la conversación, despídete
amablemente.

Cuando el cliente diga:
"gracias"
"chao"
"adiós"
"nos vemos"
"eso era"
"que estés bien"

responde naturalmente y no intentes venderle una reserva
nuevamente.

Estilista:
{ESTILISTA_NOMBRE}
"""

        mensajes = [

            {
                "role":
                    "system",

                "content":
                    system_prompt,
            }
        ]

        if historial:

            mensajes.extend(
                historial[-14:]
            )

        mensajes.append({

            "role":
                "user",

            "content":
                pregunta,
        })

        completion = (
            client
            .chat
            .completions
            .create(

                model="gpt-4o-mini",

                messages=mensajes,

                max_tokens=350,

                temperature=0.7,
            )
        )

        respuesta = (
            completion
            .choices[0]
            .message
            .content
        )

        if not respuesta:

            return (
                "¡Claro! 😊 Cuéntame un poquito más."
            )

        return respuesta.strip()

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        # Respuesta de emergencia.
        # IMPORTANTE:
        # Nunca mostrar "problema técnico" para un saludo.
        texto = (
            pregunta or ""
        ).lower().strip()

        if texto in (
            "hola",
            "hola!",
            "hola 👋",
            "buenas",
            "buenos días",
            "buenas tardes",
            "buenas noches",
        ):

            return (
                "¡Hola! 👋 Qué gusto saludarte. "
                "¿Cómo estás?"
            )

        if (
            "cómo estás" in texto
            or "como estas" in texto
        ):

            return (
                "¡Muy bien, gracias por preguntar! 😊 "
                "¿Y tú, cómo estás?"
            )

        return (
            "Claro 😊 Cuéntame, ¿en qué te puedo ayudar?"
        )


# ============================================================
# PROCESAMIENTO INTELIGENTE
# ============================================================

def procesar_mensaje(
    estado,
    texto
):

    texto = (
        texto or ""
    ).strip()

    # --------------------------------------------------------
    # SI YA ESTÁ AGENDANDO
    # --------------------------------------------------------

    if estado.get(
        "modo_agendar",
        False
    ):

        return procesar_reserva(
            estado,
            texto
        )


    # --------------------------------------------------------
    # SI EXPRESA INTENCIÓN DE AGENDAR
    # --------------------------------------------------------

    if es_intencion_agendar(
        texto
    ):

        estado["modo_agendar"] = True

        return procesar_reserva(
            estado,
            texto
        )


    # --------------------------------------------------------
    # CONVERSACIÓN NORMAL
    # --------------------------------------------------------

    return responder_openai(

        estado.get(
            "historial",
            []
        ),

        texto
    )


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

    if "historial" not in session:

        session["historial"] = [

            {
                "role":
                    "assistant",

                "content":
                    (
                        f"¡Hola! 👋 Soy el "
                        f"Asistente Virtual de "
                        f"Estilista {ESTILISTA_NOMBRE} "
                        "✂️\n\n"
                        "Qué gusto tenerte por aquí. "
                        "¿Cómo estás?"
                    ),
            }
        ]

    if "modo_agendar" not in session:

        session["modo_agendar"] = False

    if "datos_reserva" not in session:

        session["datos_reserva"] = (
            nueva_reserva()
        )


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

            historial_anterior = (
                session["historial"][:]
            )

            estado = {

                "modo_agendar":
                    session.get(
                        "modo_agendar",
                        False
                    ),

                "datos_reserva":
                    session.get(
                        "datos_reserva",
                        nueva_reserva()
                    ),

                "historial":
                    historial_anterior,
            }


            respuesta = procesar_mensaje(
                estado,
                pregunta
            )


            session[
                "historial"
            ].append({

                "role":
                    "user",

                "content":
                    pregunta,
            })

            session[
                "historial"
            ].append({

                "role":
                    "assistant",

                "content":
                    respuesta,
            })

            session[
                "modo_agendar"
            ] = estado[
                "modo_agendar"
            ]

            session[
                "datos_reserva"
            ] = estado[
                "datos_reserva"
            ]

            session.modified = True


    return render_template_string(

        TEMPLATE,

        historial=
            session["historial"]
    )


# ============================================================
# WHATSAPP VERIFY
# ============================================================

@app.route(
    "/whatsapp/webhook",
    methods=["GET"]
)
def whatsapp_verify():

    mode = request.args.get(
        "hub.mode"
    )

    token = request.args.get(
        "hub.verify_token"
    )

    challenge = request.args.get(
        "hub.challenge"
    )

    if (
        mode == "subscribe"
        and token == WHATSAPP_VERIFY_TOKEN
    ):

        return challenge, 200

    return "Forbidden", 403


# ============================================================
# WHATSAPP WEBHOOK
# ============================================================

@app.route(
    "/whatsapp/webhook",
    methods=["POST"]
)
def whatsapp_webhook():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    try:

        entry = (
            data.get("entry")
            or []
        )[0]

        changes = (
            entry.get("changes")
            or []
        )[0]

        value = (
            changes.get("value")
            or {}
        )


        if value.get(
            "statuses"
        ):

            return "ok", 200


        messages = (
            value.get(
                "messages"
            )
            or []
        )

        if not messages:

            return "ok", 200


        msg = messages[0]

        msg_id = msg.get(
            "id"
        )

        wa_id = msg.get(
            "from"
        )

        text = (

            msg.get("text")
            or {}
        ).get(
            "body",
            ""
        ).strip()


        if not wa_id or not text:

            return "ok", 200


        # ====================================================
        # DEDUP
        # ====================================================

        ahora_timestamp = (
            time.time()
        )

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


        if msg_id:

            if (
                msg_id
                in PROCESSED_MSG_IDS
            ):

                return "ok", 200

            PROCESSED_MSG_IDS[
                msg_id
            ] = ahora_timestamp


        # ====================================================
        # SESIÓN
        # ====================================================

        estado = get_wa_session(
            wa_id
        )


        historial_anterior = (
            estado["historial"][:]
        )


        estado["historial"].append({

            "role":
                "user",

            "content":
                text,
        })


        # ====================================================
        # PROCESAR
        # ====================================================

        estado_procesamiento = {

            "modo_agendar":
                estado[
                    "modo_agendar"
                ],

            "datos_reserva":
                estado[
                    "datos_reserva"
                ],

            "historial":
                historial_anterior,
        }


        respuesta = procesar_mensaje(

            estado_procesamiento,

            text
        )


        estado[
            "modo_agendar"
        ] = estado_procesamiento[
            "modo_agendar"
        ]

        estado[
            "datos_reserva"
        ] = estado_procesamiento[
            "datos_reserva"
        ]


        estado["historial"].append({

            "role":
                "assistant",

            "content":
                respuesta,
        })


        # ====================================================
        # ENVIAR
        # ====================================================

        wa_send_text(
            wa_id,
            respuesta
        )


    except Exception as e:

        print(
            "WHATSAPP ERROR:",
            repr(e)
        )


    return "ok", 200


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

        session[
            "google_oauth_state"
        ] = state

        session[
            "google_code_verifier"
        ] = flow.code_verifier

        session.modified = True

        print(
            "========================================"
        )

        print(
            "GOOGLE OAUTH INICIADO"
        )

        print(
            "STATE:",
            state
        )

        print(
            "CODE VERIFIER:",
            "GUARDADO"
            if flow.code_verifier
            else "NO GENERADO"
        )

        print(
            "REDIRECT URI:",
            GOOGLE_REDIRECT_URI
        )

        print(
            "========================================"
        )

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

            titulo=
                "Error iniciando Google OAuth",

            mensaje=
                str(e)
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

            titulo=
                "Google rechazó la autorización",

            mensaje=
                f"Google respondió: {error}"
        )


    code = request.args.get(
        "code"
    )

    if not code:

        return render_template_string(

            ERROR_TEMPLATE,

            titulo=
                "Falta código OAuth",

            mensaje=
                "Google no entregó el parámetro code."
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


        authorization_response = (
            request.url
        )


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

                titulo=
                    "Google no entregó refresh token",

                mensaje=
                    (
                        "Google autorizó la aplicación, "
                        "pero no entregó refresh_token."
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

            token=
                refresh_token
        )


    except Exception as e:

        print(
            "GOOGLE CALLBACK ERROR:",
            repr(e)
        )

        return render_template_string(

            ERROR_TEMPLATE,

            titulo=
                "Error autenticando con Google",

            mensaje=
                str(e)
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
# TOKEN HTML
# ============================================================

TOKEN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Google Calendar autorizado</title>

<style>

body {

    font-family: Arial, sans-serif;

    max-width: 850px;

    margin: 50px auto;

    padding: 20px;

    background: #f5f5f5;
}

.box {

    background: white;

    padding: 30px;

    border-radius: 15px;

    box-shadow:
        0 5px 25px rgba(0,0,0,.10);
}

textarea {

    width: 100%;

    height: 120px;

    margin-top: 15px;

    font-size: 14px;
}

.success {

    color: #087f23;
}

</style>

</head>

<body>

<div class="box">

<h1 class="success">
✅ Google Calendar autorizado
</h1>

<p>
La autorización fue completada correctamente.
</p>

<p>
Este es el <b>GOOGLE_REFRESH_TOKEN</b>:
</p>

<textarea readonly>{{ token }}</textarea>

<h3>Ahora haz esto en Render:</h3>

<ol>

<li>Ve a Environment.</li>

<li>
Busca:
<b>GOOGLE_REFRESH_TOKEN</b>
</li>

<li>
Pega el token como valor.
</li>

<li>
Guarda los cambios.
</li>

<li>
Espera el nuevo deploy.
</li>

</ol>

<p>
⚠️ No compartas este token.
</p>

</div>

</body>

</html>

"""


# ============================================================
# ERROR HTML
# ============================================================

ERROR_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Error Google OAuth</title>

<style>

body {

    font-family: Arial, sans-serif;

    max-width: 800px;

    margin: 50px auto;

    padding: 20px;
}

.box {

    padding: 30px;

    border-radius: 15px;

    background: #fff3f3;

    border: 1px solid #ffcccc;
}

pre {

    white-space: pre-wrap;

    word-break: break-word;
}

</style>

</head>

<body>

<div class="box">

<h1>
❌ {{ titulo }}
</h1>

<pre>{{ mensaje }}</pre>

<hr>

<p>

<a href="/admin/login">
Volver a iniciar autorización con Google
</a>

</p>

</div>

</body>

</html>

"""


# ============================================================
# CHAT HTML
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

    font-family: Arial, sans-serif;

    background: #f3f4f6;
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
        0 10px 40px rgba(0,0,0,.18);

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
✂️ Asistente Virtual de Estilista Diego
</div>

<div class="subtitle">
Lunes a sábado · 10:00 a 18:00 · Reservas de 1 hora
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
placeholder="Escribe un mensaje..."
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
# ARRANQUE
# ============================================================

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
            os.getenv(
                "FLASK_ENV"
            )
            == "development"
        )
    )
