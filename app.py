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

DIAS_NOMBRES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]

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
# ZONA HORARIA
# ============================================================

def obtener_zona():

    return pytz.timezone(
        TIMEZONE
    )


def ahora_local():

    zona = obtener_zona()

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

    for original, nuevo in reemplazos.items():

        texto = texto.replace(
            original,
            nuevo
        )

    return texto


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
        or "corte para nino" in texto_n
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


def obtener_servicio(
    codigo
):

    return SERVICIOS.get(
        codigo,
        SERVICIOS["otro"]
    )


# ============================================================
# DÍAS
# ============================================================

def es_dia_atencion(
    fecha
):

    zona = obtener_zona()

    fecha = fecha.astimezone(
        zona
    )

    return (
        fecha.weekday()
        in DIAS_ATENCION
    )


# ============================================================
# HORA
# ============================================================

def es_hora_atencion(
    fecha
):

    zona = obtener_zona()

    fecha = fecha.astimezone(
        zona
    )

    return (

        es_dia_atencion(fecha)

        and fecha.minute == 0

        and fecha.second == 0

        and HORA_APERTURA
        <= fecha.hour
        < HORA_CIERRE
    )


def horario_atencion_texto():

    return (
        "lunes a sábado, "
        "de 10:00 a 18:00 hrs"
    )


# ============================================================
# PRÓXIMO DÍA
# ============================================================

def proximo_dia_semana(
    fecha_base,
    weekday_objetivo,
    incluir_hoy=True
):

    zona = obtener_zona()

    fecha_base = fecha_base.astimezone(
        zona
    )

    diferencia = (
        weekday_objetivo
        - fecha_base.weekday()
    ) % 7

    if (
        diferencia == 0
        and not incluir_hoy
    ):
        diferencia = 7

    return (
        fecha_base
        + timedelta(days=diferencia)
    )


# ============================================================
# PARSEAR HORA
# ============================================================

def parse_hora_texto(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    # 10:00 / 15:30
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

        if hora > 23 or minuto > 59:
            return None

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

        if hora < 1 or hora > 12:
            return None

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


    # 15 hrs / 15 horas
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
# DETECTAR DÍA DE SEMANA
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
# DETECTAR DÍA/MES
# ============================================================

def detectar_dia_mes(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

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

        nombre_mes = match.group(2)

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

        return (
            dia,
            meses[nombre_mes]
        )


    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})\b",
        texto_n
    )

    if match:

        return (
            int(match.group(1)),
            int(match.group(2))
        )

    return None


# ============================================================
# SOLO DÍA
# ============================================================

