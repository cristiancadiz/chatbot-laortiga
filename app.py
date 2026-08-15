import os
import re
import requests
import pytz
import dateparser
import openai

from datetime import datetime, timedelta

from flask import (
    Flask,
    redirect,
    url_for,
    session,
    request,
    render_template_string,
)

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

# IMPORTANTE:
# TODAS LAS RESERVAS DURAN EXACTAMENTE 1 HORA.
DURACION_RESERVA = 60

# Horas válidas de comienzo.
HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_CIERRE
    )
)

# 10,11,12,13,14,15,16,17
#
# 18:00 no se permite como inicio porque
# una reserva de 1 hora terminaría a las 19:00.


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {

    "corte": {
        "nombre": "Corte de cabello",
    },

    "corte_barba": {
        "nombre": "Corte + barba",
    },

    "barba": {
        "nombre": "Arreglo de barba",
    },

    "corte_nino": {
        "nombre": "Corte de niño",
    },

    "perfilado": {
        "nombre": "Perfilado",
    },

    "otro": {
        "nombre": "Otro servicio",
    },
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
# GOOGLE FLOW
# ============================================================

def crear_google_flow():

    return Flow.from_client_config(

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


def obtener_calendar_service():

    credentials = obtener_credentials_diego()

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False,
    )


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
        print(
            "WhatsApp no está configurado."
        )
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

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20
        )

        print(
            "WhatsApp status:",
            response.status_code
        )

        return response

    except Exception as e:

        print(
            "Error WhatsApp:",
            repr(e)
        )

        return None


# ============================================================
# SESIONES WHATSAPP
# ============================================================

def nueva_reserva(
    telefono=None
):

    return {

        "servicio":
            None,

        "fecha_hora":
            None,

        "nombre":
            None,

        "telefono":
            telefono,
    }


def get_wa_session(
    wa_id
):

    if wa_id not in WA_SESSIONS:

        WA_SESSIONS[wa_id] = {

            "historial": [],

            "modo_agendar": False,

            "datos_reserva":
                nueva_reserva(
                    wa_id
                ),
        }

    return WA_SESSIONS[wa_id]


# ============================================================
# FECHA / HORA
# ============================================================

