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
# PROXY / HTTPS RENDER
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
DURACION_RESERVA = 60

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

    "corte": {
        "nombre": "Corte de cabello",
        "duracion": 60,
        "precio": 20000,
    },

    "corte_barba": {
        "nombre": "Corte + barba",
        "duracion": 60,
        "precio": 20000,
    },

    "barba": {
        "nombre": "Arreglo de barba",
        "duracion": 60,
        "precio": 20000,
    },

    "corte_nino": {
        "nombre": "Corte de niño",
        "duracion": 60,
        "precio": 20000,
    },

    "perfilado": {
        "nombre": "Perfilado",
        "duracion": 60,
        "precio": 20000,
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
# WHATSAPP ENVIAR
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
            "WhatsApp envío:",
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

            "paso_agenda": None,

            "horas_opciones": [],

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
# GOOGLE SERVICE
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
# TIMEZONE
# ============================================================

def obtener_zona():

    return pytz.timezone(
        TIMEZONE
    )


def ahora_local():

    return datetime.now(
        obtener_zona()
    )


# ============================================================
# NORMALIZAR
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

    for original, nuevo in reemplazos.items():

        texto = texto.replace(
            original,
            nuevo
        )

    return texto


# ============================================================
# CANCELACIÓN / SALIDA
# ============================================================

def es_cancelacion(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    patrones = [

        "no",
        "no gracias",
        "gracias",
        "muchas gracias",
        "despues",
        "después",
        "mas tarde",
        "más tarde",
        "otro dia",
        "otro día",
        "lo pensare",
        "lo pensare",
        "no quiero",
        "no por ahora",
        "dejalo",
        "déjalo",
        "cancelar",
        "cancela",
        "salir",
    ]

    texto_n = texto_n.strip()

    return texto_n in patrones


# ============================================================
# SERVICIOS TEXTO
# ============================================================

def texto_servicios():

    return (

        "✂️ Estos son nuestros servicios:\n\n"

        "1️⃣ Corte de cabello — $20.000\n"
        "2️⃣ Corte + barba — $20.000\n"
        "3️⃣ Arreglo de barba — $20.000\n"
        "4️⃣ Corte de niño — $20.000\n"
        "5️⃣ Perfilado — $20.000\n\n"

        "Todos tienen una duración de 1 hora.\n\n"

        "Si quieres reservar, dime "
        "y te ayudo a encontrar una hora disponible 😊"
    )


# ============================================================
# DETECTAR SERVICIO
# ============================================================

def detectar_servicio(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    if (
        "corte" in texto_n
        and "barba" in texto_n
    ):
        return "corte_barba"

    if (
        "corte de nino" in texto_n
        or "corte nino" in texto_n
        or "cortar al nino" in texto_n
        or "nino" in texto_n
    ):
        return "corte_nino"

    if "barba" in texto_n:
        return "barba"

    if (
        "perfilado" in texto_n
        or "perfil" in texto_n
    ):
        return "perfilado"

    if (
        "corte" in texto_n
        or "cortar" in texto_n
    ):
        return "corte"

    return None


# ============================================================
# DETECTAR SERVICIO POR NÚMERO
# ============================================================

def detectar_servicio_numero(
    texto
):

    texto_n = normalizar_texto(
        texto
    ).strip()

    mapa = {

        "1": "corte",
        "2": "corte_barba",
        "3": "barba",
        "4": "corte_nino",
        "5": "perfilado",
    }

    return mapa.get(
        texto_n
    )


# ============================================================
# OBTENER SERVICIO
# ============================================================

def obtener_servicio(
    codigo
):

    return SERVICIOS.get(
        codigo
    )


# ============================================================
# DÍA ATENCIÓN
# ============================================================

def es_dia_atencion(
    fecha
):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return fecha.weekday() in DIAS_ATENCION


# ============================================================
# HORA ATENCIÓN
# ============================================================

def es_hora_atencion(
    fecha
):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (

        es_dia_atencion(
            fecha
        )

        and fecha.minute == 0

        and fecha.second == 0

        and HORA_APERTURA
        <= fecha.hour
        < HORA_CIERRE
    )


# ============================================================
# HORARIO
# ============================================================

def horario_atencion_texto():

    return (
        "lunes a sábado, de 10:00 a 18:00 hrs"
    )


# ============================================================
# DÍAS
# ============================================================

DIAS_NOMBRES = [

    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


# ============================================================
# PARSEAR HORA
# ============================================================

def parse_hora_texto(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    # 10:00
    match = re.search(
        r"\b(\d{1,2})\s*:\s*(\d{2})\b",
        texto_n
    )

    if match:

        hora = int(
            match.group(1)
        )

        minuto = int(
            match.group(2)
        )

        return hora, minuto


    # 10 am / 3 pm
    match = re.search(
        r"\b(\d{1,2})\s*(am|pm)\b",
        texto_n
    )

    if match:

        hora = int(
            match.group(1)
        )

        periodo = match.group(2)

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

        return hora, 0


    # a las 3
    match = re.search(
        r"\ba\s+las?\s+(\d{1,2})\b",
        texto_n
    )

    if match:

        hora = int(
            match.group(1)
        )

        if 1 <= hora <= 6:
            hora += 12

        return hora, 0


    # 15 horas
    match = re.search(
        r"\b(\d{1,2})\s*(?:hrs?|horas?)\b",
        texto_n
    )

    if match:

        hora = int(
            match.group(1)
        )

        if 1 <= hora <= 6:
            hora += 12

        return hora, 0


    return None


# ============================================================
# DETECTAR DÍA SEMANA
# ============================================================

def detectar_dia_semana(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    dias = {

        "lunes": 0,
        "martes": 1,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sabado": 5,
        "domingo": 6,
    }

    for nombre, numero in dias.items():

        if re.search(
            rf"\b{nombre}\b",
            texto_n
        ):

            return numero

    return None


# ============================================================
# CONSTRUIR FECHA
# ============================================================

def construir_fecha_desde_texto(
    texto
):

    zona = obtener_zona()
    ahora = ahora_local()

    texto_n = normalizar_texto(
        texto
    )


    # PASADO MAÑANA
    if re.search(
        r"\bpasado manana\b",
        texto_n
    ):

        fecha = ahora + timedelta(days=2)

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # MAÑANA
    if re.search(
        r"\bmanana\b",
        texto_n
    ):

        fecha = ahora + timedelta(days=1)

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # HOY
    if re.search(
        r"\bhoy\b",
        texto_n
    ):

        return ahora.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # DÍA DE SEMANA
    weekday = detectar_dia_semana(
        texto
    )

    if weekday is not None:

        fecha = ahora + timedelta(

            days=(
                weekday
                - ahora.weekday()
            ) % 7
        )

        if fecha.date() == ahora.date():

            if not re.search(
                r"\bhoy\b",
                texto_n
            ):

                fecha += timedelta(
                    days=7
                )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # DÍA + MES
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

    match = re.search(

        r"\b(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|"
        r"julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre)\b",

        texto_n
    )

    if match:

        dia = int(
            match.group(1)
        )

        mes = meses[
            match.group(2)
        ]

        anio = ahora.year

        try:

            fecha = datetime(
                anio,
                mes,
                dia,
                tzinfo=zona
            )

        except ValueError:

            return None

        if fecha.date() < ahora.date():

            fecha = datetime(
                anio + 1,
                mes,
                dia,
                tzinfo=zona
            )

        return fecha


    # DD/MM
    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})\b",
        texto_n
    )

    if match:

        dia = int(
            match.group(1)
        )

        mes = int(
            match.group(2)
        )

        try:

            fecha = datetime(
                ahora.year,
                mes,
                dia,
                tzinfo=zona
            )

        except ValueError:

            return None

        if fecha.date() < ahora.date():

            try:

                fecha = datetime(
                    ahora.year + 1,
                    mes,
                    dia,
                    tzinfo=zona
                )

            except ValueError:

                return None

        return fecha


    # SOLO DÍA
    match = re.search(
        r"\b(?:el\s+|dia\s+|día\s+)?"
        r"(\d{1,2})\b",
        texto_n
    )

    if match:

        dia = int(
            match.group(1)
        )

        if 1 <= dia <= 31:

            try:

                fecha = datetime(
                    ahora.year,
                    ahora.month,
                    dia,
                    tzinfo=zona
                )

                if fecha.date() < ahora.date():

                    if ahora.month == 12:

                        fecha = datetime(
                            ahora.year + 1,
                            1,
                            dia,
                            tzinfo=zona
                        )

                    else:

                        fecha = datetime(
                            ahora.year,
                            ahora.month + 1,
                            dia,
                            tzinfo=zona
                        )

                return fecha

            except ValueError:

                return None

    return None


# ============================================================
# PARSEAR FECHA/HORA
# ============================================================

def parse_fecha_hora(
    texto
):

    zona = obtener_zona()
    ahora = ahora_local()

    if not texto:
        return None

    fecha = construir_fecha_desde_texto(
        texto
    )

    if fecha is None:

        try:

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
                }
            )

            if resultado:

                if resultado.tzinfo is None:

                    resultado = zona.localize(
                        resultado
                    )

                fecha = resultado.astimezone(
                    zona
                )

        except Exception as e:

            print(
                "DATEPARSER:",
                repr(e)
            )


    if fecha is None:
        return None


    hora = parse_hora_texto(
        texto
    )

    if hora:

        fecha = fecha.replace(

            hour=hora[0],

            minute=hora[1],

            second=0,

            microsecond=0
        )

    else:

        fecha = fecha.replace(

            hour=0,

            minute=0,

            second=0,

            microsecond=0
        )


    return fecha.astimezone(
        zona
    )


def contiene_hora(
    texto
):

    return (
        parse_hora_texto(texto)
        is not None
    )


# ============================================================
# FORMATOS
# ============================================================

def formato_fecha(
    fecha
):

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
        f"a las "
        f"{fecha.strftime('%H:%M')}"
    )


