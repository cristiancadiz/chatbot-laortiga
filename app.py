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

HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_CIERRE + 1
    )
)

# IMPORTANTE:
# TODAS las reservas duran 1 hora.
DURACION_RESERVA = 60


# ============================================================
# SERVICIOS
# ============================================================

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
# FECHA / HORA
# ============================================================

def ahora_local():

    zona = pytz.timezone(
        TIMEZONE
    )

    return datetime.now(
        zona
    )


def normalizar_texto(
    texto
):

    texto = (
        texto or ""
    ).lower().strip()

    reemplazos = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
    }

    for a, b in reemplazos.items():

        texto = texto.replace(
            a,
            b
        )

    return texto


def extraer_hora(
    texto
):

    """
    Detecta:

    10
    10 am
    10:00
    10:30
    3 pm
    15:00

    Devuelve hora y minuto.
    """

    texto_original = (
        texto or ""
    ).lower()

    patron = re.search(

        r"\b(\d{1,2})(?::(\d{2}))?\s*"
        r"(am|pm|a\.m\.|p\.m\.)?\b",

        texto_original
    )

    if not patron:
        return None

    hora = int(
        patron.group(1)
    )

    minuto = (
        int(patron.group(2))
        if patron.group(2)
        else 0
    )

    periodo = patron.group(3)

    if periodo:

        periodo = (
            periodo
            .replace(".", "")
        )

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

    else:

        # Si escribe 3, entendemos 15:00
        # dentro del contexto de atención.
        if 1 <= hora <= 8:
            hora += 12

    if not 0 <= hora <= 23:
        return None

    if not 0 <= minuto <= 59:
        return None

    return hora, minuto


# ============================================================
# PARSER DE FECHAS
# ============================================================

