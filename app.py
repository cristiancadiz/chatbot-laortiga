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
# TODOS $20.000
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

    "otro": {
        "nombre": "Otro servicio",
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
    }

    for original, nuevo in reemplazos.items():

        texto = texto.replace(
            original,
            nuevo
        )

    return texto


# ============================================================
# FORMATO PRECIO
# ============================================================

def formato_precio(valor):

    return (
        "$"
        + f"{valor:,}".replace(",", ".")
    )


# ============================================================
# SERVICIOS TEXTO
# ============================================================

def servicios_texto():

    return (

        "✂️ Nuestros servicios:\n\n"

        "1. Corte de cabello — $20.000\n"
        "2. Corte + barba — $20.000\n"
        "3. Arreglo de barba — $20.000\n"
        "4. Corte de niño — $20.000\n"
        "5. Perfilado — $20.000\n\n"

        "Todos los servicios tienen una "
        "duración aproximada de 1 hora."
    )


def servicios_corto():

    return (

        "✂️ Tenemos:\n\n"

        "• Corte de cabello — $20.000\n"
        "• Corte + barba — $20.000\n"
        "• Arreglo de barba — $20.000\n"
        "• Corte de niño — $20.000\n"
        "• Perfilado — $20.000\n\n"

        "¿Cuál te interesa?"
    )


# ============================================================
# DETECTAR SERVICIO
# ============================================================

def detectar_servicio(texto):

    texto_n = normalizar_texto(
        texto
    )

    # Corte + barba primero
    if (
        "corte" in texto_n
        and "barba" in texto_n
    ):
        return "corte_barba"

    # Corte niño
    if (
        "corte de nino" in texto_n
        or "corte nino" in texto_n
        or "cortar al nino" in texto_n
        or "corte para nino" in texto_n
    ):
        return "corte_nino"

    # Barba
    if "barba" in texto_n:
        return "barba"

    # Perfilado
    if (
        "perfilado" in texto_n
        or "perfil" in texto_n
    ):
        return "perfilado"

    # Corte
    if (
        "corte" in texto_n
        or "cortar" in texto_n
    ):
        return "corte"

    return None


def obtener_servicio(codigo):

    return SERVICIOS.get(
        codigo,
        SERVICIOS["otro"]
    )


# ============================================================
# DETECTAR SELECCIÓN DE SERVICIO 1-5
# ============================================================

def detectar_servicio_por_numero(texto):

    texto_n = normalizar_texto(
        texto
    )

    # Solo aceptamos número cuando
    # parece ser una selección.
    match = re.fullmatch(
        r"\s*([1-5])\s*[\.\)]?\s*",
        texto_n
    )

    if not match:
        return None

    numero = int(
        match.group(1)
    )

    mapa = {
        1: "corte",
        2: "corte_barba",
        3: "barba",
        4: "corte_nino",
        5: "perfilado",
    }

    return mapa.get(numero)


# ============================================================
# DÍAS
# ============================================================

def es_dia_atencion(fecha):

    zona = obtener_zona()

    fecha = fecha.astimezone(
        zona
    )

    return fecha.weekday() in DIAS_ATENCION


def horario_atencion_texto():

    return (
        "lunes a sábado, "
        "de 10:00 a 18:00 hrs"
    )


# ============================================================
# PARSEAR HORA
# ============================================================

def parse_hora_texto(texto):

    texto_n = normalizar_texto(
        texto
    )

    # 15:00
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

        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return hora, minuto

        return None


    # 3 pm / 3 p.m.
    match = re.search(
        r"\b(\d{1,2})\s*(am|pm|a\.m\.|p\.m\.)\b",
        texto_n
    )

    if match:

        hora = int(
            match.group(1)
        )

        periodo = (
            match.group(2)
            .replace(".", "")
        )

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

        if 0 <= hora <= 23:
            return hora, 0

        return None


    # a las 3
    match = re.search(
        r"\ba\s+las?\s+(\d{1,2})\b",
        texto_n
    )

    if match:

        hora = int(
            match.group(1)
        )

        # En este negocio 1-6 se interpreta
        # como horario de tarde.
        if 1 <= hora <= 6:
            hora += 12

        if 0 <= hora <= 23:
            return hora, 0

        return None


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

        if 0 <= hora <= 23:
            return hora, 0

        return None


    return None