def detectar_solo_dia(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    # Evitamos números que sean horas.
    match = re.search(
        r"\b(?:el\s+|dia\s+|día\s+)?"
        r"(\d{1,2})"
        r"(?:\s+de\s+este\s+mes)?\b",
        texto_n
    )

    if not match:
        return None

    dia = int(
        match.group(1)
    )

    if dia < 1 or dia > 31:
        return None

    if re.search(
        r"\ba\s+las?\s+" + str(dia) + r"\b",
        texto_n
    ):
        return None

    return dia


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


    # ========================================================
    # PASADO MAÑANA
    # IMPORTANTE: ANTES DE MAÑANA
    # ========================================================

    if re.search(
        r"\bpasado manana\b",
        texto_n
    ):

        fecha = (
            ahora
            + timedelta(days=2)
        )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # ========================================================
    # MAÑANA
    # ========================================================

    if re.search(
        r"\bmanana\b",
        texto_n
    ):

        fecha = (
            ahora
            + timedelta(days=1)
        )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # ========================================================
    # HOY
    # ========================================================

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


    # ========================================================
    # DÍA DE SEMANA
    # ========================================================

    weekday = detectar_dia_semana(
        texto
    )

    if weekday is not None:

        proximo = bool(
            re.search(
                r"\bproximo\b",
                texto_n
            )
        )

        siguiente = bool(
            re.search(
                r"\bsiguiente\b",
                texto_n
            )
        )

        fecha = proximo_dia_semana(

            ahora,

            weekday,

            incluir_hoy=not (
                proximo
                or siguiente
            )
        )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # ========================================================
    # DÍA + MES
    # ========================================================

    dia_mes = detectar_dia_mes(
        texto
    )

    if dia_mes:

        dia, mes = dia_mes
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

            try:

                fecha = datetime(
                    anio + 1,
                    mes,
                    dia,
                    tzinfo=zona
                )

            except ValueError:

                return None

        return fecha


    # ========================================================
    # SOLO DÍA
    # ========================================================

    solo_dia = detectar_solo_dia(
        texto
    )

    if solo_dia:

        anio = ahora.year
        mes = ahora.month

        try:

            fecha = datetime(
                anio,
                mes,
                solo_dia,
                tzinfo=zona
            )

        except ValueError:

            return None

        if fecha.date() < ahora.date():

            if mes == 12:

                anio += 1
                mes = 1

            else:

                mes += 1

            try:

                fecha = datetime(
                    anio,
                    mes,
                    solo_dia,
                    tzinfo=zona
                )

            except ValueError:

                return None

        return fecha


    return None


# ============================================================
# FECHA + HORA
# ============================================================

def parse_fecha_hora(
    texto
):

    zona = obtener_zona()
    ahora = ahora_local()

    texto = (
        texto or ""
    ).strip()

    if not texto:
        return None

    fecha = construir_fecha_desde_texto(
        texto
    )


    # ========================================================
    # DATEPARSER COMO FALLBACK
    # ========================================================

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
                },
            )

            if resultado:

                if resultado.tzinfo is None:

                    resultado = zona.localize(
                        resultado
                    )

                else:

                    resultado = (
                        resultado
                        .astimezone(zona)
                    )

                fecha = resultado

        except Exception as e:

            print(
                "dateparser error:",
                repr(e)
            )


    if fecha is None:
        return None


    hora_info = parse_hora_texto(
        texto
    )

    if hora_info:

        hora, minuto = hora_info

        try:

            fecha = fecha.replace(

                hour=hora,

                minute=minuto,

                second=0,

                microsecond=0
            )

        except ValueError:

            return None

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

    zona = obtener_zona()

    fecha = fecha.astimezone(
        zona
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

    zona = obtener_zona()

    fecha = fecha.astimezone(
        zona
    )

    return (

        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"a las {fecha.strftime('%H:%M')}"
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
# GOOGLE: DISPONIBILIDAD
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

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
        )

        if fin > inicio.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
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
            calendario
            .get(
                "busy",
                []
            )
        )

        return (
            len(bloques) == 0
        )

    except Exception as e:

        print(
            "Calendar availability error:",
            repr(e)
        )

        return None


# ============================================================
# HORAS DISPONIBLES
# ============================================================

def buscar_horas_disponibles(
    fecha,
    cantidad=5,
    desde_hora=None
):

    zona = obtener_zona()

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
                break

    return resultados


