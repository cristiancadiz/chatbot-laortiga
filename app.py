import os
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
    raise Exception(
        "Falta SECRET_KEY en las variables de entorno."
    )

app.permanent_session_lifetime = timedelta(days=30)


# ============================================================
# PROXY / HTTPS - RENDER
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
    raise Exception(
        "Falta OPENAI_API_KEY."
    )

client = openai.OpenAI(
    api_key=OPENAI_API_KEY
)


# ============================================================
# ESTILISTA
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
# HORARIO DE ATENCIÓN
# ============================================================

# Lunes = 0
# Martes = 1
# Miércoles = 2
# Jueves = 3
# Viernes = 4
# Sábado = 5
# Domingo = 6

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

# Solamente horas exactas.
# NO existen horarios 10:30, 11:30, etc.
HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_CIERRE + 1
    )
)


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {

    "corte": {
        "nombre": "Corte de cabello",
        "duracion": 45,
        "precio": None,
    },

    "corte_barba": {
        "nombre": "Corte + barba",
        "duracion": 60,
        "precio": None,
    },

    "barba": {
        "nombre": "Arreglo de barba",
        "duracion": 30,
        "precio": None,
    },

    "corte_nino": {
        "nombre": "Corte de niño",
        "duracion": 45,
        "precio": None,
    },

    "perfilado": {
        "nombre": "Perfilado",
        "duracion": 30,
        "precio": None,
    },

    "otro": {
        "nombre": "Otro servicio",
        "duracion": 60,
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
    raise Exception(
        "Falta GOOGLE_CLIENT_ID."
    )

if not GOOGLE_CLIENT_SECRET:
    raise Exception(
        "Falta GOOGLE_CLIENT_SECRET."
    )


SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]


# ============================================================
# CREAR FLOW GOOGLE
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

        print(
            "Error WhatsApp:",
            e
        )

        return None


# ============================================================
# SESIÓN WHATSAPP
# ============================================================

def get_wa_session(
    wa_id
):

    if wa_id not in WA_SESSIONS:

        WA_SESSIONS[wa_id] = {

            "historial": [],

            "modo_agendar": False,

            "datos_reserva": {

                "servicio": None,

                "fecha_hora": None,

                "nombre": None,

                # El teléfono se obtiene directamente
                # desde WhatsApp.
                "telefono": wa_id,
            },
        }

    return WA_SESSIONS[wa_id]


# ============================================================
# GOOGLE CALENDAR CREDENTIALS
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
# CALENDAR SERVICE
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
# FECHAS
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

        print(
            "Error fecha:",
            e
        )

        return None


# ============================================================
# VALIDAR HORARIO DE ATENCIÓN
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

        fecha.weekday()
        in DIAS_ATENCION

        and fecha.minute == 0

        and fecha.second == 0

        and HORA_APERTURA
        <= fecha.hour
        <= HORA_CIERRE
    )


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
        "corte" in texto
        and "barba" in texto
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

    if "corte" in texto:
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
# DISPONIBILIDAD
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion
):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        inicio = inicio.astimezone(
            zona
        )

        # Primero validamos que el inicio
        # esté dentro del horario.
        if not es_hora_atencion(
            inicio
        ):

            return False

        service = (
            obtener_calendar_service()
        )

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
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
            .get(CALENDAR_ID, {})
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
            e
        )

        return None


# ============================================================
# CREAR HORAS DEL DÍA
# ============================================================