def parse_fecha_hora(
    texto
):

    """
    Interpreta expresiones como:

    mañana a las 15
    mañana a las 3 pm
    lunes a las 10
    el 17 a las 3
    17 de agosto a las 15
    este sábado a las 11
    próximo martes a las 4
    """

    if not texto:
        return None

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        ahora = datetime.now(
            zona
        )

        texto_limpio = (
            texto
            .strip()
            .lower()
        )

        # ----------------------------------------------------
        # Normalizar expresiones comunes
        # ----------------------------------------------------

        texto_limpio = (
            texto_limpio
            .replace("hrs", "")
            .replace("hr", "")
            .replace("h", "")
        )

        # ----------------------------------------------------
        # Caso:
        # "el 17 a las 3"
        # "17 a las 3"
        # ----------------------------------------------------

        patron_dia = re.search(
            r"\b(?:el\s+)?(\d{1,2})"
            r"(?:\s+de\s+([a-záéíóú]+))?"
            r"\s+(?:a\s+las|a\s+la|a)\s+"
            r"(\d{1,2})(?::(\d{2}))?"
            r"\s*(am|pm|de\s+la\s+mañana|"
            r"de\s+la\s+tarde|de\s+la\s+noche)?",
            texto_limpio
        )

        if patron_dia:

            dia = int(
                patron_dia.group(1)
            )

            mes_texto = (
                patron_dia.group(2)
            )

            hora = int(
                patron_dia.group(3)
            )

            minuto = (
                int(
                    patron_dia.group(4)
                )
                if patron_dia.group(4)
                else 0
            )

            periodo = (
                patron_dia.group(5)
                or ""
            )

            meses = {

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

            mes = (
                meses.get(
                    mes_texto
                )
                if mes_texto
                else ahora.month
            )

            # ------------------------------------------------
            # AM / PM
            # ------------------------------------------------

            if "pm" in periodo:

                if hora < 12:
                    hora += 12

            elif "tarde" in periodo:

                if hora < 12:
                    hora += 12

            elif "noche" in periodo:

                if hora < 12:
                    hora += 12

            elif "mañana" in periodo:

                if hora == 12:
                    hora = 0

            # ------------------------------------------------
            # Determinar año
            # ------------------------------------------------

            año = ahora.year

            try:

                candidato = zona.localize(
                    datetime(
                        año,
                        mes,
                        dia,
                        hora,
                        minuto
                    )
                )

            except ValueError:

                return None

            # Si la fecha ya pasó, usar el próximo año
            # cuando no se especificó mes.
            if (
                mes_texto is None
                and candidato <= ahora
            ):

                try:

                    candidato = zona.localize(
                        datetime(
                            año + 1,
                            mes,
                            dia,
                            hora,
                            minuto
                        )
                    )

                except ValueError:

                    return None

            return candidato

        # ----------------------------------------------------
        # dateparser general
        # ----------------------------------------------------

        resultado = dateparser.parse(

            texto_limpio,

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
            "Error parseando fecha:",
            repr(e)
        )

        return None


# ============================================================
# HORARIO
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

    return (
        fecha.weekday()
        in DIAS_ATENCION
    )


def es_hora_inicio_valida(
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

        and fecha.hour in HORAS_DISPONIBLES
    )


def horario_atencion_texto():

    return (
        "lunes a sábado, "
        "de 10:00 a 18:00 hrs"
    )


# ============================================================
# SERVICIOS
# ============================================================

def detectar_servicio(
    texto
):

    texto = (
        texto or ""
    ).lower()

    if (
        (
            "corte" in texto
            or "cortar" in texto
        )
        and "barba" in texto
    ):
        return "corte_barba"

    if (
        "niño" in texto
        or "nino" in texto
        or "niña" in texto
        or "nina" in texto
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
        or "cortar" in texto
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
# INTENCIONES
# ============================================================

def es_cancelacion(
    texto
):

    texto = (
        texto or ""
    ).lower()
    texto = texto.strip()

    frases = [

        "cancelar",

        "cancela",

        "cancelémoslo",

        "cancelalo",

        "no quiero",

        "ya no quiero",

        "no reservar",

        "no quiero reservar",

        "salir",

        "terminar",

        "olvídalo",

        "olvidalo",

        "déjalo",

        "dejalo",

        "eso es todo",

        "no gracias",
    ]

    return any(
        frase in texto
        for frase in frases
    )


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

        "reservación",

        "reservacion",

        "cita",

        "hora",

        "turno",

        "quiero cortar",

        "quiero un corte",

        "cortar",

        "barbero",

        "barbería",

        "barberia",

        "estilista",
    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


# ============================================================
# DISPONIBILIDAD GOOGLE
# ============================================================

def verificar_disponibilidad(
    inicio
):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        inicio = inicio.astimezone(
            zona
        )

        # ----------------------------------------------------
        # Validar horario
        # ----------------------------------------------------

        if not es_hora_inicio_valida(
            inicio
        ):

            return False

        fin = (
            inicio
            + timedelta(
                minutes=DURACION_RESERVA
            )
        )

        # La reserva nunca puede pasar
        # de las 18:00.
        if fin.hour > HORA_CIERRE:

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
            "Calendar availability error:",
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
    incluir_hora_solicitada=False
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha_inicial = (
        fecha_inicial
        .astimezone(zona)
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

        for hora in HORAS_DISPONIBLES:

            inicio = fecha.replace(
                hour=hora
            )

            if inicio <= ahora:
                continue

            if (
                offset == 0
                and not incluir_hora_solicitada
                and fecha.date()
                == fecha_inicial.date()
            ):
                if (
                    inicio
                    <= fecha_inicial
                ):
                    continue

            disponible = (
                verificar_disponibilidad(
                    inicio
                )
            )

            if disponible is True:

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
        [
            (
                f"• {formato_fecha(hora)}"
            )
            for hora in horas
        ]
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

        # SIEMPRE 1 HORA
        fin = (
            inicio
            + timedelta(
                minutes=DURACION_RESERVA
            )
        )

        evento = {

            "summary":
                (
                    f"{servicio['nombre']} - "
                    f"{nombre_cliente}"
                ),

            "description":
                (
                    "Reserva creada por "
                    "Asistente Virtual de "
                    "Estilista Diego.\n\n"

                    f"Cliente: {nombre_cliente}\n"

                    f"Teléfono: "
                    f"{telefono_cliente}\n"

                    f"Servicio: "
                    f"{servicio['nombre']}\n"

                    f"Duración: "
                    f"{DURACION_RESERVA} minutos"
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

                    "duracion":
                        str(
                            DURACION_RESERVA
                        ),

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
# OPENAI CONVERSACIONAL
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""
Eres el Asistente Virtual de {NEGOCIO_NOMBRE}.

Tu nombre es Asistente Virtual de Estilista Diego.

Hablas español de Chile.

Tu personalidad:

- amable
- cercano
- natural
- cordial
- profesional
- conversacional
- breve
- humano

IMPORTANTE:

Puedes conversar normalmente con el cliente.

Por ejemplo:

Cliente:
"Hola"

Puedes responder:
"¡Hola! 👋 Qué gusto saludarte. ¿Cómo estás?"

Cliente:
"¿Cómo estás?"

Puedes responder:
"¡Muy bien, gracias! 😊 Espero que tú también estés bien. ¿En qué te puedo ayudar?"

Cliente:
"Bien y tú?"

Puedes responder:
"¡Me alegro! 😊 Yo también estoy muy bien, gracias. ¿Quieres que te ayude a reservar una hora con Diego?"

No respondas siempre:
"Cuéntame qué necesitas."

Debes responder según el contexto real de la conversación.

Puedes hablar sobre:

- cómo está el cliente
- saludos
- despedidas
- agradecimientos
- dudas sobre los servicios
- horarios
- disponibilidad
- reservas

Tu objetivo comercial es ayudar al cliente a llegar
a una reserva cuando manifieste interés.

ESTILISTA:

{ESTILISTA_NOMBRE}

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Domingo cerrado.

IMPORTANTE SOBRE LAS RESERVAS:

Todas las reservas duran exactamente 1 hora.

Los horarios de inicio válidos son:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00

No existen reservas de:

10:30
11:30
12:30
13:30
14:30
15:30
16:30
17:30

18:00 tampoco se utiliza como inicio,
porque una reserva de una hora terminaría a las 19:00.

La agenda pertenece solamente a Diego.

El cliente NO necesita Google Calendar.

Nunca le pidas iniciar sesión con Google.

Cuando el cliente quiera reservar debes obtener:

1. Servicio
2. Fecha
3. Hora
4. Nombre
5. Teléfono

La disponibilidad real siempre la verifica el sistema.

NO inventes horarios disponibles.

Si una hora está ocupada, el sistema ofrecerá
otras horas.

Si el cliente pregunta algo que no tiene relación
con una reserva, puedes responder normalmente
y mantener una conversación natural.

Si el cliente quiere terminar la conversación,
respeta su decisión.

No fuerces una reserva.
"""

        mensajes = [
            {
                "role":
                    "system",
                "content":
                    system_prompt
            }
        ]

        mensajes.extend(
            historial[-12:]
        )

        mensajes.append(
            {
                "role":
                    "user",
                "content":
                    pregunta
            }
        )

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
                "¡Claro! 😊 "
                "¿En qué te puedo ayudar?"
            )

        return respuesta.strip()

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        # IMPORTANTE:
        # No mostramos el error técnico al cliente.
        return (
            "¡Claro! 😊 "
            "Estoy aquí para ayudarte. "
            "¿Qué te gustaría hacer?"
        )


# ============================================================
# CANCELAR RESERVA
# ============================================================

def cancelar_reserva(
    estado
):

    telefono = (
        estado["datos_reserva"]
        .get("telefono")
    )

    estado["datos_reserva"] = (
        nueva_reserva(telefono)
    )

    estado["modo_agendar"] = False

    return (
        "Perfecto 😊 No hay problema. "
        "Dejamos la reserva cancelada.\n\n"
        "Si después quieres agendar una hora "
        "con Diego, aquí estaré. ✂️"
    )


# ============================================================
# PROCESAR RESERVA
# ============================================================

def procesar_reserva(
    estado,
    texto
):

    texto = (
        texto or ""
    ).strip()

    datos = (
        estado["datos_reserva"]
    )

    # ========================================================
    # CANCELACIÓN
    # ========================================================

    if es_cancelacion(texto):

        return cancelar_reserva(
            estado
        )


    # ========================================================
    # SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if servicio:

            datos["servicio"] = (
                servicio
            )

            # Si el mismo mensaje también
            # trae fecha/hora, intentamos procesarla.
            fecha = parse_fecha_hora(
                texto
            )

            if fecha:

                datos["fecha_hora"] = (
                    fecha.isoformat()
                )

                return (
                    "Perfecto 🙌\n\n"
                    "¿Me indicas tu nombre?"
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
            "te gustaría reservar?\n\n"
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

        fecha = parse_fecha_hora(
            texto
        )

        if not fecha:

            return (
                "Claro 😊 ¿Qué día y a qué hora "
                "te gustaría venir?\n\n"
                "Por ejemplo:\n"
                "• mañana a las 15:00\n"
                "• el lunes a las 10\n"
                "• el 17 a las 3 de la tarde\n\n"
                f"Atendemos "
                f"{horario_atencion_texto()}."
            )

        zona = pytz.timezone(
            TIMEZONE
        )

        fecha = fecha.astimezone(
            zona
        )

        ahora = datetime.now(
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
                    "El domingo no tenemos "
                    "atención 😕.\n\n"
                    "Pero puedo ofrecerte estas "
                    "próximas horas:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál te acomoda?"
                )

            return (
                "El domingo no tenemos "
                "atención 😕.\n\n"
                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # HORA NO EXACTA
        # ====================================================

        if fecha.minute != 0:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            return (
                "Las reservas comienzan "
                "solamente en horas exactas 🕐.\n\n"
                "Por ejemplo: 10:00, 11:00, "
                "12:00, 13:00, etc.\n\n"
                "Estas son algunas próximas "
                "horas disponibles:\n\n"
                f"{formato_horas(proximas)}\n\n"
                "¿Cuál prefieres?"
            )


        # ====================================================
        # HORA FUERA DE HORARIO
        # ====================================================

        if (
            fecha.hour
            not in HORAS_DISPONIBLES
        ):

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            if proximas:

                return (
                    "Ese horario está fuera "
                    "de nuestro horario de atención 😕.\n\n"
                    f"Atendemos "
                    f"{horario_atencion_texto()}.\n\n"
                    "Puedo ofrecerte:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál te acomoda?"
                )

            return (
                f"Nuestro horario es "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # HORA PASADA
        # ====================================================

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
                "Esa hora ya pasó 😕.\n\n"
                "Dime otro día y horario."
            )


        # ====================================================
        # DISPONIBILIDAD
        # ====================================================

        disponible = (
            verificar_disponibilidad(
                fecha
            )
        )

        if disponible is None:

            return (
                "No pude consultar la agenda "
                "de Diego en este momento 😕.\n\n"
                "Intenta nuevamente en unos segundos."
            )


        # ====================================================
        # OCUPADO
        # ====================================================

        if not disponible:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            if proximas:

                return (
                    "Esa hora ya está ocupada 😕.\n\n"
                    "Te puedo ofrecer estas "
                    "próximas horas disponibles:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál te acomoda?"
                )

            return (
                "Esa hora está ocupada 😕.\n\n"
                "No encontré disponibilidad cercana. "
                "¿Quieres probar otro día?"
            )


        # ====================================================
        # DISPONIBLE
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

        # Evitar tomar frases demasiado cortas
        # como nombres.
        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )

        datos["nombre"] = texto

        # ====================================================
        # SI TENEMOS TELÉFONO DE WHATSAPP
        # ====================================================

        telefono_actual = (
            datos.get("telefono")
        )

        if telefono_actual:

            return finalizar_reserva(
                estado
            )

        return (
            f"Perfecto, {texto} 👍\n\n"
            "¿Me compartes tu número de teléfono?"
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto

        return finalizar_reserva(
            estado
        )


    # ========================================================
    # SEGURIDAD
    # ========================================================

    return finalizar_reserva(
        estado
    )


# ============================================================
# FINALIZAR RESERVA
# ============================================================

def finalizar_reserva(
    estado
):

    datos = (
        estado["datos_reserva"]
    )

    if not datos["servicio"]:
        return (
            "¿Qué servicio quieres reservar? ✂️"
        )

    if not datos["fecha_hora"]:
        return (
            "¿Qué día y a qué hora "
            "te gustaría venir?"
        )

    if not datos["nombre"]:
        return (
            "¿Me indicas tu nombre? 😊"
        )

    if not datos["telefono"]:
        return (
            "¿Me compartes tu número "
            "de teléfono?"
        )


    # ========================================================
    # FECHA
    # ========================================================

    try:

        inicio = datetime.fromisoformat(
            datos["fecha_hora"]
        )

    except Exception:

        datos["fecha_hora"] = None

        return (
            "Necesito confirmar nuevamente "
            "el día y la hora. "
            "¿Cuándo te gustaría venir?"
        )


    # ========================================================
    # SEGUNDA COMPROBACIÓN
    # ========================================================

    disponible = (
        verificar_disponibilidad(
            inicio
        )
    )

    if disponible is None:

        return (
            "No pude comprobar la agenda "
            "en este momento 😕.\n\n"
            "Intenta nuevamente en unos segundos."
        )


    if not disponible:

        datos["fecha_hora"] = None

        proximas = (
            buscar_proximas_horas(
                inicio,
                cantidad=5
            )
        )

        if proximas:

            return (
                "Justo esa hora se ocupó 😕.\n\n"
                "Te puedo ofrecer estas "
                "próximas horas:\n\n"
                f"{formato_horas(proximas)}\n\n"
                "¿Cuál te acomoda?"
            )

        return (
            "Justo esa hora se ocupó 😕.\n\n"
            "Dime otro día y lo revisamos."
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
            "ERROR EVENTO:",
            resultado["error"]
        )

        return (
            "No pude completar la reserva "
            "en este momento 😕.\n\n"
            "Por favor intenta nuevamente."
        )


    # ========================================================
    # DATOS PARA RESPUESTA
    # ========================================================

    servicio = obtener_servicio(
        datos["servicio"]
    )

    nombre = datos["nombre"]

    telefono = datos["telefono"]

    fecha_texto = formato_fecha(
        inicio
    )

    servicio_nombre = (
        servicio["nombre"]
    )


    # ========================================================
    # MANTENER TELÉFONO EN WHATSAPP
    # ========================================================

    telefono_guardar = telefono


    # ========================================================
    # LIMPIAR
    # ========================================================

    estado["datos_reserva"] = (
        nueva_reserva(
            telefono_guardar
        )
    )

    estado["modo_agendar"] = False


    # ========================================================
    # CONFIRMACIÓN
    # ========================================================

    return (
        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: "
        f"{servicio_nombre}\n"

        f"👤 Cliente: "
        f"{nombre}\n"

        f"📞 Teléfono: "
        f"{telefono}\n"

        f"📅 Fecha y hora: "
        f"{fecha_texto}\n"

        f"⏱️ Duración: "
        f"{DURACION_RESERVA} hora\n\n"

        f"Tu hora quedó agendada "
        f"directamente en la agenda de "
        f"{ESTILISTA_NOMBRE}. 🙌\n\n"

        "¡Te esperamos!"
    )


# ============================================================
# PROCESAR MENSAJE GENERAL
# ============================================================

def procesar_mensaje(
    estado,
    texto
):

    texto = (
        texto or ""
    ).strip()

    if not texto:
        return (
            "¡Hola! 😊 ¿En qué te puedo ayudar?"
        )


    # ========================================================
    # CANCELACIÓN
    # ========================================================

    if (
        estado.get("modo_agendar")
        and es_cancelacion(texto)
    ):

        return cancelar_reserva(
            estado
        )


    # ========================================================
    # SI ESTÁ AGENDANDO
    # ========================================================

    if estado.get(
        "modo_agendar"
    ):

        return procesar_reserva(
            estado,
            texto
        )


    # ========================================================
    # DETECTAR INTENCIÓN DE RESERVA
    # ========================================================

    if es_intencion_agendar(
        texto
    ):

        estado["modo_agendar"] = True

        return procesar_reserva(
            estado,
            texto
        )


    # ========================================================
    # CONVERSACIÓN NORMAL
    # ========================================================

    return responder_openai(
        estado["historial"],
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
                        "¡Hola! 👋 Soy el "
                        "Asistente Virtual de "
                        "Estilista Diego ✂️\n\n"
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

            estado = {

                "historial":
                    session["historial"],

                "modo_agendar":
                    session["modo_agendar"],

                "datos_reserva":
                    session["datos_reserva"],
            }


            # ------------------------------------------------
            # AGREGAR MENSAJE USUARIO
            # ------------------------------------------------

            estado["historial"].append(
                {
                    "role":
                        "user",
                    "content":
                        pregunta,
                }
            )


            # ------------------------------------------------
            # PROCESAR
            # ------------------------------------------------

            respuesta = (
                procesar_mensaje(
                    estado,
                    pregunta
                )
            )


            # ------------------------------------------------
            # GUARDAR RESPUESTA
            # ------------------------------------------------

            estado["historial"].append(
                {
                    "role":
                        "assistant",
                    "content":
                        respuesta,
                }
            )


            session["historial"] = (
                estado["historial"][-30:]
            )

            session["modo_agendar"] = (
                estado["modo_agendar"]
            )

            session["datos_reserva"] = (
                estado["datos_reserva"]
            )

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
        )

        if not entry:
            return "ok", 200

        changes = (
            entry[0].get("changes")
            or []
        )

        if not changes:
            return "ok", 200

        value = (
            changes[0]
            .get("value")
            or {}
        )


        # ====================================================
        # IGNORAR STATUS
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


        if not wa_id:
            return "ok", 200


        if not text:

            wa_send_text(
                wa_id,
                "Por ahora puedo atender mensajes de texto 😊."
            )

            return "ok", 200


        # ====================================================
        # DEDUP
        # ====================================================

        ahora_timestamp = (
            datetime.now().timestamp()
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

            if msg_id in PROCESSED_MSG_IDS:

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


        # ====================================================
        # HISTORIAL
        # ====================================================

        estado["historial"].append(
            {
                "role":
                    "user",

                "content":
                    text,
            }
        )


        # ====================================================
        # PROCESAR
        # ====================================================

        respuesta = (
            procesar_mensaje(
                estado,
                text
            )
        )


        # ====================================================
        # HISTORIAL
        # ====================================================

        estado["historial"].append(
            {
                "role":
                    "assistant",

                "content":
                    respuesta,
            }
        )


        # Limitar memoria.
        estado["historial"] = (
            estado["historial"][-30:]
        )


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

                include_granted_scopes=
                    "true",

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
            bool(
                flow.code_verifier
            )
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

        flow.code_verifier = (
            code_verifier
        )


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


        credentials = (
            flow.credentials
        )

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
                mensaje=(
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
        0 5px 25px
        rgba(0,0,0,.10);
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
Este es tu <b>GOOGLE_REFRESH_TOKEN</b>:
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

button:hover {

    opacity: .9;
}

@media (max-width: 500px) {

    #chat-container {

        position: fixed;

        width: 100%;

        height: 100%;

        bottom: 0;

        right: 0;

        border-radius: 0;
    }
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
            ) == "development"
        )
    )
    
