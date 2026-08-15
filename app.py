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
# Cada reserva dura 1 hora.
#
# Último inicio permitido = 17:00
#
# 17:00 -> 18:00
#
# 18:00 NO es inicio porque terminaría a las 19:00.

HORAS_DISPONIBLES = list(
    range(
        HORA_APERTURA,
        HORA_CIERRE
    )
)

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
# SERVICIO
# ============================================================

def obtener_servicio(
    codigo
):

    return SERVICIOS.get(
        codigo,
        SERVICIOS["otro"]
    )


def detectar_servicio(
    texto
):

    texto = (
        texto or ""
    ).lower()

    # Corte + barba primero
    if (
        (
            "corte" in texto
            and "barba" in texto
        )
        or "corte y barba" in texto
    ):
        return "corte_barba"

    if (
        "corte de niño" in texto
        or "corte niño" in texto
        or "corte nino" in texto
        or "niño" in texto
        or "nino" in texto
    ):
        return "corte_nino"

    if (
        "perfilado" in texto
        or "perfil" in texto
    ):
        return "perfilado"

    if "barba" in texto:
        return "barba"

    if (
        "corte" in texto
        or "peluquería" in texto
        or "peluqueria" in texto
    ):
        return "corte"

    return None


# ============================================================
# GOOGLE CALENDAR
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

        # Debe ser día de atención
        if not es_dia_atencion(inicio):
            return False

        # Debe ser hora exacta
        if inicio.minute != 0 or inicio.second != 0:
            return False

        # Inicio entre 10 y 17
        if (
            inicio.hour < HORA_APERTURA
            or inicio.hour >= HORA_CIERRE
        ):
            return False

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
        )

        # Nunca permitir terminar después de las 18
        if (
            fin.hour > HORA_CIERRE
            or (
                fin.hour == HORA_CIERRE
                and (
                    fin.minute > 0
                    or fin.second > 0
                )
            )
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
# GENERAR HORAS
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

    if not es_dia_atencion(
        fecha
    ):
        return []

    horas = []

    for hora in HORAS_DISPONIBLES:

        inicio = fecha.replace(

            hour=hora,

            minute=0,

            second=0,

            microsecond=0
        )

        horas.append(
            inicio
        )

    return horas


# ============================================================
# PRÓXIMAS HORAS DE UN DÍA
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

    if not es_dia_atencion(
        fecha
    ):
        return []

    if desde_hora is None:
        desde_hora = HORA_APERTURA

    resultados = []

    for hora in HORAS_DISPONIBLES:

        if hora < desde_hora:
            continue

        inicio = fecha.replace(

            hour=hora,

            minute=0,

            second=0,

            microsecond=0
        )

        # No ofrecer horas pasadas
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


# ============================================================
# PRÓXIMAS HORAS / DÍAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
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

            if ahora.minute > 0:
                desde_hora = ahora.hour + 1
            else:
                desde_hora = ahora.hour

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


def formato_hora_corta(
    fecha
):

    return fecha.strftime(
        "%H:%M"
    )


def formato_opciones(
    horas
):

    if not horas:
        return ""

    lineas = []

    for hora in horas:

        lineas.append(
            f"• {formato_fecha(hora)}"
        )

    return "\n".join(
        lineas
    )


# ============================================================
# PARSER DE FECHA/HORA
# ============================================================

def normalizar_texto_fecha(
    texto
):

    texto = (
        texto or ""
    ).strip()
    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto


def detectar_hora_explicita(
    texto
):

    """
    Detecta horas como:

    3
    3:00
    15:00
    3 pm
    3 p.m.
    3 de la tarde
    5 de la tarde
    10 am
    10 de la mañana

    Retorna:
        (hora, minuto)
    o:
        None
    """

    texto = (
        texto or ""
    ).lower()

    texto = texto.replace(
        "p. m.",
        "pm"
    )

    texto = texto.replace(
        "p.m.",
        "pm"
    )

    texto = texto.replace(
        "a. m.",
        "am"
    )

    texto = texto.replace(
        "a.m.",
        "am"
    )

    # 3:30 pm
    patron = re.search(
        r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)\b",
        texto
    )

    if patron:

        hora = int(
            patron.group(1)
        )

        minuto = int(
            patron.group(2)
            or 0
        )

        periodo = patron.group(3)

        if hora < 1 or hora > 12:
            return None

        if periodo == "am":

            if hora == 12:
                hora = 0

        else:

            if hora != 12:
                hora += 12

        return (
            hora,
            minuto
        )

    # 3 de la tarde
    patron = re.search(
        r"\b(\d{1,2})(?:[:.](\d{2}))?\s+"
        r"(?:de\s+la\s+)?"
        r"(mañana|tarde|noche)\b",
        texto
    )

    if patron:

        hora = int(
            patron.group(1)
        )

        minuto = int(
            patron.group(2)
            or 0
        )

        periodo = patron.group(3)

        if hora < 1 or hora > 12:
            return None

        if periodo == "mañana":

            if hora == 12:
                hora = 0

        else:

            if hora != 12:
                hora += 12

        return (
            hora,
            minuto
        )

    # Hora con minutos:
    # 15:30
    patron = re.search(
        r"\b(\d{1,2}):(\d{2})\b",
        texto
    )

    if patron:

        hora = int(
            patron.group(1)
        )

        minuto = int(
            patron.group(2)
        )

        if (
            0 <= hora <= 23
            and 0 <= minuto <= 59
        ):

            return (
                hora,
                minuto
            )

    return None


