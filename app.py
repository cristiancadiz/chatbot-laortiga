import os
import re
import requests
import pytz
import openai

from flask import (
    Flask,
    redirect,
    url_for,
    session,
    request,
    render_template_string,
)

from datetime import datetime, timedelta
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
    raise Exception("Falta SECRET_KEY")

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
    raise Exception("Falta OPENAI_API_KEY")

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

HORA_APERTURA = 10
HORA_CIERRE = 18
DURACION_RESERVA = 60

# Lunes=0 ... Domingo=6
DIAS_ATENCION = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
}

HORAS_DISPONIBLES = list(
    range(HORA_APERTURA, HORA_CIERRE)
)


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte": {
        "numero": 1,
        "nombre": "Corte de cabello",
        "duracion": 60,
        "precio": 20000,
    },
    "corte_barba": {
        "numero": 2,
        "nombre": "Corte + barba",
        "duracion": 60,
        "precio": 20000,
    },
    "barba": {
        "numero": 3,
        "nombre": "Arreglo de barba",
        "duracion": 60,
        "precio": 20000,
    },
    "corte_nino": {
        "numero": 4,
        "nombre": "Corte de niño",
        "duracion": 60,
        "precio": 20000,
    },
    "perfilado": {
        "numero": 5,
        "nombre": "Perfilado",
        "duracion": 60,
        "precio": 20000,
    },
}