def formato_fecha_corta(
    fecha
):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (

        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month}"
    )


# ============================================================
# GOOGLE DISPONIBILIDAD
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion=DURACION_RESERVA
):

    try:

        zona = obtener_zona()

        inicio = inicio.astimezone(
            zona
        )

        if not es_hora_atencion(
            inicio
        ):

            return False

        fin = inicio + timedelta(
            minutes=duracion
        )

        if fin > inicio.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        ):

            return False

        service = obtener_calendar_service()

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

        bloques = calendario.get(
            "busy",
            []
        )

        return len(bloques) == 0

    except Exception as e:

        print(
            "Calendar availability:",
            repr(e)
        )

        return None


# ============================================================
# BUSCAR HORAS
# ============================================================

def buscar_horas_disponibles(
    fecha,
    cantidad=10,
    desde_hora=None
):

    fecha = fecha.astimezone(
        obtener_zona()
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

        if inicio <= ahora:
            continue

        disponible = verificar_disponibilidad(
            inicio
        )

        if disponible:

            resultados.append(
                inicio
            )

            if len(resultados) >= cantidad:
                break

    return resultados


# ============================================================
# PRÓXIMAS 10 HORAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
    cantidad=10,
    dias_maximos=14
):

    fecha_inicial = fecha_inicial.astimezone(
        obtener_zona()
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

        ahora = ahora_local()

        if fecha.date() == ahora.date():

            if ahora.minute == 0:
                desde_hora = ahora.hour
            else:
                desde_hora = ahora.hour + 1

            desde_hora = max(
                desde_hora,
                HORA_APERTURA
            )

        horas = buscar_horas_disponibles(

            fecha,

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
# FORMATEAR OPCIONES
# ============================================================

def formatear_opciones_horas(
    horas
):

    if not horas:
        return "No encontré horas disponibles."

    lineas = []

    for i, hora in enumerate(
        horas,
        start=1
    ):

        lineas.append(

            f"{i}️⃣ "
            f"{formato_fecha_corta(hora)} "
            f"a las {hora.strftime('%H:%M')}"
        )

    return "\n".join(
        lineas
    )


# ============================================================
# DETECTAR NÚMERO DE HORA
# ============================================================

def detectar_numero_opcion(
    texto,
    cantidad
):

    texto_n = normalizar_texto(
        texto
    ).strip()

    match = re.fullmatch(
        r"(\d{1,2})",
        texto_n
    )

    if not match:
        return None

    numero = int(
        match.group(1)
    )

    if 1 <= numero <= cantidad:
        return numero

    return None


# ============================================================
# INTENCIÓN DE AGENDA
# ============================================================

def es_intencion_agendar(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

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
        "quiero una cita",
        "cita",
        "turno",
    ]

    return any(
        patron in texto_n
        for patron in patrones
    )


# ============================================================
# SERVICIOS INTENCIÓN
# ============================================================

def pregunta_servicios(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    patrones = [

        "servicios",
        "servicio",
        "que haces",
        "que ofrecen",
        "que tienen",
        "que cortes",
        "que corte",
        "precio",
        "precios",
        "valor",
        "valores",
        "cuanto sale",
        "cuanto cuesta",
        "cuanto valen",
        "tarifa",
    ]

    return any(
        patron in texto_n
        for patron in patrones
    )


# ============================================================
# DISPONIBILIDAD
# ============================================================

def pregunta_disponibilidad(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    patrones = [

        "disponible",
        "disponibilidad",
        "que horas",
        "hay hora",
        "tienes hora",
        "tiene hora",
        "queda hora",
        "horas libres",
        "horas disponibles",
    ]

    return any(
        patron in texto_n
        for patron in patrones
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

Eres el Asistente Virtual de {NEGOCIO_NOMBRE}.

Tu nombre es:
"Asistente Virtual de Estilista {ESTILISTA_NOMBRE}".

Atiendes clientes como un asistente conversacional
natural, similar a ChatGPT, pero orientado finalmente
a ayudar a conocer los servicios o reservar una hora.

IMPORTANTE:

NO debes intentar agendar inmediatamente.

Primero conversa naturalmente.

Ejemplo:

Cliente:
Hola

Asistente:
¡Hola! 👋 Soy el Asistente Virtual de Estilista Diego ✂️
¿Cómo estás?

Cliente:
Súper bien ¿y tú?

Asistente:
¡Muy bien también! 😄 Gracias por preguntar.
¿Qué te gustaría hacer? Puedo contarte sobre nuestros
servicios o ayudarte a reservar una hora.

Nunca respondas simplemente:
"¡Claro! Cuéntame qué necesitas y te ayudo."
cuando el cliente está conversando normalmente.

Mantén una conversación humana.

PERSONALIDAD:

- cercano
- simpático
- amable
- profesional
- natural
- español de Chile
- respuestas relativamente cortas

SERVICIOS:

1. Corte de cabello — $20.000
2. Corte + barba — $20.000
3. Arreglo de barba — $20.000
4. Corte de niño — $20.000
5. Perfilado — $20.000

Todos duran 1 hora.

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Último inicio:
17:00.

Domingo:
cerrado.

Si el cliente pregunta por precios o servicios,
puedes explicarlos.

Si el cliente expresa claramente que quiere reservar,
el sistema externo iniciará el flujo de agenda.

No inventes disponibilidad.

No inventes reservas.

La disponibilidad real se consulta en Google Calendar.

Si el cliente dice que no quiere reservar,
no insistas.

Puedes responder cordialmente y dejar abierta
la posibilidad de volver más adelante.

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

                temperature=0.8,
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
                "¡Qué bueno! 😊 "
                "¿Quieres conocer nuestros servicios "
                "o estás buscando reservar una hora?"
            )

        return respuesta.strip()

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        return (

            "¡Qué bueno! 😊 "
            "¿Quieres conocer nuestros servicios "
            "o estás buscando reservar una hora?"
        )


# ============================================================
# CANCELAR AGENDA
# ============================================================

def cancelar_agenda(
    estado
):

    telefono = (
        estado
        .get("datos_reserva", {})
        .get("telefono")
    )

    estado["modo_agendar"] = False
    estado["paso_agenda"] = None
    estado["horas_opciones"] = []

    estado["datos_reserva"] = {

        "servicio": None,

        "fecha_hora": None,

        "nombre": None,

        "telefono": telefono,
    }


# ============================================================
# PREGUNTAR SERVICIO
# ============================================================

def preguntar_servicio():

    return (

        "¡Perfecto! 🙌 Empecemos.\n\n"

        "¿Qué servicio quieres reservar?\n\n"

        "1️⃣ Corte de cabello — $20.000\n"
        "2️⃣ Corte + barba — $20.000\n"
        "3️⃣ Arreglo de barba — $20.000\n"
        "4️⃣ Corte de niño — $20.000\n"
        "5️⃣ Perfilado — $20.000\n\n"

        "Puedes responder con el número o "
        "con el nombre del servicio."
    )


# ============================================================
# PEDIR FECHA
# ============================================================

def pedir_fecha():

    return (

        "Perfecto ✂️\n\n"

        "¿Qué día te gustaría venir?\n\n"

        "Por ejemplo:\n"
        "• mañana\n"
        "• lunes\n"
        "• martes\n"
        "• el 20 de agosto\n\n"

        f"Atendemos {horario_atencion_texto()}."
    )


# ============================================================
# MOSTRAR HORAS
# ============================================================

def mostrar_horas(
    estado,
    fecha
):

    horas = buscar_proximas_horas(
        fecha,
        cantidad=10
    )

    if not horas:

        estado["horas_opciones"] = []

        return (

            "No encontré horas disponibles "
            "para ese período 😕.\n\n"

            "¿Quieres probar con otro día?"
        )

    estado["horas_opciones"] = [

        h.isoformat()
        for h in horas
    ]

    return (

        f"Perfecto 👍 Para ese período "
        f"estas son las próximas horas disponibles:\n\n"

        f"{formatear_opciones_horas(horas)}\n\n"

        "👉 Respóndeme con el número de la hora "
        "que prefieras, por ejemplo: 3"
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

    datos = estado[
        "datos_reserva"
    ]


    # ========================================================
    # CANCELACIÓN
    # ========================================================

    if es_cancelacion(
        texto
    ):

        cancelar_agenda(
            estado
        )

        return (

            "No hay problema 😊\n\n"

            "Cuando quieras reservar o conocer "
            "nuestros servicios, aquí estaré. ✂️"
        )


    # ========================================================
    # PASO 1 - SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio_numero(
            texto
        )

        if not servicio:

            servicio = detectar_servicio(
                texto
            )

        if not servicio:

            return preguntar_servicio()

        datos["servicio"] = servicio

        estado["paso_agenda"] = "fecha"


        # Si ya escribió fecha junto al servicio
        fecha = parse_fecha_hora(
            texto
        )

        if fecha:

            return procesar_reserva(
                estado,
                texto
            )

        return pedir_fecha()


    # ========================================================
    # PASO 2 - SELECCIÓN DE HORA
    # ========================================================

    if (
        estado["horas_opciones"]
        and estado["paso_agenda"]
        == "hora"
    ):

        numero = detectar_numero_opcion(

            texto,

            len(
                estado["horas_opciones"]
            )
        )

        if numero is None:

            return (

                "Solo necesito que me indiques "
                "el número de la hora que prefieres 😊\n\n"

                f"{formatear_opciones_horas("
                    "[datetime.fromisoformat(x) "
                    "for x in estado['horas_opciones']]"
                )}"
            )

        fecha_hora = datetime.fromisoformat(

            estado[
                "horas_opciones"
            ][numero - 1]
        )

        # VERIFICACIÓN FINAL
        disponible = verificar_disponibilidad(
            fecha_hora
        )

        if disponible is None:

            return (

                "No pude consultar la agenda "
                "en este momento 😕.\n\n"
                "Intenta nuevamente."
            )

        if not disponible:

            estado["horas_opciones"] = []

            return (

                "Justo esa hora acaba de ocuparse 😕.\n\n"

                "Voy a buscar nuevamente "
                "las próximas horas disponibles."
            )

        datos["fecha_hora"] = (
            fecha_hora.isoformat()
        )

        estado["horas_opciones"] = []
        estado["paso_agenda"] = "nombre"

        return (

            "¡Excelente elección! 🙌\n\n"

            f"Tengo reservada provisionalmente "
            f"la hora del "
            f"{formato_fecha(fecha_hora)}.\n\n"

            "¿Me indicas tu nombre?"
        )


    # ========================================================
    # PASO 3 - FECHA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )

        if fecha is None:

            return (

                "No alcancé a identificar el día 😊.\n\n"

                "Puedes decirme, por ejemplo:\n"
                "• mañana\n"
                "• lunes\n"
                "• el 20 de agosto"
            )


        if not es_dia_atencion(
            fecha
        ):

            return (

                "Ese día no atendemos 😕.\n\n"

                f"Nuestro horario es "
                f"{horario_atencion_texto()}.\n\n"

                "Dime otro día y revisamos "
                "las horas disponibles."
            )


        # Si el cliente escribió hora exacta,
        # igualmente podemos convertirla en opción
        if contiene_hora(texto):

            hora_info = parse_hora_texto(
                texto
            )

            if hora_info:

                fecha_con_hora = fecha.replace(

                    hour=hora_info[0],

                    minute=hora_info[1],

                    second=0,

                    microsecond=0
                )

                if (
                    fecha_con_hora.minute != 0
                    or fecha_con_hora.hour
                    not in HORAS_DISPONIBLES
                ):

                    return mostrar_horas(
                        estado,
                        fecha
                    )

                disponible = verificar_disponibilidad(
                    fecha_con_hora
                )

                if disponible:

                    datos["fecha_hora"] = (
                        fecha_con_hora.isoformat()
                    )

                    estado["paso_agenda"] = "nombre"

                    return (

                        "¡Perfecto! 🙌\n\n"

                        f"Tengo disponible "
                        f"{formato_fecha(fecha_con_hora)}.\n\n"

                        "¿Me indicas tu nombre?"
                    )

        estado["paso_agenda"] = "hora"

        return mostrar_horas(
            estado,
            fecha
        )


    # ========================================================
    # PASO 4 - NOMBRE
    # ========================================================

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? 😊"
            )

        datos["nombre"] = texto

        # WHATSAPP YA TIENE TELÉFONO
        if datos.get("telefono"):

            return completar_reserva(
                estado
            )

        estado["paso_agenda"] = "telefono"

        return (

            f"Perfecto, {datos['nombre']} 👍\n\n"

            "¿Cuál es tu número de teléfono?"
        )


    # ========================================================
    # PASO 5 - TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto

        return completar_reserva(
            estado
        )


    # ========================================================
    # FINAL
    # ========================================================

    return completar_reserva(
        estado
    )


# ============================================================
# COMPLETAR RESERVA
# ============================================================

def completar_reserva(
    estado
):

    datos = estado[
        "datos_reserva"
    ]

    if not datos["fecha_hora"]:

        estado["paso_agenda"] = "fecha"

        return pedir_fecha()


    if not datos["servicio"]:

        estado["paso_agenda"] = "servicio"

        return preguntar_servicio()


    if not datos["nombre"]:

        estado["paso_agenda"] = "nombre"

        return (
            "¿Me indicas tu nombre? 😊"
        )


    if not datos["telefono"]:

        estado["paso_agenda"] = "telefono"

        return (
            "¿Cuál es tu número de teléfono?"
        )


    try:

        inicio = datetime.fromisoformat(
            datos["fecha_hora"]
        )

    except Exception:

        datos["fecha_hora"] = None

        estado["paso_agenda"] = "fecha"

        return pedir_fecha()


    # ========================================================
    # SEGUNDA COMPROBACIÓN
    # ========================================================

    disponible = verificar_disponibilidad(
        inicio
    )

    if disponible is None:

        return (

            "No pude comprobar nuevamente "
            "la disponibilidad 😕.\n\n"

            "Intenta nuevamente en unos segundos."
        )


    if not disponible:

        datos["fecha_hora"] = None

        estado["paso_agenda"] = "fecha"

        return (

            "Justo esa hora se ocupó 😕.\n\n"

            "Dime otro día y buscaré "
            "nuevamente las próximas horas."
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
            "ERROR CREANDO RESERVA:",
            resultado.get("error")
        )

        return (

            "No pude completar la reserva "
            "en este momento 😕.\n\n"

            "Intenta nuevamente en unos segundos."
        )


    servicio = obtener_servicio(
        datos["servicio"]
    )

    nombre = datos["nombre"]
    telefono = datos["telefono"]


    # ========================================================
    # GUARDAR TELÉFONO
    # ========================================================

    telefono_guardar = telefono


    # ========================================================
    # LIMPIAR ESTADO
    # ========================================================

    estado["modo_agendar"] = False
    estado["paso_agenda"] = None
    estado["horas_opciones"] = []

    estado["datos_reserva"] = {

        "servicio": None,

        "fecha_hora": None,

        "nombre": None,

        "telefono":
            telefono_guardar,
    }


    return (

        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: "
        f"{servicio['nombre']}\n"

        f"💰 Valor: "
        f"${servicio['precio']:,}".replace(",", ".")
        f"\n"

        f"👤 Cliente: {nombre}\n"

        f"📞 Teléfono: {telefono}\n"

        f"📅 {formato_fecha(inicio)}\n\n"

        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"

        "La atención dura 1 hora.\n\n"

        "¡Te esperamos! 🙌✂️"
    )


# ============================================================
# CREAR EVENTO GOOGLE
# ============================================================

def crear_evento_diego(

    inicio,

    servicio_codigo,

    nombre_cliente,

    telefono_cliente
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
                (
                    f"{servicio['nombre']} - "
                    f"{nombre_cliente}"
                ),

            "description":
                (
                    "Reserva creada por "
                    "Asistente Virtual de "
                    f"Estilista {ESTILISTA_NOMBRE}.\n\n"

                    f"Cliente: {nombre_cliente}\n"

                    f"Teléfono: {telefono_cliente}\n"

                    f"Servicio: "
                    f"{servicio['nombre']}\n"

                    f"Valor: "
                    f"${servicio['precio']}\n"

                    f"Duración: "
                    f"{DURACION_RESERVA} minutos\n"

                    "Origen: Asistente Virtual"
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
                        str(DURACION_RESERVA),

                    "origen":
                        (
                            "Asistente Virtual "
                            f"{ESTILISTA_NOMBRE}"
                        ),
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

        print(
            "EVENTO GOOGLE CREADO:",
            resultado.get("id")
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
            "ERROR GOOGLE EVENT:",
            repr(e)
        )

        return {

            "ok":
                False,

            "error":
                str(e)
        }


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
                        "¡Hola! 👋 "
                        "Soy el Asistente Virtual "
                        "de Estilista Diego ✂️\n\n"
                        "¿Cómo estás?"
                    ),
            }
        ]


    if "modo_agendar" not in session:

        session["modo_agendar"] = False


    if "paso_agenda" not in session:

        session["paso_agenda"] = None


    if "horas_opciones" not in session:

        session["horas_opciones"] = []


    if "datos_reserva" not in session:

        session["datos_reserva"] = {

            "servicio": None,

            "fecha_hora": None,

            "nombre": None,

            "telefono": None,
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
            # CANCELAR AGENDA
            # =================================================

            if (
                session.get(
                    "modo_agendar"
                )
                and es_cancelacion(
                    pregunta
                )
            ):

                estado = {

                    "modo_agendar":
                        session[
                            "modo_agendar"
                        ],

                    "paso_agenda":
                        session[
                            "paso_agenda"
                        ],

                    "horas_opciones":
                        session[
                            "horas_opciones"
                        ],

                    "datos_reserva":
                        session[
                            "datos_reserva"
                        ],
                }

                respuesta = cancelar_agenda(
                    estado
                )

                if respuesta is None:

                    respuesta = (

                        "No hay problema 😊 "
                        "Cuando quieras, aquí estaré."
                    )

                session[
                    "modo_agendar"
                ] = estado[
                    "modo_agendar"
                ]

                session[
                    "paso_agenda"
                ] = estado[
                    "paso_agenda"
                ]

                session[
                    "horas_opciones"
                ] = estado[
                    "horas_opciones"
                ]

                session[
                    "datos_reserva"
                ] = estado[
                    "datos_reserva"
                ]


            else:

                # =================================================
                # SERVICIOS
                # =================================================

                if (
                    not session.get(
                        "modo_agendar"
                    )
                    and pregunta_servicios(
                        pregunta
                    )
                ):

                    respuesta = texto_servicios()


                # =================================================
                # AGENDA
                # =================================================

                elif (

                    es_intencion_agendar(
                        pregunta
                    )

                    or pregunta_disponibilidad(
                        pregunta
                    )

                    or session.get(
                        "modo_agendar",
                        False
                    )
                ):

                    session[
                        "modo_agendar"
                    ] = True

                    estado = {

                        "modo_agendar":
                            True,

                        "paso_agenda":
                            session[
                                "paso_agenda"
                            ],

                        "horas_opciones":
                            session[
                                "horas_opciones"
                            ],

                        "datos_reserva":
                            session[
                                "datos_reserva"
                            ],
                    }

                    respuesta = procesar_reserva(

                        estado,

                        pregunta
                    )

                    session[
                        "modo_agendar"
                    ] = estado[
                        "modo_agendar"
                    ]

                    session[
                        "paso_agenda"
                    ] = estado[
                        "paso_agenda"
                    ]

                    session[
                        "horas_opciones"
                    ] = estado[
                        "horas_opciones"
                    ]

                    session[
                        "datos_reserva"
                    ] = estado[
                        "datos_reserva"
                    ]


                # =================================================
                # CONVERSACIÓN NORMAL
                # =================================================

                else:

                    respuesta = responder_openai(

                        session[
                            "historial"
                        ],

                        pregunta
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
            session[
                "historial"
            ]
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

        if value.get("statuses"):

            return "ok", 200

        messages = (
            value.get("messages")
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

                (
                    "Por ahora puedo ayudarte "
                    "por mensaje de texto 😊."
                )
            )

            return "ok", 200


        # ====================================================
        # DEDUP
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
                    - PROCESSED_MSG_IDS[
                        old_id
                    ]
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
        # ESTADO
        # ====================================================

        estado = get_wa_session(
            wa_id
        )

        estado[
            "datos_reserva"
        ][
            "telefono"
        ] = wa_id


        estado[
            "historial"
        ].append({

            "role":
                "user",

            "content":
                text,
        })


        # ====================================================
        # CANCELACIÓN
        # ====================================================

        if (

            estado[
                "modo_agendar"
            ]

            and es_cancelacion(
                text
            )
        ):

            cancelar_agenda(
                estado
            )

            respuesta = (

                "No hay problema 😊\n\n"

                "Cuando quieras conocer nuestros "
                "servicios o reservar una hora, "
                "aquí estaré. ✂️"
            )


        # ====================================================
        # SERVICIOS
        # ====================================================

        elif (

            not estado[
                "modo_agendar"
            ]

            and pregunta_servicios(
                text
            )
        ):

            respuesta = texto_servicios()


        # ====================================================
        # AGENDA
        # ====================================================

        elif (

            es_intencion_agendar(
                text
            )

            or pregunta_disponibilidad(
                text
            )

            or estado[
                "modo_agendar"
            ]
        ):

            estado[
                "modo_agendar"
            ] = True

            respuesta = procesar_reserva(

                estado,

                text
            )


        # ====================================================
        # CONVERSACIÓN
        # ====================================================

        else:

            respuesta = responder_openai(

                estado[
                    "historial"
                ],

                text
            )


        estado[
            "historial"
        ].append({

            "role":
                "assistant",

            "content":
                respuesta,
        })


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
# TOKEN TEMPLATE
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

<li>Ve a Environment.</li>

<li>
Busca <b>GOOGLE_REFRESH_TOKEN</b>.
</li>

<li>
Pega el token.
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
# ERROR TEMPLATE
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
# CHAT TEMPLATE
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

@media (max-width: 600px) {

    #chat-container {

        width:
            calc(100% - 20px);

        height:
            calc(100% - 20px);

        right:
            10px;

        bottom:
            10px;
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
Lunes a sábado · 10:00 a 18:00 · $20.000
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

        debug=(
            os.getenv(
                "FLASK_ENV"
            )
            == "development"
        )
    )