def contiene_hora(texto):

    return (
        parse_hora_texto(texto)
        is not None
    )


# ============================================================
# DÍA DE SEMANA
# ============================================================

def detectar_dia_semana(texto):

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
# DÍA + MES
# ============================================================

def detectar_dia_mes(texto):

    texto_n = normalizar_texto(
        texto
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

    match = re.search(
        r"\b(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|"
        r"julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre)\b",
        texto_n
    )

    if match:

        return (
            int(match.group(1)),
            meses[match.group(2)]
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

def detectar_solo_dia(texto):

    texto_n = normalizar_texto(
        texto
    )

    # Evitar capturar horas
    if re.search(
        r"\ba\s+las?\s+\d{1,2}\b",
        texto_n
    ):
        return None

    if re.search(
        r"\b\d{1,2}:\d{2}\b",
        texto_n
    ):
        return None

    # "el 20"
    match = re.search(
        r"\b(?:el|dia)\s+(\d{1,2})\b",
        texto_n
    )

    if match:

        dia = int(
            match.group(1)
        )

        if 1 <= dia <= 31:
            return dia

    # "20 de este mes"
    match = re.search(
        r"\b(\d{1,2})\s+de\s+este\s+mes\b",
        texto_n
    )

    if match:

        dia = int(
            match.group(1)
        )

        if 1 <= dia <= 31:
            return dia

    return None


# ============================================================
# CONSTRUIR FECHA
# ============================================================

def construir_fecha_desde_texto(texto):

    zona = obtener_zona()

    ahora = ahora_local()

    texto_n = normalizar_texto(
        texto
    )


    # PASADO MAÑANA PRIMERO
    if re.search(
        r"\bpasado\s+manana\b",
        texto_n
    ):

        return (
            ahora
            + timedelta(days=2)
        ).replace(
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

        return (
            ahora
            + timedelta(days=1)
        ).replace(
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

        diferencia = (
            weekday
            - ahora.weekday()
        ) % 7

        if (
            diferencia == 0
            and (
                proximo
                or siguiente
            )
        ):
            diferencia = 7

        fecha = (
            ahora
            + timedelta(
                days=diferencia
            )
        )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )


    # DÍA + MES
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


    # SOLO DÍA
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
# PARSEAR FECHA + HORA
# ============================================================

def parse_fecha_hora(texto):

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

    # FALLBACK DATEPARSER
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
                "DATEPARSER ERROR:",
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


# ============================================================
# FORMATOS
# ============================================================

def formato_fecha(fecha):

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


def formato_fecha_corta(fecha):

    zona = obtener_zona()

    fecha = fecha.astimezone(
        zona
    )

    return (

        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"a las "
        f"{fecha.strftime('%H:%M')}"
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

        # Día
        if not es_dia_atencion(
            inicio
        ):
            return False

        # Solo horas exactas
        if (
            inicio.minute != 0
            or inicio.second != 0
        ):
            return False

        # Horario
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
            calendario
            .get(
                "busy",
                []
            )
        )


        # Si hay cualquier bloque ocupado,
        # saltamos esa hora.
        return len(bloques) == 0


    except Exception as e:

        print(
            "GOOGLE FREEBUSY ERROR:",
            repr(e)
        )

        return None


# ============================================================
# BUSCAR HORAS DISPONIBLES DE UN DÍA
# ============================================================

def buscar_horas_disponibles_dia(
    fecha,
    cantidad=10,
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

        # No ofrecer horas pasadas
        if inicio <= ahora:
            continue

        disponible = (
            verificar_disponibilidad(
                inicio,
                DURACION_RESERVA
            )
        )

        # MUY IMPORTANTE:
        # None = error consultando Calendar.
        # No se puede asumir libre.
        if disponible is True:

            resultados.append(
                inicio
            )

        # Si está ocupado simplemente
        # seguimos buscando la siguiente hora.

        if len(resultados) >= cantidad:
            break

    return resultados


# ============================================================
# BUSCAR PRÓXIMAS 10 HORAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicial,
    cantidad=10,
    dias_maximos=30
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


        desde_hora = HORA_APERTURA


        # Si estamos buscando desde hoy,
        # comenzar después de la hora actual.
        if (
            fecha.date()
            == ahora.date()
        ):

            if ahora.minute > 0 or ahora.second > 0:

                desde_hora = ahora.hour + 1

            else:

                desde_hora = ahora.hour

            desde_hora = max(
                desde_hora,
                HORA_APERTURA
            )


        horas = buscar_horas_disponibles_dia(

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
# MOSTRAR OPCIONES 1-10
# ============================================================

def formatear_opciones_horas(
    horas
):

    if not horas:
        return ""

    lineas = []

    for indice, hora in enumerate(
        horas,
        start=1
    ):

        lineas.append(

            f"{indice}. "
            f"{formato_fecha_corta(hora)}"
        )

    return "\n".join(
        lineas
    )


# ============================================================
# DETECTAR SELECCIÓN 1-10
# ============================================================

def detectar_numero_opcion(
    texto,
    maximo=10
):

    texto_n = normalizar_texto(
        texto
    )

    match = re.fullmatch(
        r"\s*(\d{1,2})\s*[\.\)]?\s*",
        texto_n
    )

    if not match:
        return None

    numero = int(
        match.group(1)
    )

    if 1 <= numero <= maximo:
        return numero

    return None


# ============================================================
# INTENCIONES
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
        "reservame una hora",
        "quiero una hora",
        "quiero agendar",
        "quiero reservar",
        "sacar hora",
        "sacar una hora",
        "pedir hora",
        "quiero una cita",
        "cita",
        "turno",
        "hora para corte",
        "hora para barba",
        "hora para perfilado",
    ]

    return any(
        patron in texto_n
        for patron in patrones
    )


def pregunta_disponibilidad(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    palabras = [

        "disponible",
        "disponibilidad",
        "que horas",
        "hay hora",
        "tienes hora",
        "tiene hora",
        "queda hora",
        "horas libres",
        "horas disponibles",
        "cuando tienes",
        "cuando hay",
    ]

    return any(
        palabra in texto_n
        for palabra in palabras
    )


def pregunta_servicios(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    palabras = [

        "servicios",
        "servicio",
        "que haces",
        "que ofrecen",
        "que tienes",
        "precios",
        "precio",
        "cuanto sale",
        "cuanto cuesta",
        "valor",
        "valores",
        "tarifa",
        "cortes",
        "barberia",
        "barbero",
    ]

    return any(
        palabra in texto_n
        for palabra in palabras
    )


# ============================================================
# INTENCIÓN DE TERMINAR / NO RESERVAR
# ============================================================

def quiere_salir(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    patrones = [

        "no gracias",
        "no, gracias",
        "gracias no",
        "no quiero",
        "no necesito",
        "dejalo",
        "dejalo",
        "cancelar",
        "cancela",
        "olvidalo",
        "olvidalo",
        "nada mas",
        "eso seria",
        "eso es todo",
        "por ahora no",
        "despues",
        "otro dia",
        "no voy",
    ]

    return any(
        patron in texto_n
        for patron in patrones
    )


# ============================================================
# RESET RESERVA
# ============================================================

def resetear_reserva_estado(
    estado,
    conservar_telefono=True
):

    telefono = None

    if conservar_telefono:

        telefono = (
            estado
            .get("datos_reserva", {})
            .get("telefono")
        )

    estado["datos_reserva"] = {

        "servicio":
            None,

        "fecha_hora":
            None,

        "nombre":
            None,

        "telefono":
            telefono,
    }

    estado["modo_agendar"] = False

    estado["opciones_horas"] = []

    estado["opciones_fecha"] = None


# ============================================================
# OPENAI CONVERSACIONAL
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""

Eres el asistente virtual de {NEGOCIO_NOMBRE}.

Tu nombre funcional es:
"Asistente Virtual de Estilista {ESTILISTA_NOMBRE}".

Atiendes principalmente por chat y WhatsApp.

IMPORTANTE:

La conversación debe sentirse como una conversación
natural tipo ChatGPT, NO como un formulario.

Al comienzo NO debes preguntar automáticamente:
"¿Qué servicio quieres reservar?"

Primero conversa normalmente.

Ejemplo:

Cliente:
Hola

Asistente:
¡Hola! 👋 ¿Cómo estás?

Cliente:
Súper bien, ¿y tú?

Asistente:
¡Súper bien también! 😄
¿Qué te gustaría hacer hoy?

Cliente:
Quiero saber qué servicios tienen.

Asistente:
¡Claro! ✂️ Tenemos...

Otro ejemplo:

Cliente:
Hola

Asistente:
¡Hola! 👋 ¿Cómo estás?

Cliente:
Bien.

Asistente:
¡Me alegro! 😄 ¿En qué te puedo ayudar?

Si el cliente simplemente conversa,
responde naturalmente.

NO fuerces una reserva inmediatamente.

Pero sí debes orientar suavemente la conversación
hacia dos posibilidades:

1. Conocer los servicios.
2. Agendar una hora.

Si pregunta por servicios o precios,
entrega la información.

TODOS LOS SERVICIOS CUESTAN $20.000.

Servicios:

- Corte de cabello — $20.000
- Corte + barba — $20.000
- Arreglo de barba — $20.000
- Corte de niño — $20.000
- Perfilado — $20.000

Cada atención dura 1 hora.

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Las horas de inicio son:

10:00
11:00
12:00
13:00
14:00
15:00
16:00
17:00

Domingo cerrado.

Si el cliente quiere agendar,
el sistema externo se encargará de comprobar
Google Calendar.

Nunca inventes disponibilidad.

Nunca digas que una hora está disponible
sin que el sistema la haya comprobado.

La agenda pertenece exclusivamente a
{ESTILISTA_NOMBRE}.

El cliente no necesita iniciar sesión en Google.

Si el cliente dice que no quiere reservar,
no insistas.

Puedes responder de forma natural y cerrar:

"Perfecto 😊 Cuando quieras, aquí estaremos. ¡Que tengas un buen día!"

La conversación puede comenzar nuevamente
cuando el cliente escriba otro mensaje.

ESTILISTA:
{ESTILISTA_NOMBRE}

NEGOCIO:
{NEGOCIO_NOMBRE}

HORARIO:
{horario_atencion_texto()}

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

                max_tokens=400,

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
            "Cuéntame qué necesitas "
            "y te ayudo."
        )


# ============================================================
# RESPUESTA SERVICIOS
# ============================================================

def responder_servicios():

    return servicios_texto()


# ============================================================
# INICIAR RESERVA
# ============================================================

def iniciar_flujo_reserva(
    estado,
    texto
):

    datos = estado[
        "datos_reserva"
    ]

    servicio = detectar_servicio(
        texto
    )

    if not servicio:

        servicio = detectar_servicio_por_numero(
            texto
        )

    if servicio:

        datos["servicio"] = servicio


    estado["modo_agendar"] = True


    if not datos["servicio"]:

        return (

            "¡Perfecto! 🙌 "
            "Te ayudo a reservar.\n\n"

            "Primero dime qué servicio quieres:\n\n"

            "1. Corte de cabello — $20.000\n"
            "2. Corte + barba — $20.000\n"
            "3. Arreglo de barba — $20.000\n"
            "4. Corte de niño — $20.000\n"
            "5. Perfilado — $20.000\n\n"

            "Puedes responder con el número "
            "o escribir el servicio."
        )


    servicio_obj = obtener_servicio(
        datos["servicio"]
    )


    return (

        f"Perfecto 👍 "
        f"{servicio_obj['nombre']}.\n\n"

        "¿Para qué día te gustaría reservar?"
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
    # SALIR DEL FLUJO
    # ========================================================

    if quiere_salir(texto):

        resetear_reserva_estado(
            estado,
            conservar_telefono=True
        )

        return (

            "Perfecto 😊 No hay problema.\n\n"

            "Cuando quieras conocer los servicios "
            "o reservar una hora, aquí estaré. ✂️\n\n"

            "¡Que tengas un buen día!"
        )


    # ========================================================
    # 1. SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if not servicio:

            servicio = detectar_servicio_por_numero(
                texto
            )

        if servicio:

            datos["servicio"] = servicio

        else:

            return (

                "Claro 👍 ¿Qué servicio quieres reservar?\n\n"

                "1. Corte de cabello — $20.000\n"
                "2. Corte + barba — $20.000\n"
                "3. Arreglo de barba — $20.000\n"
                "4. Corte de niño — $20.000\n"
                "5. Perfilado — $20.000\n\n"

                "Responde con el número o "
                "con el nombre del servicio."
            )


    servicio_obj = obtener_servicio(
        datos["servicio"]
    )


    # ========================================================
    # 2. SI HAY OPCIONES DE HORA MOSTRADAS,
    #    ESPERAR NÚMERO 1-10
    # ========================================================

    opciones = estado.get(
        "opciones_horas",
        []
    )

    if opciones:

        numero = detectar_numero_opcion(
            texto,
            maximo=len(opciones)
        )

        if numero is not None:

            seleccion = opciones[
                numero - 1
            ]

            # Segunda comprobación inmediata
            # antes de aceptar la selección.
            disponible = verificar_disponibilidad(
                seleccion,
                DURACION_RESERVA
            )

            if disponible is None:

                return (

                    "No pude comprobar la agenda "
                    "en este momento 😕.\n\n"

                    "Intenta nuevamente en unos segundos."
                )

            if not disponible:

                # La hora pudo haberse ocupado
                # después de mostrarla.
                estado["opciones_horas"] = []

                nuevas = buscar_proximas_horas(
                    seleccion,
                    cantidad=10,
                    dias_maximos=30
                )

                if nuevas:

                    estado[
                        "opciones_horas"
                    ] = nuevas

                    return (

                        "Justo esa hora se ocupó 😕.\n\n"

                        "Actualicé la disponibilidad. "
                        "Elige otra opción:\n\n"

                        + formatear_opciones_horas(nuevas)
                        + "\n\n"
                        "Respóndeme con el número "
                        "de la opción que prefieras."
                    )

                return (

                    "Justo esa hora se ocupó 😕.\n\n"

                    "No encontré disponibilidad "
                    "cercana. ¿Quieres probar con "
                    "otro día?"
                )


            datos["fecha_hora"] = (
                seleccion.isoformat()
            )

            estado["opciones_horas"] = []


            return (

                f"¡Perfecto! 🙌\n\n"

                f"Elegiste "
                f"{formato_fecha(seleccion)}.\n\n"

                "¿Me indicas tu nombre?"
            )


        # Si escribió una hora directamente,
        # permitir cambiar la selección.
        fecha_directa = parse_fecha_hora(
            texto
        )

        if fecha_directa and contiene_hora(
            texto
        ):

            estado["opciones_horas"] = []

            # Continuamos procesando abajo.
            datos["fecha_hora"] = None

        else:

            return (

                "Elige una de las opciones "
                "disponibles 😊.\n\n"

                + formatear_opciones_horas(opciones)
                + "\n\n"

                "Respóndeme solamente con el número "
                "que prefieras."
            )


    # ========================================================
    # 3. FECHA Y HORA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )

        tiene_hora = contiene_hora(
            texto
        )


        # ----------------------------------------------------
        # SOLO SERVICIO + "QUIERO AGENDAR"
        # ----------------------------------------------------

        if fecha is None:

            return (

                f"Perfecto 👍 "
                f"{servicio_obj['nombre']}.\n\n"

                "¿Qué día te gustaría venir?\n\n"

                "Puedes decirme, por ejemplo:\n"

                "• mañana\n"
                "• mañana en la tarde\n"
                "• el lunes\n"
                "• el 20 de agosto\n\n"

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

                proximas = buscar_proximas_horas(
                    fecha,
                    cantidad=10,
                    dias_maximos=30
                )

                if proximas:

                    estado[
                        "opciones_horas"
                    ] = proximas

                    return (

                        "Ese día no tenemos atención 😕.\n\n"

                        "Pero encontré estas próximas "
                        "10 horas disponibles:\n\n"

                        + formatear_opciones_horas(proximas)
                        + "\n\n"

                        "Elige la que prefieras "
                        "respondiendo con un número "
                        "del 1 al "
                        + str(len(proximas))
                        + "."
                    )

                return (

                    "Ese día no tenemos atención 😕.\n\n"

                    f"Nuestro horario es "
                    f"{horario_atencion_texto()}."
                )


            horas = buscar_horas_disponibles_dia(
                fecha,
                cantidad=10
            )

            if horas:

                estado[
                    "opciones_horas"
                ] = horas

                return (

                    f"Perfecto 👍 Para "
                    f"{formato_fecha_corta(fecha)} "
                    "encontré estas horas disponibles:\n\n"

                    + formatear_opciones_horas(horas)
                    + "\n\n"

                    "Elige la que más te acomode "
                    "respondiendo con el número."
                )


            proximas = buscar_proximas_horas(
                fecha,
                cantidad=10,
                dias_maximos=30
            )

            if proximas:

                estado[
                    "opciones_horas"
                ] = proximas

                return (

                    "Ese día ya no tengo horas disponibles 😕.\n\n"

                    "Estas son las próximas 10 "
                    "horas que encontré:\n\n"

                    + formatear_opciones_horas(proximas)
                    + "\n\n"

                    "Elige una respondiendo con "
                    "el número."
                )


            return (

                "No encontré disponibilidad "
                "cercana 😕.\n\n"

                "¿Quieres probar con otro día?"
            )


        # ----------------------------------------------------
        # VALIDAR DÍA
        # ----------------------------------------------------

        if not es_dia_atencion(
            fecha
        ):

            proximas = buscar_proximas_horas(
                fecha,
                cantidad=10,
                dias_maximos=30
            )

            if proximas:

                estado[
                    "opciones_horas"
                ] = proximas

                return (

                    "Ese día no tenemos atención 😕.\n\n"

                    "Te muestro las próximas "
                    "10 horas disponibles:\n\n"

                    + formatear_opciones_horas(proximas)
                    + "\n\n"

                    "Elige una respondiendo con "
                    "el número."
                )

            return (

                "Ese día no tenemos atención 😕.\n\n"

                f"Atendemos "
                f"{horario_atencion_texto()}."
            )


        # ----------------------------------------------------
        # MINUTOS
        # ----------------------------------------------------

        if fecha.minute != 0:

            proximas = buscar_proximas_horas(
                fecha,
                cantidad=10,
                dias_maximos=30
            )

            if proximas:

                estado[
                    "opciones_horas"
                ] = proximas

                return (

                    "Las reservas son por horas "
                    "exactas 🕐.\n\n"

                    "Estas son las próximas "
                    "horas disponibles:\n\n"

                    + formatear_opciones_horas(proximas)
                    + "\n\n"

                    "Elige una respondiendo con "
                    "el número."
                )

            return (

                "Las reservas son por hora exacta 🕐.\n\n"

                "Por ejemplo: 10:00, 11:00, "
                "12:00, 13:00, 14:00, 15:00, "
                "16:00 o 17:00."
            )


        # ----------------------------------------------------
        # HORARIO
        # ----------------------------------------------------

        if (
            fecha.hour < HORA_APERTURA
            or fecha.hour >= HORA_CIERRE
        ):

            proximas = buscar_proximas_horas(
                fecha,
                cantidad=10,
                dias_maximos=30
            )

            if proximas:

                estado[
                    "opciones_horas"
                ] = proximas

                return (

                    "Ese horario está fuera "
                    "de atención 😕.\n\n"

                    f"Atendemos "
                    f"{horario_atencion_texto()}.\n\n"

                    "Estas son las próximas "
                    "horas disponibles:\n\n"

                    + formatear_opciones_horas(proximas)
                    + "\n\n"

                    "Elige una respondiendo con "
                    "el número."
                )

            return (
                f"Nuestro horario es "
                f"{horario_atencion_texto()}."
            )


        # ----------------------------------------------------
        # RESERVA TERMINA A LAS 18
        # ----------------------------------------------------

        fin = (
            fecha
            + timedelta(
                minutes=DURACION_RESERVA
            )
        )

        cierre = fecha.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        )

        if fin > cierre:

            return (

                "Esa hora no permite completar "
                "una atención de 1 hora 😕.\n\n"

                "La última hora de inicio es "
                "a las 17:00."
            )


        # ----------------------------------------------------
        # PASADO
        # ----------------------------------------------------

        if fecha <= ahora_local():

            proximas = buscar_proximas_horas(
                fecha,
                cantidad=10,
                dias_maximos=30
            )

            if proximas:

                estado[
                    "opciones_horas"
                ] = proximas

                return (

                    "Esa hora ya pasó 😕.\n\n"

                    "Estas son las próximas "
                    "horas disponibles:\n\n"

                    + formatear_opciones_horas(proximas)
                    + "\n\n"

                    "Elige una respondiendo con "
                    "el número."
                )

            return (
                "Esa hora ya pasó 😕.\n\n"
                "Dime otro día."
            )


        # ----------------------------------------------------
        # CONSULTAR GOOGLE CALENDAR
        # ----------------------------------------------------

        disponible = verificar_disponibilidad(
            fecha,
            DURACION_RESERVA
        )


        if disponible is None:

            return (

                "No pude consultar la agenda "
                "de Diego en este momento 😕.\n\n"

                "Intenta nuevamente en unos segundos."
            )


        # ----------------------------------------------------
        # OCUPADO
        # ----------------------------------------------------

        if not disponible:

            proximas = buscar_proximas_horas(
                fecha,
                cantidad=10,
                dias_maximos=30
            )

            if proximas:

                estado[
                    "opciones_horas"
                ] = proximas

                return (

                    "Esa hora ya está ocupada 😕.\n\n"

                    "La salté automáticamente y "
                    "busqué las próximas 10 horas "
                    "disponibles:\n\n"

                    + formatear_opciones_horas(proximas)
                    + "\n\n"

                    "Elige la que prefieras "
                    "respondiendo con el número."
                )

            return (

                "Esa hora está ocupada 😕.\n\n"

                "¿Quieres probar con otro día?"
            )


        # ----------------------------------------------------
        # GUARDAR
        # ----------------------------------------------------

        datos["fecha_hora"] = (
            fecha.isoformat()
        )


        return (

            "¡Perfecto! 🙌\n\n"

            f"Tengo disponible "
            f"{formato_fecha(fecha)}.\n\n"

            "¿Me indicas tu nombre?"
        )


    # ========================================================
    # 4. NOMBRE
    # ========================================================

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )

        # Evitar frases extremadamente largas
        if len(texto) > 80:

            return (
                "¿Me indicas solamente tu nombre, "
                "por favor? 😊"
            )

        datos["nombre"] = texto


        # En WhatsApp ya tenemos teléfono.
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
    # 5. TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto


    # ========================================================
    # 6. COMPLETAR
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

        return (
            "Me falta confirmar el día y "
            "la hora 😊."
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


    servicio = obtener_servicio(
        datos["servicio"]
    )


    # ========================================================
    # SEGUNDA COMPROBACIÓN
    # ========================================================

    disponible = verificar_disponibilidad(
        inicio,
        DURACION_RESERVA
    )


    if disponible is None:

        return (

            "No pude comprobar nuevamente "
            "la disponibilidad 😕.\n\n"

            "Intenta nuevamente en unos segundos."
        )


    if not disponible:

        datos["fecha_hora"] = None

        nuevas = buscar_proximas_horas(
            inicio,
            cantidad=10,
            dias_maximos=30
        )

        estado[
            "opciones_horas"
        ] = nuevas

        if nuevas:

            return (

                "Justo esa hora se ocupó 😕.\n\n"

                "Actualicé la agenda. "
                "Estas son las próximas opciones:\n\n"

                + formatear_opciones_horas(nuevas)
                + "\n\n"

                "Elige una respondiendo con "
                "el número."
            )

        return (

            "Justo esa hora se ocupó 😕.\n\n"

            "No encontré otra hora cercana. "
            "¿Quieres probar otro día?"
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


    nombre = datos["nombre"]

    telefono = datos["telefono"]

    fecha_texto = formato_fecha(
        inicio
    )

    servicio_nombre = servicio[
        "nombre"
    ]


    # ========================================================
    # CONSERVAR TELÉFONO
    # ========================================================

    telefono_guardar = telefono


    # ========================================================
    # RESET
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

    estado["opciones_horas"] = []


    return (

        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: {servicio_nombre}\n"

        f"💰 Valor: {formato_precio(servicio['precio'])}\n"

        f"👤 Cliente: {nombre}\n"

        f"📞 Teléfono: {telefono}\n"

        f"📅 {fecha_texto}\n\n"

        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"

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

                    f"Valor: "
                    f"{formato_precio(servicio['precio'])}\n"

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

                body=evento,

                sendUpdates="none"
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
    # INICIALIZAR
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
                        f"de Estilista {ESTILISTA_NOMBRE} ✂️\n\n"
                        "¿Cómo estás?"
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


    if "opciones_horas" not in session:
        session["opciones_horas"] = []


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
            # SI ESTÁ EN RESERVA
            # =================================================

            en_reserva = session.get(
                "modo_agendar",
                False
            )


            # =================================================
            # SI QUIERE SALIR
            # =================================================

            if (
                en_reserva
                and quiere_salir(pregunta)
            ):

                estado = {

                    "modo_agendar":
                        session["modo_agendar"],

                    "datos_reserva":
                        session["datos_reserva"],

                    "opciones_horas":
                        session.get(
                            "opciones_horas",
                            []
                        ),
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

                session[
                    "opciones_horas"
                ] = estado.get(
                    "opciones_horas",
                    []
                )


            # =================================================
            # RESERVA
            # =================================================

            elif en_reserva:

                estado = {

                    "modo_agendar":
                        True,

                    "datos_reserva":
                        session["datos_reserva"],

                    "opciones_horas":
                        session.get(
                            "opciones_horas",
                            []
                        ),
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

                session[
                    "opciones_horas"
                ] = estado.get(
                    "opciones_horas",
                    []
                )


            # =================================================
            # SERVICIOS
            # =================================================

            elif pregunta_servicios(
                pregunta
            ):

                respuesta = responder_servicios()


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

                    "opciones_horas":
                        session.get(
                            "opciones_horas",
                            []
                        ),
                }


                respuesta = (
                    iniciar_flujo_reserva(
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

                session[
                    "opciones_horas"
                ] = estado.get(
                    "opciones_horas",
                    []
                )


            # =================================================
            # CONVERSACIÓN NATURAL
            # =================================================

            else:

                respuesta = responder_openai(

                    session[
                        "historial"
                    ],

                    pregunta
                )


            # =================================================
            # GUARDAR RESPUESTA
            # =================================================

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
            "WHATSAPP SEND ERROR:",
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

            "opciones_horas": [],

            "datos_reserva": {

                "servicio":
                    None,

                "fecha_hora":
                    None,

                "nombre":
                    None,

                "telefono":
                    wa_id,
            },
        }


    return WA_SESSIONS[wa_id]


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
        )[0]


        changes = (
            entry.get(
                "changes"
            )
            or []
        )[0]


        value = (
            changes.get(
                "value"
            )
            or {}
        )


        # Ignorar estados
        if value.get("statuses"):

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


        en_reserva = estado[
            "modo_agendar"
        ]


        # ====================================================
        # SALIR
        # ====================================================

        if (
            en_reserva
            and quiere_salir(text)
        ):

            respuesta = (
                procesar_reserva(
                    estado,
                    text
                )
            )


        # ====================================================
        # RESERVA ACTIVA
        # ====================================================

        elif en_reserva:

            respuesta = (
                procesar_reserva(
                    estado,
                    text
                )
            )


        # ====================================================
        # SERVICIOS
        # ====================================================

        elif pregunta_servicios(
            text
        ):

            respuesta = responder_servicios()


        # ====================================================
        # RESERVA
        # ====================================================

        elif (
            es_intencion_agendar(text)
            or pregunta_disponibilidad(text)
        ):

            estado[
                "modo_agendar"
            ] = True


            respuesta = (
                iniciar_flujo_reserva(
                    estado,
                    text
                )
            )


        # ====================================================
        # CONVERSACIÓN NATURAL
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


        # Limitar historial
        if len(
            estado["historial"]
        ) > 30:

            estado[
                "historial"
            ] = estado[
                "historial"
            ][-30:]


        wa_send_text(
            wa_id,
            respuesta
        )


    except Exception as e:

        print(
            "WHATSAPP WEBHOOK ERROR:",
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


        # Render puede estar detrás del proxy.
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

                        "Vuelve a /admin/login y "
                        "autoriza nuevamente."
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

<title>
Google Calendar autorizado
</title>

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
Ve a <b>Environment</b>.
</li>

<li>
Busca:
<b>GOOGLE_REFRESH_TOKEN</b>
</li>

<li>
Pega el token anterior.
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

<title>
Error Google OAuth
</title>

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

    font-size:
        14px;
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

button:hover {

    opacity:
        .9;
}


@media (max-width: 600px) {

    #chat-container {

        position:
            fixed;

        left:
            0;

        right:
            0;

        bottom:
            0;

        width:
            100%;

        height:
            100%;

        border-radius:
            0;
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
