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

HORA_APERTURA = 10
HORA_CIERRE = 18
DURACION_RESERVA = 60

# Lunes = 0 ... sábado = 5
DIAS_ATENCION = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
}

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
# ZONA HORARIA
# ============================================================

def obtener_zona():
    return pytz.timezone(TIMEZONE)


def ahora_local():

    zona = obtener_zona()

    return datetime.now(zona)


# ============================================================
# NORMALIZAR
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

    for original, nuevo in reemplazos.items():
        texto = texto.replace(
            original,
            nuevo
        )

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
        "Puedes escribirme el número del servicio "
        "que quieres."
    )


def detectar_servicio_por_numero(texto):

    texto_n = normalizar_texto(texto)

    # Solo aceptar número limpio para evitar
    # confundir una fecha/hora con servicio.
    match = re.fullmatch(
        r"\s*([1-5])\s*",
        texto_n
    )

    if match:

        numero = int(
            match.group(1)
        )

        return SERVICIO_POR_NUMERO.get(
            numero
        )

    return None


def detectar_servicio(texto):

    texto_n = normalizar_texto(texto)

    # Primero números.
    servicio_numero = detectar_servicio_por_numero(
        texto
    )

    if servicio_numero:
        return servicio_numero

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


def obtener_servicio(codigo):

    return SERVICIOS.get(
        codigo,
        {
            "nombre": "Servicio",
            "duracion": DURACION_RESERVA,
            "precio": 20000,
        }
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


def es_dia_atencion(fecha):

    zona = obtener_zona()

    fecha = fecha.astimezone(zona)

    return fecha.weekday() in DIAS_ATENCION


# ============================================================
# FORMATO FECHAS
# ============================================================

def formato_fecha_corta(fecha):

    zona = obtener_zona()

    fecha = fecha.astimezone(zona)

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"a las {fecha.strftime('%H:%M')}"
    )


def formato_fecha_larga(fecha):

    zona = obtener_zona()

    fecha = fecha.astimezone(zona)

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
        f"a las {fecha.strftime('%H:%M')}"
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

        inicio = inicio.astimezone(zona)

        if not es_dia_atencion(inicio):
            return False

        if inicio.minute != 0:
            return False

        if (
            inicio.hour < HORA_APERTURA
            or inicio.hour >= HORA_CIERRE
        ):
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

        bloques = calendario.get(
            "busy",
            []
        )

        # Si Google Calendar devuelve aunque sea
        # un bloque ocupado, esta hora NO está libre.
        if bloques:
            return False

        return True

    except Exception as e:

        print(
            "CALENDAR AVAILABILITY ERROR:",
            repr(e)
        )

        return None


# ============================================================
# PRÓXIMAS 10 HORAS REALES
# ============================================================