def parse_fecha_hora(
    texto
):

    """
    Parser más controlado para evitar que:

    "lunes a las 10"

    termine interpretándose como una fecha
    completamente distinta.

    Soporta:

    - hoy
    - mañana
    - pasado mañana
    - lunes
    - martes
    - miércoles
    - jueves
    - viernes
    - sábado
    - domingo
    - el 17
    - 17 de agosto
    - 17/08
    - 17-08
    - 17 de agosto a las 15
    - próximo lunes
    """

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        ahora = datetime.now(
            zona
        )

        texto_original = (
            texto or ""
        ).strip()

        t = normalizar_texto(
            texto_original
        )

        # ----------------------------------------------------
        # HORA
        # ----------------------------------------------------

        hora_info = extraer_hora(
            texto_original
        )

        if hora_info:

            hora,
            minuto = hora_info

        else:

            # Si no existe hora, no podemos
            # confirmar una reserva.
            return None

        # ----------------------------------------------------
        # FECHA BASE
        # ----------------------------------------------------

        fecha_base = None

        # Hoy
        if re.search(
            r"\bhoy\b",
            t
        ):

            fecha_base = ahora.date()

        # Mañana
        elif re.search(
            r"\bmanana\b",
            t
        ):

            fecha_base = (
                ahora
                + timedelta(days=1)
            ).date()

        # Pasado mañana
        elif re.search(
            r"\bpasado manana\b",
            t
        ):

            fecha_base = (
                ahora
                + timedelta(days=2)
            ).date()

        # ----------------------------------------------------
        # FECHA NUMÉRICA
        # ----------------------------------------------------

        if fecha_base is None:

            match_fecha = re.search(

                r"\b(\d{1,2})[\/\-](\d{1,2})"
                r"(?:[\/\-](\d{2,4}))?\b",

                t
            )

            if match_fecha:

                dia = int(
                    match_fecha.group(1)
                )

                mes = int(
                    match_fecha.group(2)
                )

                anio_txt = (
                    match_fecha.group(3)
                )

                if anio_txt:

                    anio = int(
                        anio_txt
                    )

                    if anio < 100:
                        anio += 2000

                else:

                    anio = ahora.year

                    posible = datetime(
                        anio,
                        mes,
                        dia,
                        tzinfo=zona
                    )

                    if posible.date() < ahora.date():

                        anio += 1

                fecha_base = datetime(
                    anio,
                    mes,
                    dia
                ).date()

        # ----------------------------------------------------
        # "17 DE AGOSTO"
        # ----------------------------------------------------

        if fecha_base is None:

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

            for nombre_mes, numero_mes in meses.items():

                patron = re.search(

                    rf"\b(\d{{1,2}})\s+de\s+{nombre_mes}\b",

                    t
                )

                if patron:

                    dia = int(
                        patron.group(1)
                    )

                    anio = ahora.year

                    candidata = datetime(
                        anio,
                        numero_mes,
                        dia
                    ).date()

                    if candidata < ahora.date():
                        anio += 1

                    fecha_base = datetime(
                        anio,
                        numero_mes,
                        dia
                    ).date()

                    break

        # ----------------------------------------------------
        # DÍA DE LA SEMANA
        # ----------------------------------------------------

        if fecha_base is None:

            dias = {

                "lunes": 0,
                "martes": 1,
                "miercoles": 2,
                "jueves": 3,
                "viernes": 4,
                "sabado": 5,
                "domingo": 6,
            }

            dia_detectado = None

            for nombre_dia, numero_dia in dias.items():

                if re.search(
                    rf"\b{nombre_dia}\b",
                    t
                ):

                    dia_detectado = numero_dia
                    break

            if dia_detectado is not None:

                dias_adelante = (
                    dia_detectado
                    - ahora.weekday()
                ) % 7

                # Si dice "próximo lunes"
                # y hoy es lunes, queremos
                # el lunes siguiente.
                if (
                    "proximo" in t
                    or "siguiente" in t
                ):

                    if dias_adelante == 0:
                        dias_adelante = 7
                    elif dias_adelante == 0:
                        dias_adelante = 7

                # Si hoy es lunes y dice simplemente
                # "lunes", puede reservar hoy si
                # todavía existe una hora futura.
                fecha_base = (
                    ahora
                    + timedelta(
                        days=dias_adelante
                    )
                ).date()

        # ----------------------------------------------------
        # DATEPARSER COMO ÚLTIMO RECURSO
        # ----------------------------------------------------

        if fecha_base is None:

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

            if resultado:

                resultado = (
                    resultado
                    .astimezone(zona)
                )

                fecha_base = (
                    resultado.date()
                )

        if fecha_base is None:
            return None

        resultado = zona.localize(
            datetime(
                fecha_base.year,
                fecha_base.month,
                fecha_base.day,
                hora,
                minuto
            )
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

    fecha = fecha.astimezone(
        pytz.timezone(TIMEZONE)
    )

    return fecha.weekday() in DIAS_ATENCION


def es_hora_atencion(
    fecha
):

    fecha = fecha.astimezone(
        pytz.timezone(TIMEZONE)
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
# SERVICIOS
# ============================================================

def detectar_servicio(
    texto
):

    t = normalizar_texto(
        texto
    )

    if (
        "corte" in t
        and "barba" in t
    ):
        return "corte_barba"

    if (
        "nino" in t
        or "niño" in texto.lower()
    ):
        return "corte_nino"

    if "barba" in t:
        return "barba"

    if (
        "perfilado" in t
        or "perfil" in t
    ):
        return "perfilado"

    if "corte" in t:
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
# CALENDAR DISPONIBILIDAD
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

        # ----------------------------------------------------
        # Validaciones locales
        # ----------------------------------------------------

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

        # No permitir terminar después de las 19:00
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
            calendario
            .get(
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

def buscar_horas_disponibles(
    fecha,
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

        disponible = (
            verificar_disponibilidad(
                inicio,
                DURACION_RESERVA
            )
        )

        if disponible is True:

            resultados.append(
                inicio
            )

            if len(resultados) >= cantidad:
                break

    return resultados


def buscar_proximas_horas(
    fecha_inicial,
    cantidad=5,
    dias_maximos=14
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

        desde_hora = HORA_APERTURA

        if fecha.date() == ahora.date():

            desde_hora = (
                ahora.hour
                + 1
                if ahora.minute > 0
                else ahora.hour
            )

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
            f"• {h.strftime('%H:%M')}"
            for h in horas
        ]
    )


# ============================================================
# INTENCIONES
# ============================================================

def es_intencion_agendar(
    texto
):

    t = normalizar_texto(
        texto
    )

    patrones = [

        r"\bagendar\b",
        r"\bagenda\b",
        r"\breservar\b",
        r"\breserva\b",
        r"\bcita\b",
        r"\bquiero una hora\b",
        r"\bquiero hora\b",
        r"\bnecesito hora\b",
        r"\bdame una hora\b",
        r"\bhorario disponible\b",
        r"\bdisponibilidad\b",
        r"\bpuedo ir\b",
        r"\bcuando puedo\b",
        r"\bque hora tienes\b",
        r"\bque horas tienes\b",
    ]

    for patron in patrones:

        if re.search(
            patron,
            t
        ):
            return True

    # Si dice "corte mañana a las 3",
    # también entendemos intención de reserva.
    tiene_servicio = (
        detectar_servicio(texto)
        is not None
    )

    tiene_fecha = (
        any(
            palabra in t
            for palabra in [
                "hoy",
                "manana",
                "lunes",
                "martes",
                "miercoles",
                "jueves",
                "viernes",
                "sabado",
                "domingo",
            ]
        )
        or bool(
            re.search(
                r"\b\d{1,2}[\/\-]\d{1,2}\b",
                t
            )
        )
        or bool(
            re.search(
                r"\b\d{1,2}\s+de\s+\w+",
                t
            )
        )
    )

    return (
        tiene_servicio
        and tiene_fecha
    )


def es_cancelacion_reserva(
    texto
):

    t = normalizar_texto(
        texto
    )

    frases = [

        "no quiero reservar",
        "no quiero agendar",
        "cancelar",
        "cancela",
        "olvidalo",
        "olvidalo",
        "dejalo",
        "mejor no",
        "no gracias",
        "eso es todo",
        "ya no",
        "salir de reserva",
    ]

    return any(
        frase in t
        for frase in frases
    )


def es_fin_conversacion(
    texto
):

    t = normalizar_texto(
        texto
    )

    frases = [

        "chao",
        "chau",
        "adios",
        "adios gracias",
        "gracias eso es todo",
        "eso seria todo",
        "eso es todo",
        "nos vemos",
        "hasta luego",
        "hasta pronto",
        "me voy",
    ]

    return any(
        frase in t
        for frase in frases
    )


# ============================================================
# OPENAI - CONVERSACIÓN LIBRE
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""
Eres el Asistente Virtual de {NEGOCIO_NOMBRE}.

Representas al estilista/barbero {ESTILISTA_NOMBRE}.

Tu función es atender clientes por conversación,
de forma natural, amable y humana.

IMPORTANTE:

NO debes convertir automáticamente toda conversación
en una reserva.

El cliente puede simplemente:

- saludar
- preguntar cómo estás
- decir "bien"
- preguntar por Diego
- preguntar por los servicios
- conversar
- hacer preguntas generales
- agradecer
- despedirse

Debes responder naturalmente.

Ejemplos:

Cliente:
"hola"

Respuesta:
"¡Hola! 👋 Qué gusto. ¿Cómo estás?"

Cliente:
"como estas?"

Respuesta:
"¡Muy bien, gracias por preguntar! 😊 ¿Y tú, cómo estás?"

Cliente:
"bien"

Respuesta:
"¡Qué bueno! 😊 Cuéntame, ¿en qué te puedo ayudar?"

Cliente:
"estoy buscando un corte"

Respuesta:
"¡Claro! ✂️ Te puedo ayudar a encontrar una hora para tu corte. ¿Qué día te gustaría venir?"

La conversación debe sentirse similar a hablar
con una persona real.

Puedes conversar libremente.

Sin embargo, si el cliente manifiesta claramente
que quiere agendar una hora, debes orientarlo
naturalmente hacia la reserva.

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Domingo cerrado.

Las reservas comienzan solamente a:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00
18:00

Todas las reservas duran exactamente 1 hora.

Nunca inventes disponibilidad.

La disponibilidad real la comprueba el sistema.

Servicios:

- Corte de cabello
- Corte + barba
- Arreglo de barba
- Corte de niño
- Perfilado

Si el cliente pregunta algo que no sabes,
no inventes.

Habla español de Chile.

Sé cercano, breve y natural.

NO digas constantemente:
"¿Qué te gustaría hacer?"

No repitas la misma respuesta.

Si el cliente pregunta "¿cómo estás?",
responde directamente.

Si el cliente dice "bien",
continúa la conversación naturalmente.

Si el cliente quiere terminar:
despídete cordialmente.

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
                historial[-12:]
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
            .strip()
        )

        return respuesta

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        # Respuesta de respaldo para que
        # NUNCA aparezca el mensaje técnico
        # al inicio de la conversación.
        t = normalizar_texto(
            pregunta
        )

        if "como estas" in t:

            return (
                "¡Muy bien, gracias por preguntar! 😊 "
                "¿Y tú, cómo estás?"
            )

        if t in [
            "hola",
            "hola!",
            "holaa",
            "buenas",
            "buenos dias",
            "buenas tardes",
            "buenas noches",
        ]:

            return (
                "¡Hola! 👋 Qué gusto. "
                "¿Cómo estás?"
            )

        if t in [
            "bien",
            "muy bien",
            "todo bien",
        ]:

            return (
                "¡Qué bueno! 😊 "
                "Cuéntame, ¿en qué te puedo ayudar?"
            )

        return (
            "¡Claro! 😊 Cuéntame, "
            "¿en qué te puedo ayudar?"
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

        # TODAS LAS RESERVAS = 60 MINUTOS
        duracion = DURACION_RESERVA

        fin = (
            inicio
            + timedelta(
                minutes=duracion
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

                    "duracion":
                        str(duracion),

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
            "CREATE EVENT ERROR:",
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

    # --------------------------------------------------------
    # CANCELAR MODO RESERVA
    # --------------------------------------------------------

    if es_cancelacion_reserva(
        texto
    ):

        telefono = datos.get(
            "telefono"
        )

        estado[
            "datos_reserva"
        ] = nueva_reserva(
            telefono
        )

        estado[
            "modo_agendar"
        ] = False

        return (
            "Perfecto 😊 No hay problema. "
            "Dejamos la reserva pendiente.\n\n"
            "Si más adelante quieres agendar, "
            "solo dime."
        )

    # --------------------------------------------------------
    # 1. SERVICIO
    # --------------------------------------------------------

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if servicio:

            datos[
                "servicio"
            ] = servicio

        else:

            return (
                "Claro ✂️ ¿Qué servicio quieres reservar?\n\n"

                "• Corte de cabello\n"
                "• Corte + barba\n"
                "• Arreglo de barba\n"
                "• Corte de niño\n"
                "• Perfilado"
            )

    # --------------------------------------------------------
    # 2. FECHA Y HORA
    # --------------------------------------------------------

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )

        if not fecha:

            return (
                "Perfecto 😊 ¿Qué día y a qué hora "
                "te gustaría venir?\n\n"

                "Por ejemplo:\n"
                "• mañana a las 15:00\n"
                "• el lunes a las 10\n"
                "• el 17 a las 3 pm\n"
                "• el próximo sábado a las 11\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )

        zona = pytz.timezone(
            TIMEZONE
        )

        fecha = fecha.astimezone(
            zona
        )

        # ----------------------------------------------------
        # DOMINGO
        # ----------------------------------------------------

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
                    "El domingo no tenemos atención 😕.\n\n"
                    "Puedo ofrecerte estas próximas horas:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál te acomoda?"
                )

            return (
                "El domingo no tenemos atención 😕.\n\n"
                f"Atendemos {horario_atencion_texto()}."
            )

        # ----------------------------------------------------
        # MINUTOS
        # ----------------------------------------------------

        if fecha.minute != 0:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5,
                    dias_maximos=7
                )
            )

            if proximas:

                return (
                    "Las reservas comienzan en horas exactas 🕐.\n\n"
                    "Por ejemplo: 10:00, 11:00, 12:00, "
                    "13:00, 14:00, 15:00, 16:00, "
                    "17:00 o 18:00.\n\n"
                    "Te puedo ofrecer:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál prefieres?"
                )

            return (
                "Las reservas comienzan solamente "
                "en horas exactas 🕐."
            )

        # ----------------------------------------------------
        # FUERA DE HORARIO
        # ----------------------------------------------------

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour > HORA_CIERRE
        ):

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5,
                    dias_maximos=7
                )
            )

            if proximas:

                return (
                    "Ese horario está fuera de atención 😕.\n\n"
                    f"Atendemos {horario_atencion_texto()}.\n\n"
                    "Estas son algunas próximas horas:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál te acomoda?"
                )

            return (
                f"Nuestro horario es "
                f"{horario_atencion_texto()}."
            )

        # ----------------------------------------------------
        # HORA PASADA
        # ----------------------------------------------------

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
                    "Te puedo ofrecer estas próximas horas:\n\n"
                    f"{formato_horas(proximas)}\n\n"
                    "¿Cuál te acomoda?"
                )

            return (
                "Esa hora ya pasó 😕. "
                "Dime otro día y horario."
            )

        # ----------------------------------------------------
        # DISPONIBILIDAD
        # ----------------------------------------------------

        disponible = (
            verificar_disponibilidad(
                fecha,
                DURACION_RESERVA
            )
        )

        if disponible is None:

            return (
                "No pude consultar la agenda de Diego "
                "en este momento 😕.\n\n"
                "Intenta nuevamente en unos segundos."
            )

        # ----------------------------------------------------
        # OCUPADA
        # ----------------------------------------------------

        if not disponible:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5,
                    dias_maximos=7
                )
            )

            if proximas:

                return (
                    f"La hora del "
                    f"{fecha.strftime('%H:%M')} "
                    "ya está ocupada 😕.\n\n"

                    "Pero puedo ofrecerte estas próximas "
                    "horas disponibles:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (
                "Esa hora está ocupada 😕.\n\n"
                "No encontré otra cercana. "
                "Podemos buscar otro día."
            )

        # ----------------------------------------------------
        # GUARDAR FECHA
        # ----------------------------------------------------

        datos[
            "fecha_hora"
        ] = fecha.isoformat()

        return (
            "¡Perfecto! 🙌\n\n"
            f"Hay disponibilidad el "
            f"{formato_fecha(fecha)}.\n\n"
            "¿Me indicas tu nombre?"
        )

    # --------------------------------------------------------
    # 3. NOMBRE
    # --------------------------------------------------------

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? 😊"
            )

        datos[
            "nombre"
        ] = texto

        # ----------------------------------------------------
        # TELÉFONO DESDE WHATSAPP
        # ----------------------------------------------------

        telefono = datos.get(
            "telefono"
        )

        if telefono:

            return crear_reserva_final(
                estado
            )

        return (
            f"Perfecto, {texto} 👍\n\n"
            "¿Cuál es tu número de teléfono?"
        )

    # --------------------------------------------------------
    # 4. TELÉFONO
    # --------------------------------------------------------

    if not datos["telefono"]:

        datos[
            "telefono"
        ] = texto

        return crear_reserva_final(
            estado
        )

    return crear_reserva_final(
        estado
    )