# ============================================================
# PRÓXIMAS HORAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
    cantidad=5,
    dias_maximos=14,
    desde_hora=None
):

    zona = obtener_zona()

    fecha_inicial = (
        fecha_inicial
        .astimezone(zona)
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

        hora_inicio_dia = HORA_APERTURA

        if offset == 0:

            if fecha.date() == ahora.date():

                hora_inicio_dia = (
                    ahora.hour + 1
                    if ahora.minute > 0
                    else ahora.hour
                )

                hora_inicio_dia = max(
                    hora_inicio_dia,
                    HORA_APERTURA
                )

        if (
            desde_hora is not None
            and offset == 0
        ):

            hora_inicio_dia = max(
                hora_inicio_dia,
                desde_hora
            )

        horas = buscar_horas_disponibles(

            fecha,

            cantidad=(
                cantidad
                - len(resultados)
            ),

            desde_hora=
                hora_inicio_dia
        )

        resultados.extend(
            horas
        )

        if len(resultados) >= cantidad:
            break

    return resultados[:cantidad]


# ============================================================
# INTENCIÓN REAL DE AGENDAR
# ============================================================

def es_intencion_agendar(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    # ========================================================
    # FRASES MUY CLARAS
    # ========================================================

    patrones_fuertes = [

        "quiero agendar",
        "quiero reservar",
        "quiero sacar hora",
        "quiero pedir hora",
        "quiero una hora",
        "me gustaria agendar",
        "me gustaria reservar",
        "me gustaría agendar",
        "me gustaría reservar",
        "reservame",
        "resérvame",
        "agendame",
        "agéndame",
        "puedo agendar",
        "puedo reservar",
        "necesito una hora",
        "necesito agendar",
        "necesito reservar",
        "sacar hora",
        "sacar una hora",
        "reservar hora",
        "agendar hora",
        "pedir hora",
        "hacer una reserva",
        "hacer reserva",
    ]

    if any(
        patron in texto_n
        for patron in patrones_fuertes
    ):
        return True


    # ========================================================
    # PREGUNTAS DE RESERVA
    # ========================================================

    patrones_reserva = [

        r"\bpuedo\s+(?:ir|venir)\b.*\b(?:hora|hoy|manana|lunes|martes|miercoles|jueves|viernes|sabado)\b",

        r"\b(?:tienes|hay|queda)\s+(?:alguna\s+)?hora\b",

        r"\b(?:tienes|hay|queda)\s+disponibilidad\b",

        r"\b(?:que|qué)\s+horas\s+(?:tienes|hay|quedan)\b",

        r"\bcuando\s+(?:tienes|hay)\s+hora\b",

        r"\bcuándo\s+(?:tienes|hay)\s+hora\b",

        r"\bque\s+dias\s+tienes\b",

        r"\bqué\s+días\s+tienes\b",
    ]

    for patron in patrones_reserva:

        if re.search(
            patron,
            texto_n
        ):
            return True


    # ========================================================
    # COMBINACIÓN SERVICIO + FECHA/HORA
    #
    # Esto permite:
    #
    # "corte mañana"
    # "barba el viernes"
    # "corte a las 3"
    #
    # Pero NO convierte simplemente "me gusta tu corte"
    # en una reserva.
    # ========================================================

    servicio = detectar_servicio(
        texto
    )

    tiene_fecha = (
        construir_fecha_desde_texto(
            texto
        )
        is not None
    )

    tiene_hora = (
        contiene_hora(
            texto
        )
    )

    if servicio and (
        tiene_fecha
        or tiene_hora
    ):
        return True


    return False


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

        "tienes disponibilidad",
        "hay disponibilidad",
        "que horas tienes",
        "que horas hay",
        "qué horas tienes",
        "qué horas hay",
        "horas libres",
        "horas disponibles",
        "hay hora",
        "tienes hora",
        "tiene hora",
        "queda hora",
        "queda alguna hora",
        "que dias tienes",
        "qué días tienes",
    ]

    return any(
        patron in texto_n
        for patron in patrones
    )


# ============================================================
# PROMPT OPENAI
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

IMPORTANTE:

Tu comportamiento debe sentirse como una conversación
natural con ChatGPT, pero orientada progresivamente
a ayudar al cliente con los servicios del estilista.

NO debes intentar agendar una hora en cada conversación.

Si el cliente simplemente saluda, conversa, pregunta
cómo estás o comenta algo, responde naturalmente.

EJEMPLO:

Cliente:
Hola

Asistente:
¡Hola! 👋 ¿Cómo estás?

Cliente:
Bien, ¿y tú?

Asistente:
¡Muy bien también! 😄 ¿Qué tal? ¿En qué te puedo ayudar?

NO respondas inmediatamente:
"¿Qué servicio quieres reservar?"

Otro ejemplo:

Cliente:
Me quedó muy bueno el corte anterior.

Asistente:
¡Qué bueno que te gustó! 😄 Me alegra mucho.

NO intentes reservar automáticamente.

Solo orienta la conversación hacia una reserva cuando
el cliente manifieste claramente que quiere reservar,
consultar disponibilidad o venir a atenderse.

PERSONALIDAD:

- chileno
- natural
- amable
- cercano
- simpático
- profesional
- breve
- humano
- conversacional

Evita respuestas excesivamente formales.

No digas:
"Estimado cliente".

Puedes usar:
"¡Claro!"
"Sí, obvio."
"Perfecto."
"Buenísimo."
"¡De una!"

HORARIO:

Lunes a sábado:
10:00 a 18:00.

Domingo:
cerrado.

Las reservas duran 1 hora.

Horas de inicio:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00

La última reserva comienza a las 17:00.

SERVICIOS:

- Corte de cabello
- Corte + barba
- Arreglo de barba
- Corte de niño
- Perfilado

IMPORTANTE SOBRE GOOGLE CALENDAR:

La disponibilidad real se comprueba directamente
contra Google Calendar.

Nunca inventes disponibilidad.

Nunca digas que una hora está libre si el sistema
no la ha comprobado.

Cuando el cliente entre al proceso de reserva,
el sistema se encargará de pedir:

1. Servicio
2. Fecha
3. Hora
4. Nombre
5. Teléfono

El cliente NO necesita iniciar sesión en Google.

La agenda corresponde exclusivamente a
{ESTILISTA_NOMBRE}.

HORARIO:
{horario_atencion_texto()}

ESTILISTA:
{ESTILISTA_NOMBRE}

Tu objetivo es entregar una experiencia de conversación
natural y luego ayudar a reservar cuando exista intención
real de hacerlo.

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
                "¡Claro! 😊 "
                "Cuéntame, ¿en qué te puedo ayudar?"
            )


        return respuesta.strip()


    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        return (
            "¡Claro! 😊 "
            "Cuéntame qué necesitas y te ayudo."
        )