def detectar_dia_numerico(
    texto
):

    """
    Detecta días:

    17
    el 17
    día 17
    17 de agosto

    Evita confundir una hora simple
    con un día cuando sea posible.
    """

    texto = (
        texto or ""
    ).lower()

    patron = re.search(
        r"(?:^|\s)"
        r"(?:el\s+|día\s+)?"
        r"(\d{1,2})"
        r"(?:\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|"
        r"julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre))?"
        r"(?:\s|$)",
        texto
    )

    if not patron:
        return None

    numero = int(
        patron.group(1)
    )

    if numero < 1 or numero > 31:
        return None

    mes = patron.group(2)

    return (
        numero,
        mes
    )


def contiene_fecha_relativa(
    texto
):

    texto = (
        texto or ""
    ).lower()

    palabras = [

        "hoy",
        "mañana",
        "pasado mañana",
        "pasado manana",
        "lunes",
        "martes",
        "miércoles",
        "miercoles",
        "jueves",
        "viernes",
        "sábado",
        "sabado",
        "domingo",
        "próximo",
        "proximo",
        "próxima",
        "proxima",
    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


def parse_fecha_hora_generica(
    texto,
    referencia=None
):

    """
    Parser genérico.

    Intenta interpretar:

    - mañana a las 3
    - lunes a las 15
    - el 17 a las 3
    - 17 de agosto a las 15
    - próximo lunes a las 3 pm
    - 20/08 a las 10
    - 20-08-2026 10:00

    Retorna datetime o None.
    """

    texto = normalizar_texto_fecha(
        texto
    )

    zona = pytz.timezone(
        TIMEZONE
    )

    if referencia is None:

        referencia = datetime.now(
            zona
        )

    if referencia.tzinfo is None:

        referencia = zona.localize(
            referencia
        )

    else:

        referencia = referencia.astimezone(
            zona
        )

    # ========================================================
    # PRIMERO: dateparser
    # ========================================================

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
                    referencia,

                "DATE_ORDER":
                    "DMY",
            },
        )

        if resultado:

            if resultado.tzinfo is None:

                resultado = zona.localize(
                    resultado
                )

            else:

                resultado = (
                    resultado.astimezone(
                        zona
                    )
                )

            return resultado

    except Exception as e:

        print(
            "dateparser error:",
            repr(e)
        )


    # ========================================================
    # FALLBACK MANUAL DE HORA
    # ========================================================

    hora_info = detectar_hora_explicita(
        texto
    )

    if not hora_info:
        return None

    hora, minuto = hora_info

    # ========================================================
    # RELATIVOS
    # ========================================================

    texto_lower = texto.lower()

    if "pasado mañana" in texto_lower:

        fecha = (
            referencia.date()
            + timedelta(days=2)
        )

        return zona.localize(
            datetime(
                fecha.year,
                fecha.month,
                fecha.day,
                hora,
                minuto
            )
        )

    if "pasado manana" in texto_lower:

        fecha = (
            referencia.date()
            + timedelta(days=2)
        )

        return zona.localize(
            datetime(
                fecha.year,
                fecha.month,
                fecha.day,
                hora,
                minuto
            )
        )

    if "mañana" in texto_lower:

        fecha = (
            referencia.date()
            + timedelta(days=1)
        )

        return zona.localize(
            datetime(
                fecha.year,
                fecha.month,
                fecha.day,
                hora,
                minuto
            )
        )

    if "hoy" in texto_lower:

        fecha = referencia.date()

        return zona.localize(
            datetime(
                fecha.year,
                fecha.month,
                fecha.day,
                hora,
                minuto
            )
        )


    # ========================================================
    # DÍA DE LA SEMANA
    # ========================================================

    dias_texto = {

        "lunes": 0,
        "martes": 1,
        "miércoles": 2,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sábado": 5,
        "sabado": 5,
        "domingo": 6,
    }

    dia_encontrado = None

    for nombre, indice in dias_texto.items():

        if nombre in texto_lower:

            dia_encontrado = indice
            break

    if dia_encontrado is not None:

        diferencia = (
            dia_encontrado
            - referencia.weekday()
        ) % 7

        # Si dice "próximo lunes",
        # no usar hoy aunque sea lunes.
        if (
            "próximo" in texto_lower
            or "proximo" in texto_lower
            or "próxima" in texto_lower
            or "proxima" in texto_lower
        ):

            if diferencia == 0:
                diferencia = 7

        elif diferencia == 0:

            # "lunes" sin fecha:
            # si ya pasó la hora, próximo lunes.
            fecha_hoy_hora = (
                referencia.replace(
                    hour=hora,
                    minute=minuto,
                    second=0,
                    microsecond=0
                )
            )

            if fecha_hoy_hora <= referencia:

                diferencia = 7

        fecha = (
            referencia.date()
            + timedelta(
                days=diferencia
            )
        )

        return zona.localize(
            datetime(
                fecha.year,
                fecha.month,
                fecha.day,
                hora,
                minuto
            )
        )


    # ========================================================
    # DÍA NUMÉRICO
    # ========================================================

    dia_info = detectar_dia_numerico(
        texto
    )

    if dia_info:

        dia, mes_nombre = dia_info

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

        if mes_nombre:

            mes = meses.get(
                mes_nombre
            )

            if not mes:
                return None

        else:

            mes = referencia.month

        año = referencia.year

        try:

            fecha = datetime(
                año,
                mes,
                dia,
                hora,
                minuto
            )

            resultado = zona.localize(
                fecha
            )

            # Si la fecha ya pasó y no especificó
            # mes, asumimos el próximo mes.
            if (
                not mes_nombre
                and resultado <= referencia
            ):

                if mes == 12:

                    año += 1
                    mes = 1

                else:

                    mes += 1

                resultado = zona.localize(
                    datetime(
                        año,
                        mes,
                        dia,
                        hora,
                        minuto
                    )
                )

            return resultado

        except ValueError:

            return None


    return None