SERVICIO_POR_NUMERO = {
    1: "corte",
    2: "corte_barba",
    3: "barba",
    4: "corte_nino",
    5: "perfilado",
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
    raise Exception("Falta GOOGLE_CLIENT_ID")

if not GOOGLE_CLIENT_SECRET:
    raise Exception("Falta GOOGLE_CLIENT_SECRET")


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
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
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


def obtener_credentials_diego():

    if not GOOGLE_REFRESH_TOKEN:
        raise Exception(
            "Falta GOOGLE_REFRESH_TOKEN"
        )

    return Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
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
# FECHA / HORA
# ============================================================

def obtener_zona():
    return pytz.timezone(TIMEZONE)


def ahora_local():

    return datetime.now(
        obtener_zona()
    )


def es_dia_atencion(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return fecha.weekday() in DIAS_ATENCION


DIAS_NOMBRES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


MESES = [
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


def formato_fecha_corta(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"{fecha.strftime('%H:%M')}"
    )


def formato_fecha_larga(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day} de "
        f"{MESES[fecha.month - 1]} "
        f"a las {fecha.strftime('%H:%M')}"
    )


# ============================================================
# NORMALIZAR TEXTO
# ============================================================

def normalizar_texto(texto):

    texto = (texto or "").strip().lower()

    reemplazos = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ü": "u",
    }

    for a, b in reemplazos.items():
        texto = texto.replace(a, b)

    return texto


# ============================================================
# SERVICIOS
# ============================================================

def mostrar_servicios():

    return (
        "Claro 😊 Estos son nuestros servicios:\n\n"
        "1. Corte de cabello — $20.000\n"
        "2. Corte + barba — $20.000\n"
        "3. Arreglo de barba — $20.000\n"
        "4. Corte de niño — $20.000\n"
        "5. Perfilado — $20.000\n\n"
        "Si quieres reservar, escríbeme el número "
        "del servicio que quieres."
    )


def detectar_servicio_por_numero(texto):

    match = re.fullmatch(
        r"\s*([1-5])\s*",
        normalizar_texto(texto)
    )

    if not match:
        return None

    numero = int(match.group(1))

    return SERVICIO_POR_NUMERO.get(
        numero
    )


def detectar_servicio(texto):

    texto_n = normalizar_texto(texto)

    # PRIMERO número
    servicio = detectar_servicio_por_numero(
        texto
    )

    if servicio:
        return servicio

    if "corte" in texto_n and "barba" in texto_n:
        return "corte_barba"

    if (
        "corte de nino" in texto_n
        or "corte nino" in texto_n
        or "nino" in texto_n
    ):
        return "corte_nino"

    if "barba" in texto_n:
        return "barba"

    if "perfilado" in texto_n:
        return "perfilado"

    if "perfil" in texto_n:
        return "perfilado"

    if "corte" in texto_n:
        return "corte"

    return None


def obtener_servicio(codigo):

    return SERVICIOS[codigo]


# ============================================================
# INTENCIONES
# ============================================================

def pregunta_servicios(texto):

    texto_n = normalizar_texto(texto)

    palabras = [
        "servicios",
        "servicio",
        "precios",
        "precio",
        "valores",
        "valor",
        "cuanto cuesta",
        "cuanto sale",
        "que ofrecen",
        "que haces",
        "que tienen",
        "barberia",
    ]

    return any(
        p in texto_n
        for p in palabras
    )


def es_intencion_agendar(texto):

    texto_n = normalizar_texto(texto)

    palabras = [
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
        "quiero corte",
        "quiero cortarme",
        "me quiero cortar",
        "hora para corte",
        "hora para barba",
    ]

    return any(
        p in texto_n
        for p in palabras
    )


def usuario_no_quiere(texto):

    texto_n = normalizar_texto(texto)

    palabras = [
        "no quiero",
        "no gracias",
        "gracias no",
        "cancelar",
        "cancela",
        "olvidalo",
        "dejalo",
        "no por ahora",
        "despues",
        "no necesito",
    ]

    return any(
        p in texto_n
        for p in palabras
    )


# ============================================================
# GOOGLE: COMPROBAR DISPONIBILIDAD
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion=DURACION_RESERVA
):

    try:

        zona = obtener_zona()

        inicio = inicio.astimezone(zona)

        if not es_dia_atencion(inicio):
            return False

        if inicio.minute != 0:
            return False

        if inicio.hour < HORA_APERTURA:
            return False

        if inicio.hour >= HORA_CIERRE:
            return False

        fin = inicio + timedelta(
            minutes=duracion
        )

        limite = inicio.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        )

        if fin > limite:
            return False

        service = obtener_calendar_service()

        resultado = (
            service
            .freebusy()
            .query(
                body={
                    "timeMin": inicio.isoformat(),
                    "timeMax": fin.isoformat(),
                    "items": [
                        {
                            "id": CALENDAR_ID
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

        busy = calendario.get(
            "busy",
            []
        )

        # SI EXISTE CUALQUIER EVENTO:
        # NO ESTÁ DISPONIBLE
        if busy:
            return False

        return True

    except Exception as e:

        print(
            "ERROR DISPONIBILIDAD:",
            repr(e)
        )

        return None


# ============================================================
# BUSCAR PRÓXIMAS 10 HORAS
# ============================================================

def buscar_proximas_10_horas():

    ahora = ahora_local()

    resultados = []

    # Buscamos hasta 60 días hacia adelante
    for offset in range(60):

        fecha = (
            ahora
            + timedelta(days=offset)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        # Domingo
        if not es_dia_atencion(fecha):
            continue

        for hora in HORAS_DISPONIBLES:

            inicio = fecha.replace(
                hour=hora,
                minute=0,
                second=0,
                microsecond=0
            )

            # Nunca ofrecer horas pasadas
            if inicio <= ahora:
                continue

            disponible = verificar_disponibilidad(
                inicio
            )

            print(
                "HORA:",
                inicio,
                "DISPONIBLE:",
                disponible
            )

            # Si Google falla NO inventamos
            if disponible is None:
                continue

            if disponible:

                resultados.append(
                    inicio
                )

                if len(resultados) == 10:
                    return resultados

    return resultados


def formatear_opciones_horas(horas):

    lineas = []

    for i, hora in enumerate(
        horas,
        start=1
    ):

        lineas.append(
            f"{i}. {formato_fecha_corta(hora)}"
        )

    return "\n".join(lineas)


# ============================================================
# OPENAI
# ============================================================

def responder_openai(
    historial
):

    try:

        system_prompt = f"""
Eres el Asistente Virtual de Estilista {ESTILISTA_NOMBRE}.

Hablas español natural de Chile.

Tu conversación debe sentirse como una conversación
real, fluida y humana, similar a ChatGPT.

NO repitas frases genéricas constantemente.

Si el cliente dice:

Hola

puedes responder:

¡Hola! 👋 ¿Cómo estás?

Si dice:

Super bien y tú?

responde de forma natural, por ejemplo:

¡Qué bueno! 😄 Yo también estoy muy bien, gracias.
¿Quieres conocer los servicios de Diego o prefieres
agendar una hora?

OBJETIVO:

Llevar naturalmente al cliente hacia:

1. Conocer servicios.
2. Agendar una hora.

SERVICIOS:

1. Corte de cabello — $20.000
2. Corte + barba — $20.000
3. Arreglo de barba — $20.000
4. Corte de niño — $20.000
5. Perfilado — $20.000

HORARIO:

Lunes a sábado.
10:00 a 18:00.
Atenciones de 1 hora.

IMPORTANTE:

No inventes disponibilidad.

La disponibilidad real la comprueba el sistema.

Si el cliente quiere agendar, el sistema se encargará
del proceso de reserva.

No hables de APIs, código, Google Calendar,
programación ni aspectos técnicos.

Sé breve y natural.
"""

        mensajes = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

        # IMPORTANTE:
        # Aquí NO agregamos nuevamente el mensaje actual.
        mensajes.extend(
            historial[-14:]
        )

        respuesta = (
            client
            .chat
            .completions
            .create(
                model="gpt-4o-mini",
                messages=mensajes,
                max_tokens=250,
                temperature=0.8,
            )
        )

        texto = (
            respuesta
            .choices[0]
            .message
            .content
        )

        return (
            texto.strip()
            if texto
            else
            "¡Hola! 👋 ¿Cómo estás?"
        )

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        return (
            "¡Hola! 👋 ¿Cómo estás? "
            "¿Quieres conocer los servicios "
            "o reservar una hora?"
        )


# ============================================================
# RESET
# ============================================================

def resetear_reserva(estado):

    telefono = (
        estado["datos_reserva"]
        .get("telefono")
    )

    estado["modo_agendar"] = False
    estado["paso"] = "inicio"
    estado["horas_ofrecidas"] = []

    estado["datos_reserva"] = {
        "servicio": None,
        "fecha_hora": None,
        "nombre": None,
        "telefono": telefono,
    }


# ============================================================
# FLUJO DE AGENDA
# ============================================================

def procesar_agenda(
    estado,
    texto
):

    datos = estado["datos_reserva"]

    texto = (texto or "").strip()

    # --------------------------------------------------------
    # CANCELACIÓN
    # --------------------------------------------------------

    if usuario_no_quiere(texto):

        resetear_reserva(
            estado
        )

        return (
            "No hay problema 😊\n\n"
            "Cuando quieras volver, aquí estaré. "
            "¡Que estés muy bien! 👋"
        )

    # --------------------------------------------------------
    # SERVICIO
    # --------------------------------------------------------

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if not servicio:

            estado["paso"] = "servicio"

            return mostrar_servicios()

        datos["servicio"] = servicio

        info = obtener_servicio(
            servicio
        )

        # AQUÍ ESTÁ LA CORRECCIÓN PRINCIPAL:
        # apenas el cliente selecciona 1,2,3,4 o 5
        # buscamos inmediatamente las 10 próximas horas.

        horas = buscar_proximas_10_horas()

        if not horas:

            return (
                f"Perfecto 😊 Elegiste "
                f"{info['nombre']}.\n\n"
                "Por ahora no encontré horas "
                "disponibles."
            )

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        estado["paso"] = "hora"

        return (
            f"Perfecto 😊\n\n"
            f"✂️ {info['nombre']}\n"
            f"💰 $20.000\n\n"
            "Estas son las próximas 10 horas "
            "disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "Respóndeme con el número de la hora "
            "que prefieras, del 1 al 10."
        )

    # --------------------------------------------------------
    # HORA
    # --------------------------------------------------------

    if estado["paso"] == "hora":

        match = re.fullmatch(
            r"\s*(\d{1,2})\s*",
            texto
        )

        if not match:

            return (
                "Indícame el número de la hora "
                "que prefieres 😊.\n\n"
                "Por ejemplo: 1"
            )

        numero = int(
            match.group(1)
        )

        horas_guardadas = (
            estado["horas_ofrecidas"]
        )

        if (
            numero < 1
            or numero > len(horas_guardadas)
        ):

            return (
                f"Elige un número entre 1 y "
                f"{len(horas_guardadas)} 😊."
            )

        try:

            fecha_hora = datetime.fromisoformat(
                horas_guardadas[
                    numero - 1
                ]
            )

        except Exception:

            horas = buscar_proximas_10_horas()

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Actualicé la disponibilidad 😊.\n\n"
                f"{formatear_opciones_horas(horas)}"
            )

        # SEGUNDA COMPROBACIÓN
        # Evita reservar algo que acaba de ocuparse.

        disponible = verificar_disponibilidad(
            fecha_hora
        )

        if disponible is None:

            return (
                "No pude comprobar la agenda "
                "en este momento 😕.\n"
                "Intenta nuevamente."
            )

        if not disponible:

            horas = buscar_proximas_10_horas()

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Esa hora acaba de ocuparse 😕.\n\n"
                "Actualicé las próximas horas "
                "disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "Elige otra opción."
            )

        datos["fecha_hora"] = (
            fecha_hora.isoformat()
        )

        estado["paso"] = "nombre"

        return (
            "¡Perfecto! 🙌\n\n"
            f"Te reservamos "
            f"{formato_fecha_larga(fecha_hora)}.\n\n"
            "¿Me indicas tu nombre?"
        )

    # --------------------------------------------------------
    # NOMBRE
    # --------------------------------------------------------

    if estado["paso"] == "nombre":

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? 😊"
            )

        datos["nombre"] = texto

        return completar_reserva(
            estado
        )

    return mostrar_servicios()


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

        service = obtener_calendar_service()

        servicio = obtener_servicio(
            servicio_codigo
        )

        fin = inicio + timedelta(
            minutes=DURACION_RESERVA
        )

        evento = {

            "summary":
                f"{servicio['nombre']} - {nombre_cliente}",

            "description":
                (
                    "Reserva creada por Asistente Virtual.\n\n"
                    f"Cliente: {nombre_cliente}\n"
                    f"Teléfono: {telefono_cliente}\n"
                    f"Servicio: {servicio['nombre']}\n"
                    f"Valor: ${servicio['precio']}\n"
                    f"Duración: {DURACION_RESERVA} minutos"
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
                        "Asistente Virtual",
                }
            },
        }

        resultado = (
            service
            .events()
            .insert(
                calendarId=CALENDAR_ID,
                body=evento
            )
            .execute()
        )

        return {
            "ok": True,
            "evento_id":
                resultado.get("id"),
        }

    except Exception as e:

        print(
            "ERROR CREANDO EVENTO:",
            repr(e)
        )

        return {
            "ok": False,
            "error": str(e)
        }


# ============================================================
# COMPLETAR RESERVA
# ============================================================

def completar_reserva(estado):

    datos = estado["datos_reserva"]

    if not datos["fecha_hora"]:

        estado["paso"] = "hora"

        horas = buscar_proximas_10_horas()

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Estas son las próximas horas "
            "disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}"
        )

    # --------------------------------------------------------
    # COMPROBACIÓN FINAL
    # --------------------------------------------------------

    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )

    disponible = verificar_disponibilidad(
        inicio
    )

    if disponible is None:

        return (
            "No pude comprobar la agenda "
            "en este momento 😕."
        )

    if not disponible:

        datos["fecha_hora"] = None
        estado["paso"] = "hora"

        horas = buscar_proximas_10_horas()

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Esa hora acaba de ocuparse 😕.\n\n"
            "Estas son las nuevas horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}"
        )

    # --------------------------------------------------------
    # CREAR EVENTO
    # --------------------------------------------------------

    resultado = crear_evento_diego(
        inicio=inicio,
        servicio_codigo=datos["servicio"],
        nombre_cliente=datos["nombre"],
        telefono_cliente=datos["telefono"],
    )

    if not resultado["ok"]:

        return (
            "No pude completar la reserva "
            "en este momento 😕.\n\n"
            "Intenta nuevamente."
        )

    servicio = obtener_servicio(
        datos["servicio"]
    )

    nombre = datos["nombre"]

    telefono = datos["telefono"]

    fecha_texto = formato_fecha_larga(
        inicio
    )

    resetear_reserva(
        estado
    )

    return (
        "✅ ¡Reserva confirmada!\n\n"
        f"✂️ Servicio: {servicio['nombre']}\n"
        f"💰 Valor: $20.000\n"
        f"👤 Cliente: {nombre}\n"
        f"📞 Teléfono: {telefono}\n"
        f"📅 {fecha_texto}\n\n"
        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"
        "¡Te esperamos! 🙌"
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


def wa_send_text(
    to,
    text
):

    if not WHATSAPP_TOKEN:
        return

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
            to,
        "type":
            "text",
        "text": {
            "body":
                text[:3900]
        },
    }

    try:

        r = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20
        )

        print(
            "WHATSAPP:",
            r.status_code,
            r.text[:300]
        )

    except Exception as e:

        print(
            "WHATSAPP ERROR:",
            repr(e)
        )