def buscar_proximas_10_horas():

    zona = obtener_zona()

    ahora = ahora_local()

    resultados = []

    # Buscar hasta 30 días hacia adelante.
    # Normalmente encontraremos 10 horas mucho antes.
    for offset in range(31):

        fecha = (
            ahora
            + timedelta(days=offset)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        if not es_dia_atencion(fecha):
            continue

        for hora in HORAS_DISPONIBLES:

            inicio = fecha.replace(
                hour=hora,
                minute=0,
                second=0,
                microsecond=0
            )

            # Nunca ofrecer una hora pasada.
            if inicio <= ahora:
                continue

            disponible = verificar_disponibilidad(
                inicio,
                DURACION_RESERVA
            )

            print(
                "BUSCANDO HORA:",
                inicio,
                "DISPONIBLE:",
                disponible
            )

            # Si Google falla, NO inventamos disponibilidad.
            if disponible is None:
                continue

            if disponible:

                resultados.append(
                    inicio
                )

                if len(resultados) >= 10:
                    return resultados

    return resultados


# ============================================================
# MOSTRAR LAS 10 HORAS
# ============================================================

def formatear_opciones_horas(horas):

    if not horas:
        return ""

    lineas = []

    for indice, hora in enumerate(
        horas,
        start=1
    ):

        lineas.append(
            f"{indice}. {formato_fecha_corta(hora)}"
        )

    return "\n".join(lineas)


def mostrar_proximas_horas():

    horas = buscar_proximas_10_horas()

    if not horas:

        return (
            "No encontré horas disponibles "
            "en los próximos días 😕.\n\n"
            "¿Quieres que probemos más adelante?"
        )

    return (
        "Perfecto 😊 Estas son las próximas "
        "10 horas disponibles:\n\n"
        f"{formatear_opciones_horas(horas)}\n\n"
        "Respóndeme con el número de la hora "
        "que prefieras, del 1 al 10."
    )


# ============================================================
# SESIONES WHATSAPP
# ============================================================

WA_SESSIONS = {}

PROCESSED_MSG_IDS = {}

DEDUP_TTL_SECONDS = 120


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
# WHATSAPP ENVÍO
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
# INTENCIÓN DE SERVICIOS
# ============================================================

def pregunta_servicios(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "servicios",
        "servicio",
        "que haces",
        "que hacen",
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
        "barbería",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


# ============================================================
# INTENCIÓN DE AGENDA
# ============================================================

def es_intencion_agendar(texto):

    texto_n = normalizar_texto(texto)

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
        "hora para corte",
        "hora para barba",
        "quiero cortarme",
        "quiero corte",
        "me quiero cortar",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


# ============================================================
# CANCELAR / NO QUIERO
# ============================================================

def usuario_no_quiere(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "no quiero",
        "no gracias",
        "gracias no",
        "dejalo",
        "dejalo",
        "olvidalo",
        "olvidalo",
        "cancelar",
        "cancela",
        "no por ahora",
        "despues",
        "después",
        "no necesito",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


# ============================================================
# RESET
# ============================================================

def resetear_reserva(estado):

    telefono = (
        estado
        .get("datos_reserva", {})
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
# CONVERSACIÓN NATURAL OPENAI
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

Hablas español natural de Chile.

Tu comportamiento debe parecerse a una conversación
natural tipo ChatGPT.

PERSONALIDAD:

- amable
- simpático
- cercano
- natural
- profesional
- breve
- conversacional

MUY IMPORTANTE:

NO debes intentar agendar inmediatamente.

Si el cliente dice:

"Hola"

puedes responder:

"¡Hola! 👋 ¿Cómo estás?"

Si dice:

"Super bien y tú?"

responde naturalmente, por ejemplo:

"¡Qué bueno! 😄 Yo también estoy muy bien, gracias.
¿Quieres conocer los servicios de Diego o prefieres
agendar una hora?"

NO respondas siempre:

"Cuéntame qué necesitas y te ayudo."

Debes responder al contenido real del mensaje.

OBJETIVO:

Llevar naturalmente la conversación hacia una de
estas dos opciones:

1. Conocer servicios y precios.
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

Cada atención dura 1 hora.

La última hora de inicio es 17:00.

Domingo cerrado.

GOOGLE CALENDAR:

La disponibilidad real la comprueba el sistema.

Nunca inventes disponibilidad.

No digas que una hora está disponible si no
fue comprobada por el sistema.

Cuando el cliente quiera agendar, el sistema
se encargará del flujo de reserva.

Tu función aquí es mantener una conversación
natural y orientar al cliente.

Si el cliente dice que no quiere reservar,
respóndele amablemente y deja abierta la posibilidad
de volver cuando quiera.

No menciones detalles técnicos, APIs, Google,
programación ni código.

ESTILISTA:
{ESTILISTA_NOMBRE}
"""

        mensajes = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

        mensajes.extend(
            historial[-12:]
        )

        mensajes.append({
            "role": "user",
            "content": pregunta
        })

        completion = (
            client
            .chat
            .completions
            .create(
                model="gpt-4o-mini",
                messages=mensajes,
                max_tokens=300,
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
                "¡Hola! 😊 ¿Cómo estás? "
                "¿Quieres conocer los servicios "
                "o agendar una hora?"
            )

        return respuesta.strip()

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        return (
            "¡Qué bueno! 😊 "
            "Si quieres, puedo mostrarte los servicios "
            "o ayudarte a reservar una hora."
        )


# ============================================================
# PROCESAR AGENDA
# ============================================================

def procesar_agenda(
    estado,
    texto
):

    datos = estado["datos_reserva"]

    texto = (texto or "").strip()

    # ========================================================
    # CANCELACIÓN
    # ========================================================

    if usuario_no_quiere(texto):

        resetear_reserva(estado)

        return (
            "No hay problema 😊 "
            "Cuando quieras reservar o conocer "
            "los servicios de Diego, aquí estaré. "
            "¡Que estés muy bien! 👋"
        )


    # ========================================================
    # PASO 1: SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(texto)

        if servicio:

            datos["servicio"] = servicio

            servicio_info = obtener_servicio(
                servicio
            )

            # IMPORTANTE:
            # Una vez seleccionado el servicio,
            # NO pedimos fecha.
            # Buscamos inmediatamente las próximas
            # 10 horas reales disponibles.

            horas = buscar_proximas_10_horas()

            if not horas:

                return (
                    f"Perfecto 😊 Elegiste "
                    f"{servicio_info['nombre']}.\n\n"
                    "Pero por ahora no encontré "
                    "horas disponibles."
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            estado["paso"] = "seleccionar_hora"

            return (
                f"Perfecto 😊 "
                f"{servicio_info['nombre']} — "
                f"${servicio_info['precio']:,}".replace(",", ".")
                + "\n\n"
                "Estas son las próximas 10 horas "
                "disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}"
                "\n\n"
                "Respóndeme con el número de la hora "
                "que prefieras, del 1 al 10."
            )

        return mostrar_servicios()


    # ========================================================
    # PASO 2: SELECCIONAR HORA 1-10
    # ========================================================

    if estado["paso"] == "seleccionar_hora":

        match = re.fullmatch(
            r"\s*(\d{1,2})\s*",
            texto
        )

        if not match:

            return (
                "Solo necesito que me indiques "
                "el número de la hora que prefieres 😊.\n\n"
                "Por ejemplo: 1"
            )

        numero = int(
            match.group(1)
        )

        horas_guardadas = (
            estado.get(
                "horas_ofrecidas",
                []
            )
        )

        if (
            numero < 1
            or numero > len(horas_guardadas)
        ):

            return (
                f"Elige un número entre 1 y "
                f"{len(horas_guardadas)}, por favor 😊."
            )

        try:

            fecha_hora = datetime.fromisoformat(
                horas_guardadas[numero - 1]
            )

        except Exception:

            estado["horas_ofrecidas"] = []

            estado["paso"] = "seleccionar_hora"

            horas = buscar_proximas_10_horas()

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Actualicé la disponibilidad 😊.\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
            )


        # ====================================================
        # SEGUNDA COMPROBACIÓN
        # ====================================================

        disponible = verificar_disponibilidad(
            fecha_hora,
            DURACION_RESERVA
        )

        if disponible is None:

            return (
                "No pude comprobar la agenda "
                "en este momento 😕.\n\n"
                "Intenta nuevamente en unos segundos."
            )

        if not disponible:

            horas = buscar_proximas_10_horas()

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Esa hora acaba de ocuparse 😕.\n\n"
                "Actualicé las horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
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


    # ========================================================
    # PASO 3: NOMBRE
    # ========================================================

    if estado["paso"] == "nombre":

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? 😊"
            )

        datos["nombre"] = texto

        telefono = datos.get(
            "telefono"
        )

        if telefono:

            estado["paso"] = "confirmar"

            return completar_reserva(
                estado
            )

        estado["paso"] = "telefono"

        return (
            f"Perfecto, {texto} 👍\n\n"
            "¿Cuál es tu número de teléfono?"
        )


    # ========================================================
    # PASO 4: TELÉFONO
    # ========================================================

    if estado["paso"] == "telefono":

        if len(texto) < 6:

            return (
                "¿Me indicas un número de teléfono "
                "válido, por favor? 📞"
            )

        datos["telefono"] = texto

        estado["paso"] = "confirmar"

        return completar_reserva(
            estado
        )


    # ========================================================
    # CONFIRMAR / COMPLETAR
    # ========================================================

    if estado["paso"] == "confirmar":

        return completar_reserva(
            estado
        )


    return mostrar_servicios()


# ============================================================
# COMPLETAR RESERVA
# ============================================================

def completar_reserva(estado):

    datos = estado["datos_reserva"]

    if not datos["servicio"]:
        estado["paso"] = "servicio"
        return mostrar_servicios()

    if not datos["fecha_hora"]:
        estado["paso"] = "seleccionar_hora"
        return mostrar_proximas_horas()

    if not datos["nombre"]:
        estado["paso"] = "nombre"
        return "¿Me indicas tu nombre? 😊"

    if not datos["telefono"]:
        estado["paso"] = "telefono"
        return "¿Cuál es tu número de teléfono? 📞"

    try:

        inicio = datetime.fromisoformat(
            datos["fecha_hora"]
        )

    except Exception:

        datos["fecha_hora"] = None
        estado["paso"] = "seleccionar_hora"

        return mostrar_proximas_horas()


    # ========================================================
    # COMPROBACIÓN FINAL
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

        estado["paso"] = "seleccionar_hora"

        horas = buscar_proximas_10_horas()

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Justo esa hora acaba de ocuparse 😕.\n\n"
            "Estas son las nuevas próximas horas "
            "disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "¿Cuál prefieres?"
        )


    # ========================================================
    # CREAR EVENTO
    # ========================================================

    resultado = crear_evento_diego(
        inicio=inicio,
        servicio_codigo=datos["servicio"],
        nombre_cliente=datos["nombre"],
        telefono_cliente=datos["telefono"],
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

    fecha_texto = formato_fecha_larga(
        inicio
    )


    # Guardar teléfono para futuras reservas.
    telefono_guardar = telefono


    # Limpiar flujo.
    resetear_reserva(estado)

    estado["datos_reserva"]["telefono"] = (
        telefono_guardar
    )


    return (
        "✅ ¡Reserva confirmada!\n\n"
        f"✂️ Servicio: {servicio['nombre']}\n"
        f"💰 Valor: ${servicio['precio']:,}".replace(",", ".")
        + "\n"
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

        service = obtener_calendar_service()

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
                    f"Valor: ${servicio['precio']}\n"
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

                    "duracion":
                        str(DURACION_RESERVA),

                    "origen":
                        (
                            f"Asistente Virtual "
                            f"{ESTILISTA_NOMBRE}"
                        ),
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

        print(
            "EVENTO GOOGLE CREADO:",
            resultado.get("id")
        )

        return {
            "ok": True,
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
            "ok": False,
            "error": str(e)
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
                        "¡Hola! 👋 Soy el Asistente Virtual "
                        "de Estilista Diego ✂️\n\n"
                        "¿Cómo estás?"
                    ),
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

            session["historial"].append({

                "role":
                    "user",

                "content":
                    pregunta,
            })


            # =================================================
            # CANCELACIÓN
            # =================================================

            if (
                session.get("modo_agendar")
                and usuario_no_quiere(pregunta)
            ):

                estado = {
                    "modo_agendar":
                        True,

                    "paso":
                        session.get(
                            "paso",
                            "inicio"
                        ),

                    "horas_ofrecidas":
                        session.get(
                            "horas_ofrecidas",
                            []
                        ),

                    "datos_reserva":
                        session["datos_reserva"],
                }

                respuesta = (
                    procesar_agenda(
                        estado,
                        pregunta
                    )
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


            # =================================================
            # YA ESTÁ AGENDANDO
            # =================================================

            elif session.get(
                "modo_agendar",
                False
            ):

                estado = {

                    "modo_agendar":
                        True,

                    "paso":
                        session.get(
                            "paso",
                            "inicio"
                        ),

                    "horas_ofrecidas":
                        session.get(
                            "horas_ofrecidas",
                            []
                        ),

                    "datos_reserva":
                        session["datos_reserva"],
                }

                respuesta = (
                    procesar_agenda(
                        estado,
                        pregunta
                    )
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


            # =================================================
            # INICIAR AGENDA
            # =================================================

            elif es_intencion_agendar(
                pregunta
            ):

                session["modo_agendar"] = True
                session["paso"] = "inicio"

                estado = {

                    "modo_agendar":
                        True,

                    "paso":
                        "inicio",

                    "horas_ofrecidas":
                        [],

                    "datos_reserva":
                        session["datos_reserva"],
                }

                # Si el cliente ya escribió servicio
                # junto con la intención de agendar,
                # procesamos directamente.

                respuesta = (
                    procesar_agenda(
                        estado,
                        pregunta
                    )
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


            # =================================================
            # PEDIR SERVICIOS
            # =================================================

            elif pregunta_servicios(
                pregunta
            ):

                respuesta = mostrar_servicios()


            # =================================================
            # CONVERSACIÓN NORMAL
            # =================================================

            else:

                respuesta = responder_openai(
                    session["historial"],
                    pregunta
                )


            session["historial"].append({

                "role":
                    "assistant",

                "content":
                    respuesta,
            })

            session.modified = True


    return render_template_string(
        TEMPLATE,
        historial=session["historial"]
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

        estado["datos_reserva"]["telefono"] = wa_id

        estado["historial"].append({

            "role":
                "user",

            "content":
                text,
        })


        # ====================================================
        # PROCESAR
        # ====================================================

        if estado["modo_agendar"]:

            respuesta = procesar_agenda(
                estado,
                text
            )

        elif es_intencion_agendar(text):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"

            respuesta = procesar_agenda(
                estado,
                text
            )

        elif pregunta_servicios(text):

            respuesta = mostrar_servicios()

        else:

            respuesta = responder_openai(
                estado["historial"],
                text
            )


        estado["historial"].append({

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

        session.modified = True

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
            titulo="Error iniciando Google OAuth",
            mensaje=str(e)
        )


# ============================================================
# GOOGLE CALLBACK
# ============================================================

@app.route("/callback")
def callback():

    error = request.args.get(
        "error"
    )

    if error:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Google rechazó la autorización",
            mensaje=f"Google respondió: {error}"
        )

    code = request.args.get(
        "code"
    )

    if not code:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Falta código OAuth",
            mensaje="Google no entregó el parámetro code."
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
                titulo="Google no entregó refresh token",
                mensaje=(
                    "Google autorizó la aplicación, "
                    "pero no entregó refresh_token. "
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
            token=refresh_token
        )

    except Exception as e:

        print(
            "GOOGLE CALLBACK ERROR:",
            repr(e)
        )

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Error autenticando con Google",
            mensaje=str(e)
        )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
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

<title>
Error Google OAuth
</title>

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
            os.getenv("FLASK_ENV")
            == "development"
        )
    )