# ============================================================
# DETECTAR SI FALTA FECHA/HORA
# ============================================================

def tiene_fecha(
    texto
):

    texto = (
        texto or ""
    ).lower()

    if contiene_fecha_relativa(
        texto
    ):
        return True

    if re.search(
        r"\b\d{1,2}\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|"
        r"julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre)\b",
        texto
    ):
        return True

    if re.search(
        r"\b(?:el\s+|día\s+)?\d{1,2}\b",
        texto
    ):
        return True

    return False


def tiene_hora(
    texto
):

    return (
        detectar_hora_explicita(
            texto
        )
        is not None
    )


# ============================================================
# INTENCIÓN DE AGENDAR
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
        "reservación",
        "reservacion",
        "cita",
        "hora",
        "turno",
        "quiero una hora",
        "quiero hora",
        "pedir hora",
        "tomar hora",
        "disponibilidad",
        "disponible",
        "corte",
        "barba",
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
# OPENAI
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""

Eres el Asistente Virtual de Estilista Diego.

Tu función es atender conversacionalmente a los clientes
y ayudarlos principalmente a agendar una hora.

NO eres LaOrtiga.

El negocio es:
{NEGOCIO_NOMBRE}

El estilista es:
{ESTILISTA_NOMBRE}

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Cada atención ocupa EXACTAMENTE 1 HORA.