WA_SESSIONS = {}


def get_wa_session(wa_id):

    if wa_id not in WA_SESSIONS:

        WA_SESSIONS[wa_id] = {

            "historial": [],

            "modo_agendar": False,

            "paso": "inicio",

            "horas_ofrecidas": [],

            "datos_reserva": {
                "servicio": None,
                "fecha_hora": None,
                "nombre": None,
                "telefono": wa_id,
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

    data = request.get_json(
        silent=True
    ) or {}

    try:

        value = (
            data
            .get("entry", [])[0]
            .get("changes", [])[0]
            .get("value", {})
        )

        messages = value.get(
            "messages",
            []
        )

        if not messages:
            return "ok", 200

        msg = messages[0]

        wa_id = msg.get(
            "from"
        )

        text = (
            msg
            .get("text", {})
            .get("body", "")
            .strip()
        )

        if not wa_id or not text:
            return "ok", 200

        estado = get_wa_session(
            wa_id
        )

        estado["datos_reserva"]["telefono"] = wa_id

        # GUARDAMOS EL MENSAJE UNA SOLA VEZ
        estado["historial"].append({
            "role": "user",
            "content": text
        })

        # ----------------------------------------------------
        # YA ESTÁ AGENDANDO
        # ----------------------------------------------------

        if estado["modo_agendar"]:

            respuesta = procesar_agenda(
                estado,
                text
            )

        # ----------------------------------------------------
        # QUIERE AGENDAR
        # ----------------------------------------------------

        elif es_intencion_agendar(text):

            estado["modo_agendar"] = True
            estado["paso"] = "servicio"

            # Si escribió directamente "quiero corte",
            # detectamos el servicio.
            respuesta = procesar_agenda(
                estado,
                text
            )

        # ----------------------------------------------------
        # SERVICIOS
        # ----------------------------------------------------

        elif pregunta_servicios(text):

            # IMPORTANTE:
            # Mostrar servicios también activa el modo
            # de agenda para que el siguiente "1" funcione.

            estado["modo_agendar"] = True
            estado["paso"] = "servicio"

            respuesta = mostrar_servicios()

        # ----------------------------------------------------
        # CONVERSACIÓN NATURAL
        # ----------------------------------------------------

        else:

            respuesta = responder_openai(
                estado["historial"]
            )

        estado["historial"].append({
            "role": "assistant",
            "content": respuesta
        })

        wa_send_text(
            wa_id,
            respuesta
        )

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            repr(e)
        )

    return "ok", 200


# ============================================================
# CHAT WEB
# ============================================================

@app.route("/")
def home():

    return redirect(
        url_for("chat")
    )


@app.route(
    "/chat",
    methods=["GET", "POST"]
)
def chat():

    session.permanent = True

    if "historial" not in session:

        session["historial"] = [
            {
                "role": "assistant",
                "content":
                    "¡Hola! 👋 Soy el Asistente Virtual "
                    "de Estilista Diego ✂️\n\n"
                    "¿Cómo estás?"
            }
        ]

    if "modo_agendar" not in session:
        session["modo_agendar"] = False

    if "paso" not in session:
        session["paso"] = "inicio"

    if "horas_ofrecidas" not in session:
        session["horas_ofrecidas"] = []

    if "datos_reserva" not in session:

        session["datos_reserva"] = {
            "servicio": None,
            "fecha_hora": None,
            "nombre": None,
            "telefono": None,
        }

    if request.method == "POST":

        pregunta = request.form.get(
            "pregunta",
            ""
        ).strip()

        if pregunta:

            session["historial"].append({
                "role": "user",
                "content": pregunta
            })

            # ------------------------------------------------
            # YA ESTÁ AGENDANDO
            # ------------------------------------------------

            if session["modo_agendar"]:

                estado = {
                    "modo_agendar": True,
                    "paso":
                        session["paso"],
                    "horas_ofrecidas":
                        session["horas_ofrecidas"],
                    "datos_reserva":
                        session["datos_reserva"],
                }

                respuesta = procesar_agenda(
                    estado,
                    pregunta
                )

                session["modo_agendar"] = (
                    estado["modo_agendar"]
                )

                session["paso"] = (
                    estado["paso"]
                )

                session["horas_ofrecidas"] = (
                    estado["horas_ofrecidas"]
                )

                session["datos_reserva"] = (
                    estado["datos_reserva"]
                )

            # ------------------------------------------------
            # QUIERE AGENDAR
            # ------------------------------------------------

            elif es_intencion_agendar(
                pregunta
            ):

                session["modo_agendar"] = True
                session["paso"] = "servicio"

                estado = {
                    "modo_agendar": True,
                    "paso": "servicio",
                    "horas_ofrecidas": [],
                    "datos_reserva":
                        session["datos_reserva"],
                }

                respuesta = procesar_agenda(
                    estado,
                    pregunta
                )

                session["paso"] = (
                    estado["paso"]
                )

                session["horas_ofrecidas"] = (
                    estado["horas_ofrecidas"]
                )

                session["datos_reserva"] = (
                    estado["datos_reserva"]
                )

            # ------------------------------------------------
            # SERVICIOS
            # ------------------------------------------------

            elif pregunta_servicios(
                pregunta
            ):

                # CORRECCIÓN CLAVE:
                # al mostrar servicios dejamos activo
                # el flujo de agenda.

                session["modo_agendar"] = True
                session["paso"] = "servicio"

                respuesta = mostrar_servicios()

            # ------------------------------------------------
            # CHAT NATURAL
            # ------------------------------------------------

            else:

                respuesta = responder_openai(
                    session["historial"]
                )

            session["historial"].append({
                "role": "assistant",
                "content": respuesta
            })

            session.modified = True

    return render_template_string(
        TEMPLATE,
        historial=session["historial"]
    )


# ============================================================
# GOOGLE LOGIN
# ============================================================

@app.route("/admin/login")
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

        session["google_oauth_state"] = state
        session["google_code_verifier"] = (
            flow.code_verifier
        )

        return redirect(
            authorization_url
        )

    except Exception as e:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Error iniciando Google OAuth",
            mensaje=str(e)
        )