# ============================================================
# CREAR RESERVA FINAL
# ============================================================

def crear_reserva_final(
    estado
):

    datos = estado[
        "datos_reserva"
    ]

    try:

        inicio = datetime.fromisoformat(
            datos["fecha_hora"]
        )

        servicio = obtener_servicio(
            datos["servicio"]
        )

        # SEGUNDA VERIFICACIÓN
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

            datos[
                "fecha_hora"
            ] = None

            return (
                "Justo esa hora se ocupó mientras "
                "terminábamos la reserva 😕.\n\n"
                "Dime otra hora y vuelvo a revisar."
            )

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
                "en este momento 😕.\n\n"
                "Puedes intentarlo nuevamente."
            )

        nombre = datos[
            "nombre"
        ]

        telefono = datos[
            "telefono"
        ]

        fecha_texto = formato_fecha(
            inicio
        )

        servicio_nombre = servicio[
            "nombre"
        ]

        # ----------------------------------------------------
        # LIMPIAR RESERVA
        # ----------------------------------------------------

        estado[
            "datos_reserva"
        ] = nueva_reserva(
            telefono
        )

        estado[
            "modo_agendar"
        ] = False

        return (
            "✅ ¡Reserva confirmada!\n\n"

            f"✂️ Servicio: {servicio_nombre}\n"

            f"👤 Cliente: {nombre}\n"

            f"📞 Teléfono: {telefono}\n"

            f"📅 {fecha_texto}\n"

            "⏱️ Duración: 1 hora\n\n"

            f"Tu hora quedó agendada directamente "
            f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"

            "¡Te esperamos! 🙌"
        )

    except Exception as e:

        print(
            "FINAL RESERVATION ERROR:",
            repr(e)
        )

        return (
            "Ocurrió un problema al finalizar "
            "la reserva 😕."
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

        session[
            "historial"
        ] = [

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

        session[
            "modo_agendar"
        ] = False

    if "datos_reserva" not in session:

        session[
            "datos_reserva"
        ] = nueva_reserva()

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

            historial_anterior = list(
                session[
                    "historial"
                ]
            )

            # ------------------------------------------------
            # DESPEDIDA
            # ------------------------------------------------

            if es_fin_conversacion(
                pregunta
            ):

                respuesta = (
                    "¡Perfecto! 😊 "
                    "Fue un gusto atenderte. "
                    "Cuando necesites una hora con Diego, "
                    "aquí estaré. ¡Que estés muy bien! 👋"
                )

                session[
                    "modo_agendar"
                ] = False

                session[
                    "datos_reserva"
                ] = nueva_reserva()

            # ------------------------------------------------
            # CANCELAR RESERVA
            # ------------------------------------------------

            elif (
                session.get(
                    "modo_agendar"
                )
                and es_cancelacion_reserva(
                    pregunta
                )
            ):

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

            # ------------------------------------------------
            # SI ESTÁ RESERVANDO
            # ------------------------------------------------

            elif session.get(
                "modo_agendar"
            ):

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

            # ------------------------------------------------
            # NUEVA INTENCIÓN DE RESERVA
            # ------------------------------------------------

            elif es_intencion_agendar(
                pregunta
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

            # ------------------------------------------------
            # CONVERSACIÓN LIBRE
            # ------------------------------------------------

            else:

                respuesta = (
                    responder_openai(
                        historial_anterior,
                        pregunta
                    )
                )

            # ------------------------------------------------
            # HISTORIAL
            # ------------------------------------------------

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

            # Evitar sesiones gigantes
            session[
                "historial"
            ] = session[
                "historial"
            ][-20:]

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

        # ----------------------------------------------------
        # DEDUP
        # ----------------------------------------------------

        ahora_timestamp = (
            datetime.now()
            .timestamp()
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

        # ----------------------------------------------------
        # SESIÓN
        # ----------------------------------------------------

        estado = get_wa_session(
            wa_id
        )

        historial_anterior = list(
            estado[
                "historial"
            ]
        )

        # ----------------------------------------------------
        # DESPEDIDA
        # ----------------------------------------------------

        if es_fin_conversacion(
            text
        ):

            respuesta = (
                "¡Perfecto! 😊 "
                "Fue un gusto atenderte. "
                "Cuando necesites una hora con Diego, "
                "aquí estaré. ¡Que estés muy bien! 👋"
            )

            estado[
                "modo_agendar"
            ] = False

            estado[
                "datos_reserva"
            ] = nueva_reserva(
                wa_id
            )

        # ----------------------------------------------------
        # RESERVA ACTIVA
        # ----------------------------------------------------

        elif estado[
            "modo_agendar"
        ]:

            estado_local = estado

            respuesta = (
                procesar_reserva(
                    estado_local,
                    text
                )
            )

        # ----------------------------------------------------
        # NUEVA RESERVA
        # ----------------------------------------------------

        elif es_intencion_agendar(
            text
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

        # ----------------------------------------------------
        # CONVERSACIÓN LIBRE
        # ----------------------------------------------------

        else:

            respuesta = (
                responder_openai(
                    historial_anterior,
                    text
                )
            )

        # ----------------------------------------------------
        # HISTORIAL
        # ----------------------------------------------------

        estado[
            "historial"
        ].append({

            "role":
                "user",

            "content":
                text,
        })

        estado[
            "historial"
        ].append({

            "role":
                "assistant",

            "content":
                respuesta,
        })

        estado[
            "historial"
        ] = estado[
            "historial"
        ][-20:]

        # ----------------------------------------------------
        # ENVIAR
        # ----------------------------------------------------

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
            bool(flow.code_verifier)
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
                "Vuelve a /admin/login."
            )

        if not code_verifier:

            raise Exception(
                "Se perdió el code_verifier OAuth. "
                "Vuelve a /admin/login."
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
Busca:
<b>GOOGLE_REFRESH_TOKEN</b>
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