Las horas de inicio posibles son:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00

18:00 NO es una hora de inicio.

Los domingos no hay atención.

SERVICIOS:

- Corte de cabello
- Corte + barba
- Arreglo de barba
- Corte de niño
- Perfilado
- Otro servicio

Puedes conversar libremente con el cliente.

El cliente puede preguntarte cosas sobre:
- servicios
- horarios
- reservas
- cómo funciona la atención
- precios si están disponibles
- disponibilidad
- información general

Sé:
- amable
- natural
- cercano
- profesional
- breve
- español de Chile

Si el cliente demuestra interés en agendar,
guíalo progresivamente.

Para reservar necesitas:

1. Servicio
2. Fecha
3. Hora
4. Nombre
5. Teléfono

Nunca inventes disponibilidad.

La disponibilidad real la comprueba el sistema
consultando Google Calendar.

El calendario pertenece exclusivamente a Diego.

El cliente NO necesita Google Calendar.

Si el cliente pregunta por disponibilidad,
el sistema debe consultar Calendar antes de afirmar
que una hora está disponible.

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

                model="gpt-4o-mini",

                messages=mensajes,

                max_tokens=400,

                temperature=0.6,
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
            repr(e)
        )

        return (
            "Ups 😅 tuve un pequeño problema "
            "técnico. ¿Me puedes repetir?"
        )


# ============================================================
# RESPUESTA PARA ALTERNATIVAS
# ============================================================