# ============================================================
# CALLBACK
# ============================================================

@app.route("/callback")
def callback():

    try:

        code = request.args.get(
            "code"
        )

        if not code:
            raise Exception(
                "Google no entregó el código."
            )

        state = session.get(
            "google_oauth_state"
        )

        verifier = session.get(
            "google_code_verifier"
        )

        if not state:
            raise Exception(
                "Se perdió el estado OAuth. "
                "Vuelve a /admin/login."
            )

        flow = crear_google_flow()

        flow.state = state
        flow.code_verifier = verifier

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

        refresh_token = (
            flow.credentials.refresh_token
        )

        if not refresh_token:

            raise Exception(
                "Google no entregó refresh_token."
            )

        session.pop(
            "google_oauth_state",
            None
        )

        session.pop(
            "google_code_verifier",
            None
        )

        return render_template_string(
            TOKEN_TEMPLATE,
            token=refresh_token
        )

    except Exception as e:

        print(
            "CALLBACK ERROR:",
            repr(e)
        )

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Error autenticando con Google",
            mensaje=str(e)
        )


# ============================================================
# TEMPLATE CHAT
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
    font-family: Arial;
    max-width: 850px;
    margin: 50px auto;
    padding: 20px;
    background: #f5f5f5;
}

.box {
    background: white;
    padding: 30px;
    border-radius: 15px;
}

textarea {
    width: 100%;
    height: 120px;
    margin-top: 15px;
}

</style>

</head>

<body>

<div class="box">

<h1>✅ Google Calendar autorizado</h1>

<p>
Copia este refresh token y guárdalo en Render:
</p>

<textarea readonly>{{ token }}</textarea>

<h3>Variable:</h3>

<p>
<b>GOOGLE_REFRESH_TOKEN</b>
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

<title>Error</title>

<style>

body {
    font-family: Arial;
    max-width: 800px;
    margin: 50px auto;
    padding: 20px;
}

.box {
    padding: 30px;
    background: #fff3f3;
    border-radius: 15px;
}

pre {
    white-space: pre-wrap;
}

</style>

</head>

<body>

<div class="box">

<h1>❌ {{ titulo }}</h1>

<pre>{{ mensaje }}</pre>

<hr>

<a href="/admin/login">
Volver a Google
</a>

</div>

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
        debug=False
    )