def generar_horas_del_dia(
    fecha
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha = fecha.astimezone(
        zona
    )

    horas = []

    if not es_dia_atencion(
        fecha
    ):

        return horas

    for hora in HORAS_DISPONIBLES:

        inicio = fecha.replace(

            hour=hora,

            minute=0,

            second=0,

            microsecond=0,
        )

        horas.append(
            inicio
        )

    return horas


# ============================================================
# PRÓXIMAS HORAS DISPONIBLES
# ============================================================

def buscar_horas_disponibles(
    fecha,
    duracion=60,
    cantidad=5,
    desde_hora=None
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha = fecha.astimezone(
        zona
    )

    ahora = datetime.now(
        zona
    )

    resultados = []

    # Si no se especifica una hora inicial,
    # comenzamos desde las 10:00.
    if desde_hora is None:

        desde_hora = HORA_APERTURA

    # Si el día es domingo,
    # no hay atención.
    if not es_dia_atencion(
        fecha
    ):

        return resultados

    for hora in HORAS_DISPONIBLES:

        if hora < desde_hora:
            continue

        inicio = fecha.replace(

            hour=hora,

            minute=0,

            second=0,

            microsecond=0,
        )

        # No ofrecer horas pasadas.
        if inicio <= ahora:
            continue

        disponible = (
            verificar_disponibilidad(
                inicio,
                duracion
            )
        )

        if disponible:

            resultados.append(
                inicio
            )

            if len(resultados) >= cantidad:

                break

    return resultados


# ============================================================
# BUSCAR PRÓXIMAS HORAS INCLUYENDO DÍAS SIGUIENTES
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
    duracion=60,
    cantidad=5,
    dias_maximos=14
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
            + timedelta(days=offset)
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

        desde_hora = HORA_APERTURA

        # Si estamos revisando hoy,
        # comenzamos desde la próxima hora.
        if fecha.date() == ahora.date():

            if ahora.minute > 0:

                desde_hora = (
                    ahora.hour + 1
                )

            else:

                desde_hora = (
                    ahora.hour
                )

            desde_hora = max(
                desde_hora,
                HORA_APERTURA
            )

        horas = buscar_horas_disponibles(

            fecha,

            duracion,

            cantidad=(
                cantidad
                - len(resultados)
            ),

            desde_hora=desde_hora
        )

        resultados.extend(
            horas
        )

        if len(resultados) >= cantidad:

            break

    return resultados[:cantidad]


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

        duracion = servicio[
            "duracion"
        ]

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
                    "Estilista Diego.\n\n"
                    f"Cliente: {nombre_cliente}\n"
                    f"Teléfono: {telefono_cliente}\n"
                    f"Servicio: {servicio['nombre']}\n"
                    f"Duración: {duracion} minutos"
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
            e
        )

        return {

            "ok":
                False,

            "error":
                str(e)
        }


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


# ============================================================
# FORMATO CORTO DE HORA
# ============================================================

def formato_horas(
    horas
):

    if not horas:

        return ""

    return "\n".join(

        [
            f"• {h.strftime('%H:%M')}"
            for h in horas
        ]
    )


# ============================================================
# INTENCIÓN
# ============================================================

def es_intencion_agendar(
    texto
):

    texto = (
        texto or ""
    ).lower()

    palabras = [

        "agendar",
        "agenda",
        "reservar",
        "reserva",
        "cita",
        "hora",
        "turno",
        "barbero",
        "barbería",
        "barberia",
        "estilista",
        "corte",
        "barba",
    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


# ============================================================
# OPENAI
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""

Eres el Asistente Virtual de Estilista Diego.

Tu objetivo principal es ayudar a los clientes
a reservar horas disponibles en la agenda de Diego.

NO eres LaOrtiga.

Habla en español de Chile.

Sé:
- amable
- cercano
- profesional
- breve

Servicios:

• Corte de cabello
• Corte + barba
• Arreglo de barba
• Corte de niño
• Perfilado
• Otro servicio

HORARIO DE ATENCIÓN:

Lunes a sábado.
Desde las 10:00 hasta las 18:00 horas.

Las horas de inicio de atención son solamente:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00
18:00

NO existen horarios de inicio como 10:30,
11:30, 12:30, etc.

Los domingos NO hay atención.

La agenda utilizada pertenece EXCLUSIVAMENTE
a Diego.

El cliente no necesita Google Calendar.

Nunca pidas al cliente iniciar sesión en Google.

Cuando quiera reservar debes obtener:

1. Servicio
2. Día
3. Hora
4. Nombre
5. Teléfono

No inventes disponibilidad.

La disponibilidad real la verifica el sistema.

Si una hora está ocupada, el sistema ofrecerá
otras horas disponibles.

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

        mensajes += (
            historial[-10:]
            if historial
            else []
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

                max_tokens=300,

                temperature=0.5,
            )
        )

        return (
            completion
            .choices[0]
            .message
            .content
            .strip()
        )

    except Exception as e:

        print(
            "OpenAI error:",
            e
        )

        return (
            "Ups 😅 tuve un problema "
            "técnico. ¿Me puedes repetir?"
        )


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


    # ========================================================
    # 1. SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if servicio:

            datos["servicio"] = (
                servicio
            )

            return (

                "Perfecto ✂️\n\n"
                "¿Qué día y a qué hora "
                "te gustaría venir?\n\n"
                f"Atendemos "
                f"{horario_atencion_texto()}."
            )

        return (

            "¡Claro! ✂️ ¿Qué servicio "
            "quieres reservar?\n\n"

            "• Corte de cabello\n"
            "• Corte + barba\n"
            "• Arreglo de barba\n"
            "• Corte de niño\n"
            "• Perfilado"
        )


    # ========================================================
    # 2. FECHA Y HORA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )

        if not fecha:

            return (

                "No entendí la fecha y hora 😅\n\n"

                "Por ejemplo:\n"
                "\"mañana a las 15:00\"\n\n"

                f"Nuestro horario es "
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

                    obtener_servicio(
                        datos["servicio"]
                    )["duracion"],

                    cantidad=5
                )
            )

            if proximas:

                horas = formato_horas(
                    proximas
                )

                return (

                    "El domingo no tenemos "
                    "atención 😕.\n\n"

                    "Las próximas horas "
                    "disponibles son:\n\n"

                    f"{horas}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                "El domingo no tenemos "
                "atención 😕.\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # VALIDAR HORA EXACTA
        # ====================================================

        if fecha.minute != 0:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    obtener_servicio(
                        datos["servicio"]
                    )["duracion"],

                    cantidad=5,

                    dias_maximos=7
                )
            )

            if proximas:

                horas = formato_horas(
                    proximas
                )

                return (

                    "Las reservas comienzan "
                    "solamente en horas exactas "
                    "🕐.\n\n"

                    "Por ejemplo: 10:00, 11:00, "
                    "12:00, 13:00, etc.\n\n"

                    "Estas son las próximas "
                    "horas disponibles:\n\n"

                    f"{horas}\n\n"

                    "¿Cuál prefieres?"
                )

            return (

                "Las reservas comienzan "
                "solamente en horas exactas 🕐.\n\n"

                f"Nuestro horario es "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # VALIDAR RANGO HORARIO
        # ====================================================

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour > HORA_CIERRE
        ):

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    obtener_servicio(
                        datos["servicio"]
                    )["duracion"],

                    cantidad=5,

                    dias_maximos=7
                )
            )

            if proximas:

                horas = formato_horas(
                    proximas
                )

                return (

                    "Ese horario está fuera "
                    "de nuestro horario de atención 😕.\n\n"

                    f"Atendemos "
                    f"{horario_atencion_texto()}.\n\n"

                    "Próximas horas disponibles:\n\n"

                    f"{horas}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                f"Nuestro horario de atención es "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # VALIDAR QUE NO SEA UNA HORA PASADA
        # ====================================================

        ahora = datetime.now(
            zona
        )

        if fecha <= ahora:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    obtener_servicio(
                        datos["servicio"]
                    )["duracion"],

                    cantidad=5
                )
            )

            if proximas:

                horas = formato_horas(
                    proximas
                )

                return (

                    "Esa hora ya pasó 😕.\n\n"

                    "Te puedo ofrecer estas "
                    "próximas horas:\n\n"

                    f"{horas}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                "Esa hora ya pasó 😕.\n\n"

                "Dime otro día y horario."
            )


        # ====================================================
        # VERIFICAR CALENDAR
        # ====================================================

        servicio = obtener_servicio(
            datos["servicio"]
        )

        disponible = (
            verificar_disponibilidad(

                fecha,

                servicio["duracion"]
            )
        )


        if disponible is None:

            return (

                "No pude consultar la agenda "
                "de Diego en este momento 😕.\n\n"
                "Intenta nuevamente en unos segundos."
            )


        # ====================================================
        # HORA OCUPADA
        # ====================================================

        if not disponible:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    servicio["duracion"],

                    cantidad=5,

                    desde_hora=fecha.hour,

                    dias_maximos=7
                )
            )

            if proximas:

                horas = formato_horas(
                    proximas
                )

                return (

                    "Esa hora ya está ocupada 😕.\n\n"

                    "Te puedo ofrecer estas "
                    "próximas horas disponibles:\n\n"

                    f"{horas}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                "Esa hora está ocupada 😕.\n\n"

                "No encontré disponibilidad "
                "cercana. Prueba con otro día."
            )


        # ====================================================
        # GUARDAR FECHA
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
    # 3. NOMBRE
    # ========================================================

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )

        datos["nombre"] = texto

        # ====================================================
        # WHATSAPP
        # ====================================================

        telefono_actual = datos.get(
            "telefono"
        )

        if telefono_actual:

            # Si ya tenemos el número de WhatsApp,
            # no preguntamos nuevamente.
            inicio = datetime.fromisoformat(
                datos["fecha_hora"]
            )

            servicio = obtener_servicio(
                datos["servicio"]
            )

            disponible = (
                verificar_disponibilidad(

                    inicio,

                    servicio["duracion"]
                )
            )

            if not disponible:

                datos["fecha_hora"] = None

                return (

                    "Justo esa hora se ocupó 😕.\n\n"

                    "Dime otra hora y vuelvo "
                    "a revisar la agenda."
                )

            resultado = crear_evento_diego(

                inicio=inicio,

                servicio_codigo=
                    datos["servicio"],

                nombre_cliente=
                    datos["nombre"],

                telefono_cliente=
                    telefono_actual,
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

            fecha_texto = formato_fecha(
                inicio
            )

            servicio_nombre = servicio[
                "nombre"
            ]

            telefono = telefono_actual

            estado["datos_reserva"] = {

                "servicio":
                    None,

                "fecha_hora":
                    None,

                "nombre":
                    None,

                "telefono":
                    telefono_actual,
            }

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
    # 4. TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto


    # ========================================================
    # 5. CREAR RESERVA
    # ========================================================

    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )

    servicio = obtener_servicio(
        datos["servicio"]
    )


    # ========================================================
    # SEGUNDA VERIFICACIÓN
    # ========================================================

    disponible = (
        verificar_disponibilidad(

            inicio,

            servicio["duracion"]
        )
    )


    if disponible is None:

        return (

            "No pude comprobar nuevamente "
            "la disponibilidad 😕.\n\n"

            "Por favor intenta nuevamente."
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

        inicio=inicio,

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

    servicio_nombre = servicio[
        "nombre"
    ]


    # ========================================================
    # LIMPIAR RESERVA
    # ========================================================

    estado["datos_reserva"] = {

        "servicio":
            None,

        "fecha_hora":
            None,

        "nombre":
            None,

        "telefono":
            None,
    }

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
# HOME
# ============================================================

@app.route("/")
def home():

    return redirect(
        url_for("chat")
    )


# ============================================================
# CHAT
# ============================================================

@app.route(
    "/chat",
    methods=["GET", "POST"]
)
def chat():

    if "historial" not in session:

        session["historial"] = [

            {
                "role":
                    "assistant",

                "content":
                    (
                        "¡Hola! 👋 Soy el "
                        "Asistente Virtual de "
                        "Estilista Diego ✂️\n\n"
                        "¿Quieres reservar una hora?"
                    ),
            }
        ]


    if "modo_agendar" not in session:

        session["modo_agendar"] = False


    if "datos_reserva" not in session:

        session["datos_reserva"] = {

            "servicio":
                None,

            "fecha_hora":
                None,

            "nombre":
                None,

            "telefono":
                None,
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

            session[
                "historial"
            ].append({

                "role":
                    "user",

                "content":
                    pregunta,
            })


            if (

                es_intencion_agendar(
                    pregunta
                )

                or session.get(
                    "modo_agendar"
                )
            ):

                session[
                    "modo_agendar"
                ] = True

                estado = {

                    "modo_agendar":
                        True,

                    "datos_reserva":
                        session[
                            "datos_reserva"
                        ],
                }

                respuesta = (
                    procesar_reserva(

                        estado,

                        pregunta
                    )
                )

                session[
                    "datos_reserva"
                ] = estado[
                    "datos_reserva"
                ]

                session[
                    "modo_agendar"
                ] = estado[
                    "modo_agendar"
                ]

            else:

                respuesta = (
                    responder_openai(

                        session[
                            "historial"
                        ],

                        pregunta
                    )
                )


            session[
                "historial"
            ].append({

                "role":
                    "assistant",

                "content":
                    respuesta,
            })

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

        and token ==
            WHATSAPP_VERIFY_TOKEN
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


        # ====================================================
        # IGNORAR ESTADOS
        # ====================================================

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
        # EVITAR MENSAJES DUPLICADOS
        # ====================================================

        ahora_timestamp = (
            datetime.now().timestamp()
        )

        if msg_id:

            # Limpiar mensajes antiguos.
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

                return "ok", 200


            PROCESSED_MSG_IDS[
                msg_id
            ] = ahora_timestamp


        # ====================================================
        # OBTENER SESIÓN
        # ====================================================

        estado = get_wa_session(
            wa_id
        )


        estado[
            "historial"
        ].append({

            "role":
                "user",

            "content":
                text,
        })


        # ====================================================
        # PROCESAMIENTO
        # ====================================================

        if (

            es_intencion_agendar(
                text
            )

            or estado[
                "modo_agendar"
            ]
        ):

            estado[
                "modo_agendar"
            ] = True

            respuesta = (
                procesar_reserva(

                    estado,

                    text
                )
            )

        else:

            respuesta = (
                responder_openai(

                    estado[
                        "historial"
                    ],

                    text
                )
            )


        # ====================================================
        # HISTORIAL
        # ====================================================

        estado[
            "historial"
        ].append({

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
            "WhatsApp error:",
            e
        )


    return "ok", 200


# ============================================================
# GOOGLE AUTH
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
# CALLBACK GOOGLE
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


        print(
            "========================================"
        )

        print(
            "GOOGLE OAUTH CALLBACK"
        )

        print(
            "STATE SESSION:",
            bool(state)
        )

        print(
            "CODE VERIFIER SESSION:",
            bool(code_verifier)
        )

        print(
            "REQUEST URL:",
            request.url
        )

        print(
            "========================================"
        )


        if not state:

            raise Exception(

                "Se perdió la sesión OAuth "
                "antes del callback. "
                "Vuelve a iniciar desde /admin/login."
            )


        if not code_verifier:

            raise Exception(

                "Se perdió el code_verifier OAuth "
                "antes del callback. "
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
                        "pero no entregó refresh_token.\n\n"
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

            token=
                refresh_token
        )


    except Exception as e:

        print(
            "========================================"
        )

        print(
            "GOOGLE CALLBACK ERROR"
        )

        print(
            repr(e)
        )

        print(
            "========================================"
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
# HTML TOKEN
# ============================================================

TOKEN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Google Calendar autorizado</title>

<style>

body {

    font-family:
        Arial,
        sans-serif;

    max-width:
        850px;

    margin:
        50px auto;

    padding:
        20px;

    background:
        #f5f5f5;
}

.box {

    background:
        white;

    padding:
        30px;

    border-radius:
        15px;

    box-shadow:
        0 5px 25px
        rgba(0,0,0,.10);
}

textarea {

    width:
        100%;

    height:
        120px;

    margin-top:
        15px;

    font-size:
        14px;
}

.success {

    color:
        #087f23;
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

<li>
Ve a Environment.
</li>

<li>
Busca:
<b>GOOGLE_REFRESH_TOKEN</b>
</li>

<li>
Pega el token anterior como valor.
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
# HTML ERROR
# ============================================================

ERROR_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Error Google OAuth</title>

<style>

body {

    font-family:
        Arial,
        sans-serif;

    max-width:
        800px;

    margin:
        50px auto;

    padding:
        20px;
}

.box {

    padding:
        30px;

    border-radius:
        15px;

    background:
        #fff3f3;

    border:
        1px solid #ffcccc;
}

pre {

    white-space:
        pre-wrap;

    word-break:
        break-word;
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
# HTML CHAT
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
    box-sizing:
        border-box;
}

body {

    margin:
        0;

    font-family:
        Arial,
        sans-serif;

    background:
        #f3f4f6;
}


#chat-container {

    position:
        fixed;

    bottom:
        20px;

    right:
        20px;

    width:
        370px;

    height:
        560px;

    background:
        white;

    border-radius:
        18px;

    box-shadow:
        0 10px 40px
        rgba(0,0,0,.18);

    display:
        flex;

    flex-direction:
        column;

    overflow:
        hidden;
}


#chat-header {

    padding:
        18px;

    background:
        #111827;

    color:
        white;
}

.name {

    font-weight:
        bold;

    font-size:
        16px;
}

.subtitle {

    font-size:
        12px;

    opacity:
        .7;

    margin-top:
        4px;
}


#chat-messages {

    flex:
        1;

    overflow-y:
        auto;

    padding:
        15px;

    background:
        #f9fafb;
}


.msg {

    max-width:
        84%;

    margin-bottom:
        10px;

    padding:
        10px 13px;

    border-radius:
        16px;

    white-space:
        pre-wrap;

    line-height:
        1.4;

    font-size:
        14px;
}


.bot {

    background:
        #111827;

    color:
        white;

    margin-right:
        auto;
}


.user {

    background:
        #e5e7eb;

    color:
        #111827;

    margin-left:
        auto;
}


#chat-input-form {

    display:
        flex;

    padding:
        8px;

    border-top:
        1px solid #ddd;
}


#chat-input {

    flex:
        1;

    border:
        none;

    outline:
        none;

    padding:
        12px;
}


button {

    border:
        none;

    background:
        #111827;

    color:
        white;

    padding:
        0 18px;

    border-radius:
        10px;

    cursor:
        pointer;
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
placeholder="Ej: Quiero agendar un corte..."
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

        debug=
            os.getenv(
                "FLASK_ENV"
            ) == "development"
    )