def responder_horas_alternativas(
    horas,
    encabezado
):

    if not horas:

        return (
            f"{encabezado}\n\n"
            "No encontré disponibilidad cercana.\n"
            "¿Quieres probar otro día?"
        )

    opciones = []

    for hora in horas:

        opciones.append(
            f"• {formato_fecha(hora)}"
        )

    return (
        f"{encabezado}\n\n"
        "Puedo ofrecerte estas horas:\n\n"
        + "\n".join(opciones)
        + "\n\n¿Cuál te acomoda?"
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
    # 1. DETECTAR SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio_detectado = detectar_servicio(
            texto
        )

        if servicio_detectado:

            datos["servicio"] = (
                servicio_detectado
            )

        else:

            return (

                "Claro ✂️ Te ayudo a reservar.\n\n"

                "¿Qué servicio quieres?\n\n"

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

        zona = pytz.timezone(
            TIMEZONE
        )

        ahora = datetime.now(
            zona
        )

        fecha_detectada = None

        # Intentamos interpretar fecha/hora
        if (
            tiene_fecha(texto)
            and tiene_hora(texto)
        ):

            fecha_detectada = (
                parse_fecha_hora_generica(
                    texto,
                    ahora
                )
            )

        # Si tiene fecha pero no hora
        elif tiene_fecha(texto):

            # Guardamos temporalmente el día
            try:

                resultado_fecha = (
                    dateparser.parse(

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
                        }
                    )
                )

                if resultado_fecha:

                    if resultado_fecha.tzinfo is None:

                        resultado_fecha = (
                            zona.localize(
                                resultado_fecha
                            )
                        )

                    else:

                        resultado_fecha = (
                            resultado_fecha.astimezone(
                                zona
                            )
                        )

                    datos[
                        "fecha_pendiente"
                    ] = resultado_fecha.isoformat()

            except Exception as e:

                print(
                    "Error fecha:",
                    repr(e)
                )

            return (

                "Perfecto 👍\n\n"

                "¿A qué hora te gustaría venir?\n\n"

                "Puedes decirme, por ejemplo:\n"
                "• 10:00\n"
                "• 3 de la tarde\n"
                "• 17:00"
            )

        # Si tiene hora pero no fecha
        elif tiene_hora(texto):

            datos[
                "hora_pendiente"
            ] = texto

            return (

                "Perfecto 👍 ¿Para qué día "
                "te gustaría agendar?\n\n"

                "Por ejemplo:\n"
                "• mañana\n"
                "• el viernes\n"
                "• el 17 de agosto"
            )

        else:

            return (

                "Perfecto ✂️\n\n"

                "¿Para qué día y hora "
                "te gustaría reservar?\n\n"

                "Por ejemplo:\n"
                "\"el viernes a las 15:00\"\n"
                "\"mañana a las 3 de la tarde\"\n"
                "\"el 17 a las 11\""
            )


        # ====================================================
        # SI HABÍA FECHA PENDIENTE
        # ====================================================

        if (
            not fecha_detectada
            and datos.get(
                "fecha_pendiente"
            )
            and tiene_hora(texto)
        ):

            try:

                fecha_base = datetime.fromisoformat(
                    datos[
                        "fecha_pendiente"
                    ]
                )

                hora_info = detectar_hora_explicita(
                    texto
                )

                if hora_info:

                    hora, minuto = hora_info

                    fecha_detectada = (
                        fecha_base.replace(
                            hour=hora,
                            minute=minuto,
                            second=0,
                            microsecond=0
                        )
                    )

            except Exception as e:

                print(
                    "Error combinando fecha/hora:",
                    repr(e)
                )


        # ====================================================
        # SI HABÍA HORA PENDIENTE
        # ====================================================

        if (
            not fecha_detectada
            and datos.get(
                "hora_pendiente"
            )
            and tiene_fecha(texto)
        ):

            try:

                hora_info = detectar_hora_explicita(
                    datos[
                        "hora_pendiente"
                    ]
                )

                fecha_base = (
                    parse_fecha_hora_generica(
                        texto,
                        ahora
                    )
                )

                if (
                    hora_info
                    and fecha_base
                ):

                    hora, minuto = hora_info

                    fecha_detectada = (
                        fecha_base.replace(
                            hour=hora,
                            minute=minuto,
                            second=0,
                            microsecond=0
                        )
                    )

            except Exception as e:

                print(
                    "Error combinando:",
                    repr(e)
                )


        # ====================================================
        # NO SE PUDO INTERPRETAR
        # ====================================================

        if not fecha_detectada:

            return (

                "No logré identificar bien "
                "la fecha y hora 😅.\n\n"

                "Puedes decirme algo como:\n"
                "\"el lunes a las 15:00\"\n"
                "\"mañana a las 4 pm\"\n"
                "\"el 17 de agosto a las 3\""
            )


        fecha = fecha_detectada.astimezone(
            zona
        )


        # ====================================================
        # VALIDAR MINUTOS
        # ====================================================

        if fecha.minute != 0:

            proximas = (
                buscar_proximas_horas(
                    fecha,
                    cantidad=5
                )
            )

            return responder_horas_alternativas(

                proximas,

                "Las reservas comienzan solamente "
                "en horas exactas 🕐."
            )


        # ====================================================
        # VALIDAR DOMINGO
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

            return responder_horas_alternativas(

                proximas,

                "El domingo no tenemos atención 😕."
            )


        # ====================================================
        # VALIDAR HORARIO
        # ====================================================

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour >= HORA_CIERRE
        ):

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    cantidad=5
                )
            )

            return responder_horas_alternativas(

                proximas,

                (
                    "Ese horario está fuera de "
                    "nuestro horario de atención 😕.\n\n"
                    "Atendemos de lunes a sábado "
                    "de 10:00 a 18:00."
                )
            )


        # ====================================================
        # VALIDAR PASADO
        # ====================================================

        if fecha <= ahora:

            proximas = (
                buscar_proximas_horas(

                    fecha,

                    cantidad=5
                )
            )

            return responder_horas_alternativas(

                proximas,

                "Esa hora ya pasó 😕."
            )


        # ====================================================
        # VERIFICAR CALENDAR
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
                buscar_horas_disponibles(

                    fecha,

                    cantidad=5,

                    desde_hora=fecha.hour + 1
                )
            )

            # Si no hay más horas ese día,
            # buscar días siguientes.
            if not proximas:

                proximas = (
                    buscar_proximas_horas(

                        fecha + timedelta(days=1),

                        cantidad=5
                    )
                )

            return responder_horas_alternativas(

                proximas,

                (
                    f"La hora del "
                    f"{formato_fecha(fecha)} "
                    f"ya está ocupada 😕."
                )
            )


        # ====================================================
        # GUARDAR FECHA/HORA
        # ====================================================

        datos["fecha_hora"] = (
            fecha.isoformat()
        )

        # Limpiar pendientes
        datos.pop(
            "fecha_pendiente",
            None
        )

        datos.pop(
            "hora_pendiente",
            None
        )


        # ====================================================
        # PEDIR NOMBRE
        # ====================================================

        return (

            "¡Perfecto! 🙌\n\n"

            f"Tengo disponible el "
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


    # ========================================================
    # 4. TELÉFONO
    # ========================================================

    if not datos.get(
        "telefono"
    ):

        return (
            f"Perfecto, {datos['nombre']} 👍\n\n"
            "¿Cuál es tu número de teléfono?"
        )


    # ========================================================
    # 5. SEGUNDA VERIFICACIÓN
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
            "la disponibilidad 😕.\n\n"

            "Por favor intenta nuevamente."
        )


    if not disponible:

        datos["fecha_hora"] = None

        proximas = (
            buscar_horas_disponibles(

                inicio,

                cantidad=5,

                desde_hora=inicio.hour + 1
            )
        )

        if not proximas:

            proximas = (
                buscar_proximas_horas(

                    inicio + timedelta(days=1),

                    cantidad=5
                )
            )

        return responder_horas_alternativas(

            proximas,

            "Justo esa hora se ocupó 😕."
        )


    # ========================================================
    # 6. CREAR EVENTO
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


    servicio = obtener_servicio(
        datos["servicio"]
    )

    nombre = datos["nombre"]

    telefono = datos["telefono"]

    fecha_texto = formato_fecha(
        inicio
    )


    # ========================================================
    # LIMPIAR
    # ========================================================

    telefono_guardado = (
        datos["telefono"]
    )

    estado["datos_reserva"] = {

        "servicio":
            None,

        "fecha_hora":
            None,

        "nombre":
            None,

        "telefono":
            telefono_guardado,
    }

    estado["modo_agendar"] = False


    return (

        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: "
        f"{servicio['nombre']}\n"

        f"👤 Cliente: "
        f"{nombre}\n"

        f"📞 Teléfono: "
        f"{telefono}\n"

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

                        "Puedes preguntarme lo que quieras "
                        "sobre los servicios o, si quieres, "
                        "te puedo ayudar a reservar una hora."
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


            # =================================================
            # DETERMINAR MODO
            # =================================================

            if (
                es_intencion_agendar(
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

                # Conversación libre
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


        # Ignorar estados
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
        # MODO CONVERSACIÓN / RESERVA
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
# CALLBACK
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

        debug=
            os.getenv(
                "FLASK_ENV"
            ) == "development"
    )
