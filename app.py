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

def nueva_reserva(
    telefono=None
):

    return {

        "estado":
            "inicio",

        "servicio":
            None,

        "fecha_hora":
            None,

        "opciones_horarios":
            [],

        "opcion_seleccionada":
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
# PRECIO
# ============================================================

def precio_texto(
    precio
):

    return (
        f"${precio:,.0f}"
        .replace(",", ".")
    )


# ============================================================
# SERVICIOS TEXTO
# ============================================================

def servicios_texto():

    return (

        "✂️ *Servicios de Diego*\n\n"

        "1️⃣ Corte de cabello — $20.000\n"
        "2️⃣ Corte + barba — $20.000\n"
        "3️⃣ Arreglo de barba — $20.000\n"
        "4️⃣ Corte de niño — $20.000\n"
        "5️⃣ Perfilado — $20.000\n\n"

        "Todos los servicios tienen un valor de "
        "$20.000.\n\n"

        "Si quieres reservar, dime cuál te interesa "
        "y te muestro las próximas horas disponibles 😊."
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
    )

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
# DÍA DE ATENCIÓN
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
# HORA DE ATENCIÓN
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
# PRÓXIMO DÍA
# ============================================================

def proximo_dia_semana(
    fecha_base,
    weekday_objetivo,
    incluir_hoy=True
):

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

    match = re.search(
        r"\b(\d{1,2})\s*:\s*(\d{2})\b",
        texto_n
    )

    if match:

        return (
            int(match.group(1)),
            int(match.group(2))
        )

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

    return None


# ============================================================
# CONTIENE HORA
# ============================================================

def contiene_hora(
    texto
):

    return (
        parse_hora_texto(texto)
        is not None
    )


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

    if re.search(
        r"\bpasado manana\b",
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

    weekday = detectar_dia_semana(
        texto
    )

    if weekday is not None:

        proximo = bool(
            re.search(
                r"\b(proximo|siguiente)\b",
                texto_n
            )
        )

        fecha = proximo_dia_semana(

            ahora,

            weekday,

            incluir_hoy=not proximo
        )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
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

    return None


# ============================================================
# PARSEAR FECHA HORA
# ============================================================

def parse_fecha_hora(
    texto
):

    zona = obtener_zona()

    ahora = ahora_local()

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
                },
            )

            if resultado:

                if resultado.tzinfo is None:

                    fecha = zona.localize(
                        resultado
                    )

                else:

                    fecha = (
                        resultado
                        .astimezone(zona)
                    )

        except Exception as e:

            print(
                "Dateparser error:",
                repr(e)
            )

    if fecha is None:
        return None

    hora = parse_hora_texto(
        texto
    )

    if hora:

        try:

            fecha = fecha.replace(

                hour=hora[0],

                minute=hora[1],

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
# FORMATO FECHA
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


# ============================================================
# FORMATO CORTO
# ============================================================

def formato_fecha_corta(
    fecha
):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (

        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"a las {fecha.strftime('%H:%M')}"
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
            .get("calendars", {})
            .get(CALENDAR_ID, {})
        )

        bloques = calendario.get(
            "busy",
            []
        )

        return len(bloques) == 0

    except Exception as e:

        print(
            "Calendar availability error:",
            repr(e)
        )

        return None


# ============================================================
# BUSCAR PRÓXIMAS 10 HORAS
# ============================================================

def buscar_proximas_horas(
    cantidad=10,
    dias_maximos=30
):

    zona = obtener_zona()

    ahora = ahora_local()

    resultados = []

    for offset in range(
        dias_maximos
    ):

        fecha = (
            ahora
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

        for hora in HORAS_DISPONIBLES:

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

                    return resultados

            elif disponible is None:

                # Si Calendar falla, no inventamos.
                continue

    return resultados


# ============================================================
# MOSTRAR 10 OPCIONES
# ============================================================

def mostrar_opciones_horarios(
    opciones
):

    if not opciones:

        return (

            "No encontré horas disponibles "
            "en los próximos días 😕.\n\n"

            "¿Quieres que revisemos más adelante?"
        )

    lineas = [

        "📅 *Próximas horas disponibles*",
        "",
    ]

    numeros = [

        "1️⃣",
        "2️⃣",
        "3️⃣",
        "4️⃣",
        "5️⃣",
        "6️⃣",
        "7️⃣",
        "8️⃣",
        "9️⃣",
        "🔟",
    ]

    for i, fecha in enumerate(
        opciones
    ):

        lineas.append(

            f"{numeros[i]} "
            f"{formato_fecha(fecha)}"
        )

    lineas.extend([

        "",

        "Escribe el número de la hora "
        "que prefieres 😊.",

    ])

    return "\n".join(
        lineas
    )


# ============================================================
# DETECTAR NÚMERO 1-10
# ============================================================

def detectar_opcion_horario(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    # Acepta:
    # 1
    # 2
    # ...
    # 10
    # también "opción 3"

    match = re.fullmatch(
        r"(?:opcion\s*)?(\d{1,2})",
        texto_n
    )

    if not match:
        return None

    numero = int(
        match.group(1)
    )

    if 1 <= numero <= 10:
        return numero

    return None


# ============================================================
# INTENCIÓN AGENDA
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
        "cita",
        "turno",
        "quiero cortarme",
        "quiero corte",
        "quiero barba",
        "quiero perfilado",
    ]

    return any(
        patron in texto_n
        for patron in patrones
    )


# ============================================================
# INTENCIÓN SERVICIOS
# ============================================================

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
        "costo",
    ]

    return any(
        palabra in texto_n
        for palabra in palabras
    )


# ============================================================
# RESPUESTA GPT
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""

Eres el Asistente Virtual de {NEGOCIO_NOMBRE}.

Tu nombre funcional es:
"Asistente Virtual de Estilista {ESTILISTA_NOMBRE}".

Eres un asistente conversacional para una barbería/
estilista.

Hablas español de Chile.

PERSONALIDAD:

- natural
- amable
- cercano
- simpático
- profesional
- breve
- conversacional

IMPORTANTE:

La conversación debe sentirse como conversar
con ChatGPT.

NO debes empujar agresivamente una reserva.

Si el cliente dice:

"Hola"

puedes responder:

"¡Hola! 👋 ¿Cómo estás?"

Si responde:

"Bien"

puedes continuar naturalmente:

"¡Qué bueno! 😊 ¿En qué te puedo ayudar?"

Puedes conversar normalmente.

Pero cuando tenga sentido, puedes mencionar
suavemente que puedes:

- mostrar los servicios
- mostrar precios
- buscar horas disponibles
- ayudar a reservar

SERVICIOS:

Todos cuestan exactamente $20.000.

1. Corte de cabello — $20.000
2. Corte + barba — $20.000
3. Arreglo de barba — $20.000
4. Corte de niño — $20.000
5. Perfilado — $20.000

HORARIO:

Lunes a sábado.
10:00 a 18:00.

Cada atención dura 1 hora.

La última hora de inicio es 17:00.

Domingo cerrado.

GOOGLE CALENDAR:

La disponibilidad real la consulta el sistema
directamente en Google Calendar.

Nunca inventes horarios.

Nunca digas que una hora está disponible
si el sistema no la comprobó.

El cliente NO necesita Google Calendar.

La agenda utilizada es solamente la agenda
de {ESTILISTA_NOMBRE}.

Cuando el cliente manifieste claramente que
quiere reservar, el sistema se encargará del
flujo de agenda.

NO debes inventar una reserva.

Si el cliente pregunta por servicios o precios,
entrega la información.

Si el cliente dice que no quiere reservar,
no insistas.

Puedes despedirte amablemente.

"""

        mensajes = [

            {
                "role":
                    "system",

                "content":
                    system_prompt,
            }
        ]

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

                max_tokens=300,

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
# CONFIRMACIONES
# ============================================================

def respuesta_es_si(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    return texto_n in [

        "si",
        "sí",
        "sipo",
        "sip",
        "yes",
        "dale",
        "ok",
        "okay",
        "bueno",
        "confirmo",
        "confirmar",
        "confirmemos",
        "perfecto",
        "ya",
    ]


def respuesta_es_no(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    return texto_n in [

        "no",
        "nop",
        "no gracias",
        "mejor no",
        "dejalo",
        "déjalo",
        "cancelar",
        "cancela",
        "cancelalo",
        "cancelarlo",
    ]


# ============================================================
# REINICIAR RESERVA
# ============================================================

def cancelar_reserva(
    estado
):

    telefono = (
        estado["datos_reserva"]
        .get("telefono")
    )

    estado[
        "modo_agendar"
    ] = False

    estado[
        "datos_reserva"
    ] = nueva_reserva(
        telefono
    )

    return (

        "No hay problema 😊\n\n"

        "Cuando quieras reservar o conocer "
        "los servicios de Diego, aquí estaré.\n\n"

        "¡Que tengas un excelente día! ✂️"
    )


# ============================================================
# PROCESAR AGENDA
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

    estado_actual = datos.get(
        "estado",
        "inicio"
    )


    # ========================================================
    # CANCELACIÓN
    # ========================================================

    if respuesta_es_no(
        texto
    ):

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

        if not servicio:

            servicio = (
                detectar_servicio_numero(
                    texto
                )
            )

        if servicio:

            datos[
                "servicio"
            ] = servicio

        else:

            return (

                "Perfecto 😊 Antes de buscar tu hora, "
                "¿qué servicio quieres?\n\n"

                "1️⃣ Corte de cabello — $20.000\n"
                "2️⃣ Corte + barba — $20.000\n"
                "3️⃣ Arreglo de barba — $20.000\n"
                "4️⃣ Corte de niño — $20.000\n"
                "5️⃣ Perfilado — $20.000\n\n"

                "Puedes escribir el nombre o "
                "el número."
            )


    servicio = obtener_servicio(
        datos["servicio"]
    )


    # ========================================================
    # SI YA HAY UNA OPCIÓN GUARDADA
    # ========================================================

    opciones = datos.get(
        "opciones_horarios",
        []
    )


    # ========================================================
    # SELECCIÓN 1-10
    # ========================================================

    if opciones:

        opcion = detectar_opcion_horario(
            texto
        )

        if opcion is not None:

            indice = opcion - 1

            if indice >= len(opciones):

                return (

                    "Esa opción no está disponible 😕.\n\n"

                    f"Elige un número entre 1 y "
                    f"{len(opciones)}."
                )

            fecha = opciones[
                indice
            ]

            disponible = (
                verificar_disponibilidad(
                    fecha,
                    DURACION_RESERVA
                )
            )

            if disponible is not True:

                datos[
                    "opciones_horarios"
                ] = []

                nuevas = (
                    buscar_proximas_horas(
                        cantidad=10
                    )
                )

                datos[
                    "opciones_horarios"
                ] = nuevas

                return (

                    "Justo esa hora acaba de ocuparse 😕.\n\n"

                    + mostrar_opciones_horarios(
                        nuevas
                    )
                )

            datos[
                "fecha_hora"
            ] = fecha.isoformat()

            datos[
                "opcion_seleccionada"
            ] = opcion

            datos[
                "opciones_horarios"
            ] = []

            return (

                "¡Perfecto! 👍\n\n"

                f"✂️ {servicio['nombre']}\n"
                f"📅 {formato_fecha(fecha)}\n"
                f"💰 {precio_texto(servicio['precio'])}\n\n"

                "¿Quieres confirmar esta hora?"
            )


    # ========================================================
    # SI NO HAY FECHA Y HORA
    # ========================================================

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto
        )

        # ----------------------------------------------------
        # Si el cliente dio una fecha/hora específica
        # ----------------------------------------------------

        if fecha and contiene_hora(
            texto
        ):

            if not es_dia_atencion(
                fecha
            ):

                nuevas = (
                    buscar_proximas_horas(
                        cantidad=10
                    )
                )

                datos[
                    "opciones_horarios"
                ] = nuevas

                return (

                    "Ese día no tenemos atención 😕.\n\n"

                    + mostrar_opciones_horarios(
                        nuevas
                    )
                )

            disponible = (
                verificar_disponibilidad(
                    fecha,
                    DURACION_RESERVA
                )
            )

            if disponible is True:

                datos[
                    "fecha_hora"
                ] = fecha.isoformat()

                return (

                    "¡Perfecto! 👍\n\n"

                    f"✂️ {servicio['nombre']}\n"
                    f"📅 {formato_fecha(fecha)}\n"
                    f"💰 {precio_texto(servicio['precio'])}\n\n"

                    "¿Quieres confirmar esta hora?"
                )

            if disponible is False:

                return (

                    "Esa hora está ocupada 😕.\n\n"

                    "Te muestro las próximas "
                    "10 horas disponibles:\n\n"

                    + mostrar_opciones_horarios(
                        buscar_proximas_horas(
                            cantidad=10
                        )
                    )
                )

            return (

                "No pude consultar la agenda "
                "en este momento 😕.\n\n"
                "Intenta nuevamente en unos segundos."
            )


        # ----------------------------------------------------
        # Buscar automáticamente 10 próximas
        # ----------------------------------------------------

        nuevas = (
            buscar_proximas_horas(
                cantidad=10
            )
        )

        datos[
            "opciones_horarios"
        ] = nuevas

        if not nuevas:

            return (

                "No encontré horas disponibles "
                "en los próximos días 😕.\n\n"

                "¿Quieres que revisemos más adelante?"
            )

        return mostrar_opciones_horarios(
            nuevas
        )


    # ========================================================
    # CONFIRMACIÓN
    # ========================================================

    if datos["fecha_hora"]:

        if respuesta_es_si(
            texto
        ):

            if not datos["nombre"]:

                return (

                    "¡Perfecto! 🙌\n\n"

                    "¿Me indicas tu nombre?"
                )

        else:

            # Si escribió otra cosa en vez de sí,
            # preguntar nuevamente sin perder la hora.

            return (

                "¿Confirmamos esta hora? 😊\n\n"

                f"✂️ {servicio['nombre']}\n"
                f"📅 {formato_fecha(datetime.fromisoformat(datos['fecha_hora']))}\n"
                f"💰 {precio_texto(servicio['precio'])}\n\n"

                "Responde *sí* para confirmar "
                "o *no* para cancelar."
            )


    # ========================================================
    # NOMBRE
    # ========================================================

    if not datos["nombre"]:

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? 😊"
            )

        datos[
            "nombre"
        ] = texto

        # WhatsApp ya trae teléfono.
        if datos.get("telefono"):

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

    if not datos.get(
        "telefono"
    ):

        datos[
            "telefono"
        ] = texto

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
            "Me falta confirmar la hora 😊."
        )

    if not datos["servicio"]:

        return (
            "Me falta saber qué servicio quieres ✂️."
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
            "Necesito volver a confirmar la hora 😊."
        )

    servicio = obtener_servicio(
        datos["servicio"]
    )

    # ========================================================
    # SEGUNDA COMPROBACIÓN
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

            "Intenta nuevamente en unos segundos."
        )

    if not disponible:

        datos["fecha_hora"] = None

        nuevas = (
            buscar_proximas_horas(
                cantidad=10
            )
        )

        datos[
            "opciones_horarios"
        ] = nuevas

        return (

            "Justo esa hora se ocupó 😕.\n\n"

            + mostrar_opciones_horarios(
                nuevas
            )
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

    precio = precio_texto(
        servicio["precio"]
    )

    # Mantener teléfono para futuras reservas
    telefono_guardar = telefono

    estado[
        "datos_reserva"
    ] = nueva_reserva(
        telefono_guardar
    )

    estado[
        "modo_agendar"
    ] = False

    return (

        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: {servicio_nombre}\n"
        f"💰 Valor: {precio}\n"
        f"👤 Cliente: {nombre}\n"
        f"📞 Teléfono: {telefono}\n"
        f"📅 {fecha_texto}\n\n"

        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"

        "La atención dura 1 hora.\n\n"

        "¡Te esperamos! 🙌"
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
                    f"Estilista {ESTILISTA_NOMBRE}.\n\n"

                    f"Cliente: {nombre_cliente}\n"
                    f"Teléfono: {telefono_cliente}\n"
                    f"Servicio: {servicio['nombre']}\n"
                    f"Valor: {precio_texto(servicio['precio'])}\n"
                    f"Duración: {DURACION_RESERVA} minutos\n"
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

                    "precio":
                        str(servicio["precio"]),

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

            historial = session[
                "historial"
            ]

            historial.append({

                "role":
                    "user",

                "content":
                    pregunta,
            })

            iniciar = (

                es_intencion_agendar(
                    pregunta
                )

                or pregunta_servicios(
                    pregunta
                )

                or session.get(
                    "modo_agendar",
                    False
                )
            )

            # =================================================
            # SERVICIOS
            # =================================================

            if (
                pregunta_servicios(
                    pregunta
                )
                and not session.get(
                    "modo_agendar",
                    False
                )
            ):

                respuesta = servicios_texto()

            # =================================================
            # AGENDA
            # =================================================

            elif iniciar:

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
            # GPT NORMAL
            # =================================================

            else:

                respuesta = (
                    responder_openai(

                        historial,

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

        if value.get("statuses"):
            return "ok", 200

        messages = (
            value.get("messages")
            or []
        )

        if not messages:
            return "ok", 200

        msg = messages[0]

        msg_id = msg.get("id")

        wa_id = msg.get("from")

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

        iniciar = (

            es_intencion_agendar(
                text
            )

            or pregunta_servicios(
                text
            )

            or estado[
                "modo_agendar"
            ]
        )

        # ====================================================
        # SERVICIOS
        # ====================================================

        if (
            pregunta_servicios(text)
            and not estado[
                "modo_agendar"
            ]
        ):

            respuesta = servicios_texto()

        # ====================================================
        # AGENDA
        # ====================================================

        elif iniciar:

            estado[
                "modo_agendar"
            ] = True

            respuesta = (
                procesar_reserva(

                    estado,

                    text
                )
            )

        # ====================================================
        # GPT NORMAL
        # ====================================================

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

<li>Pega el token.</li>

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

</style>

</head>

<body>

<div id="chat-container">

<div id="chat-header">

<div class="name">
✂️ Asistente Virtual de Estilista Diego
</div>

<div class="subtitle">
Lunes a sábado · 10:00 a 18:00 · Servicios $20.000
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
