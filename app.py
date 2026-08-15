import os
import re
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
    raise Exception("Falta OPENAI_API_KEY.")

client = openai.OpenAI(
    api_key=OPENAI_API_KEY
)

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-4o-mini"
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

# Las atenciones duran exactamente 1 hora.
DURACION_RESERVA = 60

# Si la atención termina a las 18:00,
# la última hora de inicio posible es 17:00.
HORA_ULTIMO_INICIO = HORA_CIERRE - 1

HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_ULTIMO_INICIO + 1
    )
)


# ============================================================
# SERVICIOS
# ============================================================

# Todas las reservas duran 1 hora.
SERVICIOS = {

    "corte": {
        "nombre": "Corte de cabello",
        "duracion": 60,
        "precio": None,
    },

    "corte_barba": {
        "nombre": "Corte + barba",
        "duracion": 60,
        "precio": None,
    },

    "barba": {
        "nombre": "Arreglo de barba",
        "duracion": 60,
        "precio": None,
    },

    "corte_nino": {
        "nombre": "Corte de niño",
        "duracion": 60,
        "precio": None,
    },

    "perfilado": {
        "nombre": "Perfilado",
        "duracion": 60,
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
    raise Exception("Falta GOOGLE_CLIENT_ID.")

if not GOOGLE_CLIENT_SECRET:
    raise Exception("Falta GOOGLE_CLIENT_SECRET.")


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
        print(
            "WhatsApp no configurado."
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
            "WhatsApp response:",
            response.status_code,
            response.text[:500]
        )

        return response

    except Exception as e:

        print(
            "Error WhatsApp:",
            repr(e)
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

                "telefono": wa_id,
            },
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
# FECHA Y HORA ACTUAL
# ============================================================

def ahora_local():

    zona = pytz.timezone(
        TIMEZONE
    )

    return datetime.now(
        zona
    )


# ============================================================
# NORMALIZAR TEXTO
# ============================================================

def normalizar_texto(
    texto
):

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
    }

    for origen, destino in reemplazos.items():

        texto = texto.replace(
            origen,
            destino
        )

    return texto


# ============================================================
# PARSEAR HORA EXPLÍCITA
# ============================================================

def detectar_hora_explicita(
    texto
):

    texto_normalizado = normalizar_texto(
        texto
    )

    # 15:00
    match = re.search(
        r"\b([01]?\d|2[0-3])\s*:\s*([0-5]\d)\b",
        texto_normalizado
    )

    if match:

        hora = int(
            match.group(1)
        )

        minuto = int(
            match.group(2)
        )

        return hora, minuto


    # "3 pm", "3 p.m.", "3 de la tarde"
    match = re.search(
        r"\b(1[0-2]|0?[1-9])\s*(?:de\s+la\s+)?(am|pm)\b",
        texto_normalizado
    )

    if match:

        hora = int(
            match.group(1)
        )

        periodo = match.group(2)

        if periodo == "pm" and hora != 12:

            hora += 12

        if periodo == "am" and hora == 12:

            hora = 0

        return hora, 0


    # "3 de la tarde"
    match = re.search(
        r"\b(1[0-2]|0?[1-9])\s+de\s+la\s+(manana|tarde|noche)\b",
        texto_normalizado
    )

    if match:

        hora = int(
            match.group(1)
        )

        periodo = match.group(2)

        if periodo in [
            "tarde",
            "noche"
        ]:

            if hora != 12:

                hora += 12

        return hora, 0


    return None


# ============================================================
# PARSEAR FECHA
# ============================================================

def parse_fecha_hora(
    texto
):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        ahora = ahora_local()

        texto_original = (
            texto or ""
        ).strip()

        texto_normalizado = normalizar_texto(
            texto_original
        )


        # ====================================================
        # HORA
        # ====================================================

        hora_detectada = (
            detectar_hora_explicita(
                texto_normalizado
            )
        )


        # ====================================================
        # FECHA
        # ====================================================

        # Primero intentamos dateparser.
        resultado = dateparser.parse(

            texto_original,

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


        # ====================================================
        # CASO "EL 17 A LAS 3"
        # ====================================================

        patron_dia = re.search(
            r"\b(?:el\s+)?(\d{1,2})\b",
            texto_normalizado
        )

        if patron_dia:

            dia = int(
                patron_dia.group(1)
            )

            if 1 <= dia <= 31:

                anio = ahora.year
                mes = ahora.month

                # Si ese día ya pasó este mes,
                # asumimos el próximo mes válido.
                try:

                    candidato = zona.localize(
                        datetime(
                            anio,
                            mes,
                            dia,
                            0,
                            0
                        )
                    )

                    if candidato.date() < ahora.date():

                        if mes == 12:

                            anio += 1
                            mes = 1

                        else:

                            mes += 1

                        candidato = zona.localize(
                            datetime(
                                anio,
                                mes,
                                dia,
                                0,
                                0
                            )
                        )

                    resultado = candidato

                except ValueError:

                    pass


        # ====================================================
        # SI NO ENCONTRAMOS FECHA
        # ====================================================

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


        # ====================================================
        # APLICAR HORA DETECTADA
        # ====================================================

        if hora_detectada:

            hora, minuto = (
                hora_detectada
            )

            resultado = resultado.replace(

                hour=hora,

                minute=minuto,

                second=0,

                microsecond=0,
            )


        return resultado


    except Exception as e:

        print(
            "Error parseando fecha:",
            repr(e)
        )

        return None


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

        fecha.weekday()
        in DIAS_ATENCION

        and fecha.minute == 0

        and fecha.second == 0

        and HORA_APERTURA
        <= fecha.hour
        <= HORA_ULTIMO_INICIO
    )


# ============================================================
# HORARIO TEXTO
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

    texto = normalizar_texto(
        texto
    )

    if (
        "corte" in texto
        and "barba" in texto
    ):
        return "corte_barba"

    if (
        "nino" in texto
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
    duracion=60
):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        inicio = inicio.astimezone(
            zona
        )

        # Inicio válido.
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

        # La reserva no puede terminar después
        # de las 18:00.
        cierre = inicio.replace(

            hour=HORA_CIERRE,

            minute=0,

            second=0,

            microsecond=0
        )

        if fin > cierre:

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
            .get(
                "calendars",
                {}
            )
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
# BUSCAR HORAS DE UN DÍA
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

    ahora = ahora_local()

    resultados = []


    if not es_dia_atencion(
        fecha
    ):

        return resultados


    if desde_hora is None:

        desde_hora = HORA_APERTURA


    for hora in HORAS_DISPONIBLES:

        if hora < desde_hora:

            continue


        inicio = fecha.replace(

            hour=hora,

            minute=0,

            second=0,

            microsecond=0
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
# BUSCAR PRÓXIMAS HORAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
    duracion=60,
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

    ahora = ahora_local()

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


        hora_inicio = HORA_APERTURA


        if (
            fecha.date()
            == ahora.date()
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


        # Si estamos buscando desde una hora
        # específica del mismo día.
        if (
            offset == 0
            and desde_hora is not None
        ):

            hora_inicio = max(
                hora_inicio,
                desde_hora
            )


        horas = buscar_horas_disponibles(

            fecha,

            duracion,

            cantidad=(
                cantidad
                - len(resultados)
            ),

            desde_hora=hora_inicio
        )


        resultados.extend(
            horas
        )


        if len(resultados) >= cantidad:

            break


    return resultados[:cantidad]


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
# FORMATO HORAS
# ============================================================

def formato_horas(
    horas
):

    if not horas:

        return ""

    resultado = []

    for h in horas:

        resultado.append(
            f"• {formato_fecha(h)}"
        )

    return "\n".join(
        resultado
    )


# ============================================================
# INTENCIÓN DE AGENDAR
# ============================================================

def es_intencion_agendar(
    texto
):

    texto = normalizar_texto(
        texto
    )

    palabras = [

        "agendar",
        "agenda",
        "reservar",
        "reserva",
        "cita",
        "hora",
        "turno",

        "quiero cortarme",
        "quiero un corte",
        "quiero cortar",

        "cortar el pelo",
        "corte de pelo",
        "corte de cabello",

        "arreglarme la barba",
        "arreglo de barba",

        "quiero barba",
        "quiero perfilado",
    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


# ============================================================
# DETECTAR SI ESTÁ DANDO NOMBRE
# ============================================================

def parece_nombre(
    texto
):

    texto = (
        texto or ""
    ).strip()

    if not texto:

        return False

    if len(texto) > 80:

        return False

    palabras = texto.split()

    if len(palabras) > 6:

        return False

    prohibidas = [

        "hola",
        "buenas",
        "gracias",
        "si",
        "sí",
        "no",
        "quiero",
        "mañana",
        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
        "domingo",
    ]

    texto_normalizado = normalizar_texto(
        texto
    )

    if texto_normalizado in prohibidas:

        return False

    # Si parece una pregunta,
    # no es nombre.
    if "?" in texto:

        return False

    return True


# ============================================================
# CREAR EVENTO EN GOOGLE CALENDAR
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
                    "Estilista Diego.\n\n"

                    f"Cliente: {nombre_cliente}\n"

                    f"Teléfono: "
                    f"{telefono_cliente}\n"

                    f"Servicio: "
                    f"{servicio['nombre']}\n"

                    f"Duración: "
                    f"{duracion} minutos\n"

                    f"Horario: "
                    f"{inicio.strftime('%H:%M')} - "
                    f"{fin.strftime('%H:%M')}"
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
                        "60",

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
# RESPUESTA CONVERSACIONAL OPENAI
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""
Eres el Asistente Virtual de Estilista Diego.

Tu nombre comercial es:
"{NEGOCIO_NOMBRE}"

El estilista es:
"{ESTILISTA_NOMBRE}"

Hablas español de Chile.

Tu personalidad:

- amable
- natural
- cercana
- profesional
- simpática
- conversacional
- breve
- humana

NO debes comportarte como un robot.

El cliente puede conversar contigo de cualquier manera.

Puede:

- saludarte
- preguntarte cómo estás
- decirte cómo está
- hacer preguntas sobre los servicios
- preguntar por horarios
- preguntar por la atención
- conversar antes de reservar
- hacer preguntas generales relacionadas con Diego
- finalmente decidir reservar

No intentes agendar a la fuerza.

Si el cliente solamente dice:
"Hola"

puedes responder naturalmente:

"¡Hola! 👋 ¿Cómo estás? Soy el asistente virtual de Diego. ¿En qué te puedo ayudar?"

Si el cliente dice:
"Bien y tú?"

puedes responder naturalmente.

Cuando el cliente manifieste intención clara de reservar,
el sistema externo se encargará del proceso de reserva.

SERVICIOS:

- Corte de cabello
- Corte + barba
- Arreglo de barba
- Corte de niño
- Perfilado
- Otro servicio

HORARIO:

Lunes a sábado.

Atención:
10:00 a 18:00.

Cada reserva dura exactamente 1 hora.

Por lo tanto, las horas de INICIO disponibles son:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00

No existe inicio a las 18:00 porque una atención de 1 hora
terminaría a las 19:00.

Domingo:
NO hay atención.

IMPORTANTE:

Nunca inventes disponibilidad.

Nunca digas que una hora está disponible
si el sistema no la ha comprobado en Google Calendar.

Nunca inventes precios.

Si no conoces un precio, indica que debe consultarse
con Diego.

Puedes conversar naturalmente, pero cuando se trate
de disponibilidad de horas, la información real
proviene de Google Calendar.

El cliente NO necesita Google Calendar.

El cliente NO necesita iniciar sesión.

La agenda pertenece exclusivamente a Diego.

Cuando el sistema de reserva ya tenga una fecha y hora
confirmadas, no vuelvas a inventar otra fecha.

Responde de forma natural y breve.

Fecha y hora actual de referencia:
{ahora_local().strftime("%Y-%m-%d %H:%M")}
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
            historial[-12:]
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

                model=
                    OPENAI_MODEL,

                messages=
                    mensajes,

                max_tokens=
                    300,

                temperature=
                    0.7,
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
                "Claro 😊 ¿En qué te puedo ayudar?"
            )


        return respuesta.strip()


    except Exception as e:

        print(
            "========================================"
        )

        print(
            "OPENAI ERROR"
        )

        print(
            repr(e)
        )

        print(
            "MODEL:",
            OPENAI_MODEL
        )

        print(
            "========================================"
        )


        # Respuesta de respaldo.
        # No dejamos que la conversación se rompa.
        texto = normalizar_texto(
            pregunta
        )


        if any(
            x in texto
            for x in [
                "hola",
                "buenas",
                "holaa",
                "hello"
            ]
        ):

            return (
                "¡Hola! 👋 ¿Cómo estás? "
                "Soy el asistente virtual de Diego ✂️ "
                "¿En qué te puedo ayudar?"
            )


        if any(
            x in texto
            for x in [
                "como estas",
                "como estas tu",
                "bien y tu"
            ]
        ):

            return (
                "¡Muy bien, gracias! 😊 "
                "¿Y tú? ¿En qué te puedo ayudar?"
            )


        if any(
            x in texto
            for x in [
                "horario",
                "atienden",
                "atiende",
                "abren"
            ]
        ):

            return (
                "Diego atiende de lunes a sábado, "
                "de 10:00 a 18:00 hrs ✂️."
            )


        return (
            "¡Claro! 😊 Cuéntame, ¿en qué te puedo ayudar?"
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

            datos["servicio"] = servicio

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
    # 2. FECHA Y HORA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )


        if not fecha:

            return (

                "Claro 😊 ¿Qué día y hora "
                "te gustaría?\n\n"

                "Por ejemplo:\n"

                "• mañana a las 15:00\n"
                "• el lunes a las 3\n"
                "• el 17 a las 15:00\n"
                "• viernes a las 4 de la tarde"
            )


        zona = pytz.timezone(
            TIMEZONE
        )

        fecha = fecha.astimezone(
            zona
        )


        # ====================================================
        # VALIDAR DÍA
        # ====================================================

        if not es_dia_atencion(
            fecha
        ):

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    DURACION_RESERVA,

                    cantidad=5
                )
            )


            if proximas:

                return (

                    "Ese día no tenemos atención 😕.\n\n"

                    "Te puedo ofrecer estas próximas "
                    "horas disponibles:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )


            return (

                "Ese día no tenemos atención 😕.\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # VALIDAR MINUTOS
        # ====================================================

        if fecha.minute != 0:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    DURACION_RESERVA,

                    cantidad=5,

                    dias_maximos=7,

                    desde_hora=fecha.hour
                )
            )


            if proximas:

                return (

                    "Las reservas comienzan "
                    "en horas exactas 🕐.\n\n"

                    "Por ejemplo: 10:00, 11:00, "
                    "12:00, 13:00, 14:00, "
                    "15:00, 16:00 o 17:00.\n\n"

                    "Estas son algunas alternativas:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál prefieres?"
                )


            return (

                "Las reservas comienzan "
                "en horas exactas 🕐.\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # VALIDAR HORARIO
        # ====================================================

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour > HORA_ULTIMO_INICIO
        ):

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    DURACION_RESERVA,

                    cantidad=5,

                    dias_maximos=7
                )
            )


            if proximas:

                return (

                    "Ese horario está fuera "
                    "del horario de atención 😕.\n\n"

                    "Las horas de inicio son desde "
                    "las 10:00 hasta las 17:00.\n\n"

                    "Te puedo ofrecer:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )


            return (

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ====================================================
        # VALIDAR HORA PASADA
        # ====================================================

        ahora = ahora_local()


        if fecha <= ahora:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    DURACION_RESERVA,

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
        # CONSULTAR GOOGLE CALENDAR
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
        # HORA OCUPADA
        # ====================================================

        if not disponible:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    DURACION_RESERVA,

                    cantidad=5,

                    dias_maximos=7,

                    desde_hora=fecha.hour
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
                "Prueba con otro día."
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

        if not parece_nombre(
            texto
        ):

            return (
                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )


        datos["nombre"] = texto


        # ====================================================
        # SI VIENE DESDE WHATSAPP
        # ====================================================

        telefono_actual = (
            datos.get(
                "telefono"
            )
        )


        if telefono_actual:

            inicio = datetime.fromisoformat(
                datos["fecha_hora"]
            )


            servicio = obtener_servicio(
                datos["servicio"]
            )


            # Segunda comprobación.
            disponible = (
                verificar_disponibilidad(

                    inicio,

                    DURACION_RESERVA
                )
            )


            if disponible is None:

                return (

                    "No pude comprobar nuevamente "
                    "la agenda 😕.\n\n"

                    "Intenta nuevamente."
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


            # Limpiar para próxima reserva.
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

                f"✂️ Servicio: "
                f"{servicio_nombre}\n"

                f"👤 Cliente: "
                f"{nombre}\n"

                f"📞 Teléfono: "
                f"{telefono}\n"

                f"📅 {fecha_texto}\n"

                f"⏱️ Duración: 1 hora\n\n"

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

            DURACION_RESERVA
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

        f"✂️ Servicio: "
        f"{servicio_nombre}\n"

        f"👤 Cliente: "
        f"{nombre}\n"

        f"📞 Teléfono: "
        f"{telefono}\n"

        f"📅 {fecha_texto}\n"

        "⏱️ Duración: 1 hora\n\n"

        f"Tu hora quedó agendada "
        f"directamente en la agenda de "
        f"{ESTILISTA_NOMBRE}.\n\n"

        "¡Te esperamos! 🙌"
    )


# ============================================================
# SALUDO INICIAL
# ============================================================

SALUDO_INICIAL = (
    "¡Hola! 👋 ¿Cómo estás?\n\n"
    "Soy el Asistente Virtual de "
    "Estilista Diego ✂️\n\n"
    "Estoy aquí para ayudarte con lo que necesites. "
    "Puedes preguntarme por los servicios, horarios "
    "o, si quieres, podemos buscar una hora para ti 😊."
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
                    SALUDO_INICIAL,
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


            # =================================================
            # RESERVA
            # =================================================

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


            # =================================================
            # CONVERSACIÓN GENERAL
            # =================================================

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
        # DEDUPLICACIÓN
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
            repr(e)
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
    box-shadow: 0 5px 25px rgba(0,0,0,.10);
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

    border-top:
        1px solid #ddd;
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


@media (max-width: 600px) {

    #chat-container {

        width: calc(100% - 20px);

        height: calc(100% - 20px);

        right: 10px;

        bottom: 10px;
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