# ============================================================
# PREGUNTAR SERVICIO
# ============================================================

def preguntar_servicio():

    return (

        "¡Claro! ✂️ ¿Qué servicio te gustaría reservar?\n\n"

        "• Corte de cabello\n"
        "• Corte + barba\n"
        "• Arreglo de barba\n"
        "• Corte de niño\n"
        "• Perfilado"
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
    # SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if servicio:

            datos["servicio"] = servicio

        else:

            return preguntar_servicio()


    servicio = obtener_servicio(
        datos["servicio"]
    )


    # ========================================================
    # FECHA Y HORA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )

        tiene_hora = contiene_hora(
            texto
        )


        # ----------------------------------------------------
        # NO HAY FECHA
        # ----------------------------------------------------

        if fecha is None:

            return (

                "Perfecto ✂️\n\n"

                "¿Qué día y a qué hora "
                "te gustaría venir?\n\n"

                "Por ejemplo:\n"
                "• mañana a las 10\n"
                "• el lunes a las 15:00\n"
                "• el 20 a las 3 pm\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ----------------------------------------------------
        # FECHA SIN HORA
        # ----------------------------------------------------

        if not tiene_hora:

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

                        "Ese día no tenemos "
                        "atención 😕.\n\n"

                        "Te puedo ofrecer estas "
                        "próximas horas:\n\n"

                        f"{formato_horas(proximas)}\n\n"

                        "¿Cuál te acomoda?"
                    )

                return (

                    "Ese día no tenemos "
                    "atención 😕.\n\n"

                    f"Atendemos "
                    f"{horario_atencion_texto()}."
                )


            horas = (
                buscar_horas_disponibles(

                    fecha,

                    cantidad=5
                )
            )

            if horas:

                return (

                    f"Perfecto 👍 Para el "
                    f"{formato_fecha_corta(fecha)} "
                    "tengo estas horas disponibles:\n\n"

                    f"{formato_horas(horas)}\n\n"

                    "¿Cuál prefieres?"
                )


            proximas = (
                buscar_proximas_horas(

                    fecha,

                    cantidad=5
                )
            )

            if proximas:

                return (

                    "Ese día ya no tengo "
                    "horas disponibles 😕.\n\n"

                    "Te puedo ofrecer:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )


            return (
                "No encontré disponibilidad "
                "cercana 😕.\n\n"
                "¿Quieres probar con otro día?"
            )


        # ----------------------------------------------------
        # DÍA
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

                    "Ese día no tenemos "
                    "atención 😕.\n\n"

                    "Te puedo ofrecer:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                "Ese día no tenemos "
                "atención 😕.\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
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

                    "Las reservas son por "
                    "hora exacta 🕐.\n\n"

                    "Las horas son:\n"
                    "10:00, 11:00, 12:00, "
                    "13:00, 14:00, 15:00, "
                    "16:00 y 17:00.\n\n"

                    "Estas están disponibles:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál prefieres?"
                )

            return (
                "Las reservas son por "
                "hora exacta 🕐."
            )


        # ----------------------------------------------------
        # HORARIO
        # ----------------------------------------------------

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour >= HORA_CIERRE
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

                    "Ese horario está fuera "
                    "de nuestro horario 😕.\n\n"

                    f"Atendemos "
                    f"{horario_atencion_texto()}.\n\n"

                    "Tengo estas próximas horas:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (
                f"Nuestro horario es "
                f"{horario_atencion_texto()}."
            )


        # ----------------------------------------------------
        # FIN DE RESERVA
        # ----------------------------------------------------

        fin = (
            fecha
            + timedelta(
                minutes=DURACION_RESERVA
            )
        )

        if fin > fecha.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        ):

            return (

                "Esa hora no permite completar "
                "una atención de 1 hora 😕.\n\n"

                "La última hora de inicio es "
                "a las 17:00."
            )


        # ----------------------------------------------------
        # PASADO
        # ----------------------------------------------------

        ahora = ahora_local()

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

                    "Te puedo ofrecer:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (
                "Esa hora ya pasó 😕.\n\n"
                "Dime otro día y horario."
            )


        # ----------------------------------------------------
        # GOOGLE
        # ----------------------------------------------------

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


        # ----------------------------------------------------
        # OCUPADA
        # ----------------------------------------------------

        if not disponible:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    cantidad=5,

                    dias_maximos=14,

                    desde_hora=fecha.hour
                )
            )

            if proximas:

                return (

                    "Esa hora ya está ocupada 😕.\n\n"

                    "Te puedo ofrecer:\n\n"

                    f"{formato_horas(proximas)}\n\n"

                    "¿Cuál prefieres?"
                )

            return (
                "Esa hora está ocupada 😕.\n\n"
                "¿Quieres probar con otro día?"
            )


        # ----------------------------------------------------
        # GUARDAR FECHA
        # ----------------------------------------------------

        datos["fecha_hora"] = (
            fecha.isoformat()
        )


        # ----------------------------------------------------
        # NOMBRE
        # ----------------------------------------------------

        return (

            "¡Perfecto! 🙌\n\n"

            f"Tengo disponible "
            f"{formato_fecha(fecha)}.\n\n"

            "¿Me indicas tu nombre?"
        )


    # ========================================================
    # NOMBRE
    # ========================================================

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )

        datos["nombre"] = texto

        telefono_actual = (
            datos.get("telefono")
        )

        if telefono_actual:

            return completar_reserva(
                estado
            )

        return (

            f"Perfecto, {datos['nombre']} 👍\n\n"

            "¿Cuál es tu número de teléfono?"
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto


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

        return (
            "Me falta confirmar el día "
            "y la hora 😊."
        )

    if not datos["servicio"]:

        return (
            "Me falta saber qué servicio "
            "quieres reservar ✂️."
        )

    if not datos["nombre"]:

        return (
            "Me falta tu nombre 😊."
        )

    if not datos["telefono"]:

        return (
            "Me falta tu teléfono 📞."
        )


    try:

        inicio = datetime.fromisoformat(
            datos["fecha_hora"]
        )

    except Exception:

        datos["fecha_hora"] = None

        return (
            "Necesito volver a confirmar "
            "el día y la hora 😊."
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
            "la disponibilidad 😕.\n\n"
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

    fecha_texto = formato_fecha(
        inicio
    )

    servicio_nombre = servicio[
        "nombre"
    ]


    # ========================================================
    # MANTENER TELÉFONO
    # ========================================================

    telefono_guardar = telefono


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
            telefono_guardar,
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

        "La atención dura 1 hora.\n\n"

        "¡Te esperamos! 🙌"
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

                    f"Duración: "
                    f"{duracion} minutos\n"

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
                        str(duracion),

                    "origen":
                        f"Asistente Virtual {ESTILISTA_NOMBRE}",
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


    # ========================================================
    # HISTORIAL
    # ========================================================

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
                        "¿Cómo estás? "
                        "¿En qué te puedo ayudar?"
                    ),
            }
        ]


    # ========================================================
    # MODO AGENDA
    # ========================================================

    if "modo_agendar" not in session:

        session["modo_agendar"] = False


    # ========================================================
    # DATOS
    # ========================================================

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

            iniciar_reserva = (

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
            )


            if iniciar_reserva:

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

                # =================================================
                # CONVERSACIÓN NORMAL
                # =================================================

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
            data.get(
                "entry"
            )
            or []
        )

        if not entry:
            return "ok", 200

        entry = entry[0]


        changes = (
            entry.get(
                "changes"
            )
            or []
        )

        if not changes:
            return "ok", 200

        changes = changes[0]


        value = (
            changes.get(
                "value"
            )
            or {}
        )


        # ====================================================
        # ESTADOS
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

            msg.get(
                "text"
            )
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
        # INTENCIÓN
        # ====================================================

        iniciar_reserva = (

            es_intencion_agendar(
                text
            )

            or pregunta_disponibilidad(
                text
            )

            or estado[
                "modo_agendar"
            ]
        )


        if iniciar_reserva:

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

<li>Pega el token anterior.</li>

<li>Guarda los cambios.</li>

<li>Espera el nuevo deploy.</li>

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

        debug=(
            os.getenv(
                "FLASK_ENV"
            )
            == "development"
        )
    )
