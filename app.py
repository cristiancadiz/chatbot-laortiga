import os
import time
import secrets
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
    abort,
)

from datetime import timedelta, datetime
from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


# ============================================================
# CONFIGURACIÓN
# ============================================================

load_dotenv()

# ------------------------------------------------------------
# IMPORTANTE:
# En Render SIEMPRE usamos HTTPS.
# Solo permitimos HTTP inseguro cuando estamos desarrollando
# explícitamente en local.
# ------------------------------------------------------------

if os.getenv("FLASK_ENV") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
else:
    # Evita que una variable antigua de Render provoque
    # el error:
    # insecure_transport: OAuth 2 MUST utilize https
    os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)


app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY")

if not app.secret_key:
    raise Exception(
        "Falta SECRET_KEY en las variables de entorno."
    )

app.permanent_session_lifetime = timedelta(days=30)


# ============================================================
# OPENAI
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise Exception(
        "Falta OPENAI_API_KEY en las variables de entorno."
    )

client = openai.OpenAI(
    api_key=OPENAI_API_KEY
)


# ============================================================
# CONFIGURACIÓN ESTILISTA
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

DURACION_DEFAULT = int(
    os.getenv(
        "DURACION_DEFAULT",
        "60"
    )
)


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {

    "corte": {
        "nombre": "Corte de cabello",
        "duracion": 45,
        "precio": None,
    },

    "corte_barba": {
        "nombre": "Corte + barba",
        "duracion": 60,
        "precio": None,
    },

    "barba": {
        "nombre": "Arreglo de barba",
        "duracion": 30,
        "precio": None,
    },

    "corte_nino": {
        "nombre": "Corte de niño",
        "duracion": 45,
        "precio": None,
    },

    "perfilado": {
        "nombre": "Perfilado",
        "duracion": 30,
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
    raise Exception(
        "Falta GOOGLE_CLIENT_ID."
    )

if not GOOGLE_CLIENT_SECRET:
    raise Exception(
        "Falta GOOGLE_CLIENT_SECRET."
    )


# ============================================================
# SCOPES
# ============================================================

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
]


# ============================================================
# CREAR FLOW OAUTH
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

        redirect_uri=
            GOOGLE_REDIRECT_URI,
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

if (
    not WHATSAPP_TOKEN
    or not WHATSAPP_PHONE_NUMBER_ID
    or not WHATSAPP_VERIFY_TOKEN
):

    print(
        "⚠️ WhatsApp no está completamente configurado."
    )


# ============================================================
# SESIONES WHATSAPP
# ============================================================

WA_SESSIONS = {}

PROCESSED_MSG_IDS = {}

DEDUP_TTL_SECONDS = 120


# ============================================================
# DEDUPLICACIÓN
# ============================================================

def _dedup_seen(msg_id):

    if not msg_id:
        return False

    now = time.time()

    for key in list(
        PROCESSED_MSG_IDS.keys()
    ):

        if (
            now
            - PROCESSED_MSG_IDS[key]
            > DEDUP_TTL_SECONDS
        ):

            del PROCESSED_MSG_IDS[key]

    if msg_id in PROCESSED_MSG_IDS:
        return True

    PROCESSED_MSG_IDS[msg_id] = now

    return False


# ============================================================
# ENVIAR WHATSAPP
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
            "❌ WhatsApp no configurado."
        )

        return None

    url = (
        f"https://graph.facebook.com/v20.0/"
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
            timeout=20,
        )

        print(
            "📤 WA SEND:",
            response.status_code,
            response.text[:500]
        )

        return response

    except Exception as e:

        print(
            "❌ Error enviando WhatsApp:",
            e
        )

        return None


# ============================================================
# SESIÓN WHATSAPP
# ============================================================

def get_wa_session(wa_id):

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
# CREDENCIALES GOOGLE
# ============================================================

def obtener_credentials_diego():

    if not GOOGLE_REFRESH_TOKEN:

        raise Exception(
            "No existe GOOGLE_REFRESH_TOKEN."
        )

    credentials = Credentials(

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

    return credentials


# ============================================================
# SERVICIO GOOGLE CALENDAR
# ============================================================

def obtener_calendar_service():

    credentials = (
        obtener_credentials_diego()
    )

    service = build(

        "calendar",

        "v3",

        credentials=credentials,

        cache_discovery=False,
    )

    return service


# ============================================================
# FECHA / HORA
# ============================================================

def parse_fecha_hora(texto):

    try:

        zona = pytz.timezone(
            TIMEZONE
        )

        ahora = datetime.now(
            zona
        )

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

        if not resultado:
            return None

        if resultado.tzinfo is None:

            resultado = zona.localize(
                resultado
            )

        return resultado

    except Exception as e:

        print(
            "❌ Error parseando fecha:",
            e
        )

        return None


# ============================================================
# DETECTAR SERVICIO
# ============================================================

def detectar_servicio(texto):

    texto = (
        texto or ""
    ).lower()

    if (
        "corte" in texto
        and "barba" in texto
    ):

        return "corte_barba"

    if (
        "niño" in texto
        or "nino" in texto
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


# ============================================================
# OBTENER SERVICIO
# ============================================================

def obtener_servicio(codigo):

    return SERVICIOS.get(
        codigo,
        SERVICIOS["otro"]
    )


# ============================================================
# VERIFICAR DISPONIBILIDAD
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion
):

    try:

        service = (
            obtener_calendar_service()
        )

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
        )

        body = {

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

        resultado = (
            service
            .freebusy()
            .query(
                body=body
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
            "❌ Error consultando "
            "Google Calendar:",
            e
        )

        return None


# ============================================================
# BUSCAR HORAS DISPONIBLES
# ============================================================

def buscar_horas_disponibles(
    fecha,
    duracion=60
):

    zona = pytz.timezone(
        TIMEZONE
    )

    fecha = fecha.astimezone(
        zona
    )

    hora_inicio = 9

    hora_fin = 20

    resultados = []

    for hora in range(
        hora_inicio,
        hora_fin
    ):

        for minuto in [
            0,
            30
        ]:

            inicio = fecha.replace(

                hour=hora,

                minute=minuto,

                second=0,

                microsecond=0,
            )

            if inicio <= datetime.now(
                zona
            ):

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

            if len(resultados) >= 5:

                return resultados

    return resultados


# ============================================================
# CREAR EVENTO EN CALENDARIO DE DIEGO
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

        servicio = (
            obtener_servicio(
                servicio_codigo
            )
        )

        duracion = servicio[
            "duracion"
        ]

        fin = (
            inicio
            + timedelta(
                minutes=duracion
            )
        )

        titulo = (

            f"{servicio['nombre']} - "
            f"{nombre_cliente}"
        )

        descripcion = (

            "Reserva agendada por "
            "Asistente Virtual de "
            "Estilista Diego.\n\n"

            f"Cliente: {nombre_cliente}\n"

            f"Teléfono: {telefono_cliente}\n"

            f"Servicio: "
            f"{servicio['nombre']}\n"

            f"Duración: "
            f"{duracion} minutos\n"
        )

        evento_body = {

            "summary":
                titulo,

            "description":
                descripcion,

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
                        "Asistente Virtual Diego",
                }
            },
        }

        evento = (

            service
            .events()
            .insert(

                calendarId=
                    CALENDAR_ID,

                body=
                    evento_body,
            )
            .execute()
        )

        return {

            "ok":
                True,

            "evento_id":
                evento.get("id"),

            "link":
                evento.get("htmlLink"),
        }

    except Exception as e:

        print(
            "❌ Error creando evento:",
            e
        )

        return {

            "ok":
                False,

            "error":
                str(e)
        }


# ============================================================
# FORMATO FECHA
# ============================================================

def formato_fecha(fecha):

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
# DETECTAR INTENCIÓN
# ============================================================

def es_intencion_agendar(texto):

    texto = (
        texto or ""
    ).lower()

    palabras = [

        "agendar",

        "agenda",

        "reservar",

        "reserva",

        "cita",

        "hora",

        "turno",

        "barbero",

        "barbería",

        "barberia",

        "estilista",

        "corte",

        "barba",
    ]

    return any(

        palabra in texto

        for palabra in palabras
    )


# ============================================================
# GPT
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    try:

        system_prompt = f"""

Eres el Asistente Virtual de Estilista Diego.

Tu función principal es ayudar a los clientes a reservar
una hora disponible en la agenda de Diego.

NO eres un asistente de LaOrtiga.
NO menciones LaOrtiga.
NO menciones Capitán Planeta.

Habla en español de Chile.

Sé:

- cercano
- amable
- profesional
- breve
- natural

Tu objetivo principal es llevar al cliente a reservar
una hora.

Servicios disponibles:

1. Corte de cabello
2. Corte + barba
3. Arreglo de barba
4. Corte de niño
5. Perfilado
6. Otro servicio

IMPORTANTE:

La agenda que se consulta y modifica es EXCLUSIVAMENTE
la agenda de Diego.

El cliente NO necesita Google Calendar.

El cliente NO necesita iniciar sesión en Google.

El cliente NO debe autorizar Google.

Nunca le pidas al cliente iniciar sesión en Google.

Cuando el cliente quiera reservar, debes obtener:

1. Servicio
2. Día
3. Hora
4. Nombre
5. Teléfono

No inventes disponibilidad.

La disponibilidad real será comprobada por el sistema.

Si una hora está ocupada, ofrece alternativas.

No afirmes que una reserva fue realizada hasta que el
sistema confirme que el evento fue creado correctamente.

Nombre del estilista:

{ESTILISTA_NOMBRE}

Negocio:

{NEGOCIO_NOMBRE}

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

            historial[-10:]

            if historial

            else []
        )

        mensajes.append(

            {
                "role":
                    "user",

                "content":
                    pregunta,
            }
        )

        completion = (

            client
            .chat
            .completions
            .create(

                model="gpt-4o-mini",

                messages=mensajes,

                max_tokens=300,

                temperature=0.5,
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
            "❌ OpenAI error:",
            e
        )

        return (

            "Ups 😅 tuve un pequeño "
            "problema técnico. "
            "¿Me puedes repetir?"
        )


# ============================================================
# GUARDAR HISTORIAL
# ============================================================

def guardar_historial_en_archivo(
    historial
):

    carpeta = (
        "conversaciones_guardadas"
    )

    os.makedirs(
        carpeta,
        exist_ok=True
    )

    timestamp = (
        datetime.now()
        .strftime(
            "%Y-%m-%d_%H-%M-%S"
        )
    )

    ruta = (
        f"{carpeta}/chat_{timestamp}.txt"
    )

    try:

        with open(

            ruta,

            "w",

            encoding="utf-8"

        ) as f:

            for mensaje in historial:

                rol = (

                    "Cliente"

                    if mensaje["role"]
                    == "user"

                    else "Asistente"
                )

                f.write(

                    f"{rol}: "
                    f"{mensaje['content']}\n\n"
                )

    except Exception as e:

        print(
            "⚠️ No pude guardar historial:",
            e
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

    texto_limpio = (
        texto or ""
    ).strip()


    # --------------------------------------------------------
    # SERVICIO
    # --------------------------------------------------------

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto_limpio
        )

        if servicio:

            datos["servicio"] = (
                servicio
            )

            return (

                "Perfecto ✂️\n\n"

                "¿Qué día y a qué hora "
                "te gustaría venir?"
            )

        return (

            "¡Claro! ✂️ ¿Qué servicio "
            "quieres reservar?\n\n"

            "• Corte de cabello\n"

            "• Corte + barba\n"

            "• Arreglo de barba\n"

            "• Corte de niño\n"

            "• Perfilado"
        )


    # --------------------------------------------------------
    # FECHA / HORA
    # --------------------------------------------------------

    if not datos["fecha_hora"]:

        fecha = parse_fecha_hora(
            texto_limpio
        )

        if not fecha:

            return (

                "No alcancé a entender "
                "la fecha y hora 😅\n\n"

                "Por ejemplo: "
                "\"mañana a las 15:00\"."
            )

        servicio = obtener_servicio(
            datos["servicio"]
        )

        disponible = (
            verificar_disponibilidad(

                fecha,

                servicio["duracion"]
            )
        )

        if disponible is None:

            return (

                "No pude consultar la "
                "agenda de Diego en "
                "este momento 😕.\n\n"

                "Inténtalo nuevamente."
            )

        if not disponible:

            alternativas = (
                buscar_horas_disponibles(

                    fecha,

                    servicio["duracion"]
                )
            )

            if alternativas:

                texto_horas = "\n".join(

                    [

                        f"• {h.strftime('%H:%M')}"

                        for h in alternativas
                    ]
                )

                return (

                    "Esa hora ya está ocupada "
                    "en la agenda de Diego 😕.\n\n"

                    "Tengo estas alternativas:\n\n"

                    f"{texto_horas}\n\n"

                    "¿Cuál te acomoda?"
                )

            return (

                "Esa hora está ocupada y "
                "no encontré otras horas "
                "cercanas disponibles 😕.\n\n"

                "¿Quieres probar otro horario?"
            )

        datos["fecha_hora"] = (
            fecha.isoformat()
        )

        return (

            f"¡Perfecto! 🙌\n\n"

            f"Hay disponibilidad el "
            f"{formato_fecha(fecha)}.\n\n"

            "¿Me indicas tu nombre para "
            "dejar la reserva?"
        )


    # --------------------------------------------------------
    # NOMBRE
    # --------------------------------------------------------

    if not datos["nombre"]:

        if len(texto_limpio) < 2:

            return (

                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )

        datos["nombre"] = (
            texto_limpio
        )

        return (

            "Perfecto, "
            f"{datos['nombre']} 👍\n\n"

            "¿Me das tu número de teléfono "
            "para dejarlo asociado a la reserva?"
        )


    # --------------------------------------------------------
    # TELÉFONO
    # --------------------------------------------------------

    if not datos["telefono"]:

        datos["telefono"] = (
            texto_limpio
        )


    # --------------------------------------------------------
    # CREAR RESERVA
    # --------------------------------------------------------

    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )

    servicio = obtener_servicio(
        datos["servicio"]
    )

    # --------------------------------------------------------
    # DOBLE VERIFICACIÓN
    # --------------------------------------------------------

    disponible = (
        verificar_disponibilidad(

            inicio,

            servicio["duracion"]
        )
    )

    if not disponible:

        datos["fecha_hora"] = None

        return (

            "Justo mientras terminábamos "
            "la reserva esa hora se ocupó 😕.\n\n"

            "Dime otra hora y vuelvo a "
            "revisar la agenda de Diego."
        )


    # --------------------------------------------------------
    # CREAR EVENTO
    # --------------------------------------------------------

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
            "❌ Error reserva:",
            resultado["error"]
        )

        return (

            "No pude completar la reserva "
            "en este momento 😕.\n\n"

            "Por favor inténtalo nuevamente."
        )


    servicio_nombre = servicio[
        "nombre"
    ]

    fecha_texto = formato_fecha(
        inicio
    )

    nombre_confirmado = (
        datos["nombre"]
    )

    estado["datos_reserva"] = {

        "servicio": None,

        "fecha_hora": None,

        "nombre": None,

        "telefono": None,
    }

    estado["modo_agendar"] = False

    return (

        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: "
        f"{servicio_nombre}\n"

        f"👤 Cliente: "
        f"{nombre_confirmado}\n"

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

    if "historial" not in session:

        session["historial"] = [

            {
                "role":
                    "assistant",

                "content": (

                    "¡Hola! 👋 Soy el "
                    "Asistente Virtual de "
                    "Estilista Diego ✂️\n\n"

                    "Estoy aquí para ayudarte "
                    "a encontrar y reservar "
                    "una hora disponible."
                ),
            }
        ]

    if "modo_agendar" not in session:

        session["modo_agendar"] = False

    if "datos_reserva" not in session:

        session["datos_reserva"] = {

            "servicio": None,

            "fecha_hora": None,

            "nombre": None,

            "telefono": None,
        }

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

    if "historial" not in session:

        session["historial"] = [

            {
                "role":
                    "assistant",

                "content": (

                    "¡Hola! 👋 Soy el "
                    "Asistente Virtual de "
                    "Estilista Diego ✂️\n\n"

                    "¿Quieres reservar una hora?"
                ),
            }
        ]

    if "modo_agendar" not in session:

        session["modo_agendar"] = False

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

            session["historial"].append(

                {
                    "role":
                        "user",

                    "content":
                        pregunta,
                }
            )


            if (

                es_intencion_agendar(
                    pregunta
                )

                or session.get(
                    "modo_agendar"
                )
            ):

                session["modo_agendar"] = True

                estado = {

                    "modo_agendar":
                        session[
                            "modo_agendar"
                        ],

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

                respuesta = (
                    responder_openai(

                        session[
                            "historial"
                        ],

                        pregunta
                    )
                )


            session["historial"].append(

                {
                    "role":
                        "assistant",

                    "content":
                        respuesta,
                }
            )

            session.modified = True

            guardar_historial_en_archivo(

                session[
                    "historial"
                ]
            )


    return render_template_string(

        TEMPLATE,

        historial=
            session[
                "historial"
            ],
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

    print(
        "📩 WA IN RAW:",
        str(data)[:800]
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

        print(

            "📩 WA:",

            {
                "id":
                    msg_id,

                "from":
                    wa_id,

                "text":
                    text,
            }
        )

        if _dedup_seen(
            msg_id
        ):

            return "ok", 200

        if not wa_id or not text:

            return "ok", 200

        estado = get_wa_session(
            wa_id
        )

        estado["historial"].append(

            {
                "role":
                    "user",

                "content":
                    text,
            }
        )

        if (

            es_intencion_agendar(
                text
            )

            or estado["modo_agendar"]
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

        estado["historial"].append(

            {
                "role":
                    "assistant",

                "content":
                    respuesta,
            }
        )

        wa_send_text(

            wa_id,

            respuesta
        )

    except Exception as e:

        print(
            "❌ Error webhook WA:",
            e
        )

    return "ok", 200


# ============================================================
# ADMIN LOGIN GOOGLE
#
# SOLO DIEGO.
#
# Aquí generamos:
#
# - state
# - code_verifier
#
# y ambos se guardan en la sesión.
#
# Esto corrige:
#
# invalid_grant: Missing code verifier
# ============================================================

@app.route(
    "/admin/login"
)
def admin_login():

    # Limpiamos cualquier OAuth anterior
    session.pop(
        "oauth_state",
        None
    )

    session.pop(
        "oauth_code_verifier",
        None
    )

    flow = crear_google_flow()


    # --------------------------------------------------------
    # PKCE
    # --------------------------------------------------------

    code_verifier = (
        secrets.token_urlsafe(64)
    )

    authorization_url, state = (

        flow.authorization_url(

            access_type="offline",

            include_granted_scopes="true",

            prompt="consent",

            code_challenge_method="S256",

            code_verifier=
                code_verifier,
        )
    )


    # --------------------------------------------------------
    # GUARDAR EN SESIÓN
    # --------------------------------------------------------

    session[
        "oauth_state"
    ] = state

    session[
        "oauth_code_verifier"
    ] = code_verifier

    session.permanent = True

    session.modified = True


    print(
        "🔐 OAuth iniciado."
    )

    print(
        "STATE:",
        state
    )

    return redirect(
        authorization_url
    )


# ============================================================
# CALLBACK GOOGLE
# ============================================================

@app.route(
    "/callback"
)
def callback():

    saved_state = session.get(
        "oauth_state"
    )

    saved_code_verifier = session.get(
        "oauth_code_verifier"
    )


    # --------------------------------------------------------
    # COMPROBAR STATE
    # --------------------------------------------------------

    if not saved_state:

        return (

            """
            <h2>❌ Sesión OAuth no encontrada</h2>

            <p>
            Vuelve a comenzar desde:
            </p>

            <p>
            <a href="/admin/login">
            /admin/login
            </a>
            </p>
            """,

            400
        )


    # --------------------------------------------------------
    # COMPROBAR CODE VERIFIER
    # --------------------------------------------------------

    if not saved_code_verifier:

        return (

            """
            <h2>❌ Code verifier no encontrado</h2>

            <p>
            La sesión OAuth se perdió.
            </p>

            <p>
            Vuelve a comenzar desde:
            </p>

            <p>
            <a href="/admin/login">
            /admin/login
            </a>
            </p>
            """,

            400
        )


    # --------------------------------------------------------
    # CREAR FLOW NUEVAMENTE
    # --------------------------------------------------------

    flow = crear_google_flow()

    flow.state = saved_state

    flow.code_verifier = (
        saved_code_verifier
    )


    # --------------------------------------------------------
    # VALIDAR STATE
    # --------------------------------------------------------

    returned_state = request.args.get(
        "state"
    )

    if returned_state != saved_state:

        return (

            """
            <h2>❌ Error de seguridad OAuth</h2>

            <p>
            El estado OAuth no coincide.
            </p>

            <p>
            Vuelve a comenzar desde:
            </p>

            <p>
            <a href="/admin/login">
            /admin/login
            </a>
            </p>
            """,

            400
        )


    # --------------------------------------------------------
    # SI GOOGLE DEVUELVE ERROR
    # --------------------------------------------------------

    if request.args.get(
        "error"
    ):

        error = request.args.get(
            "error"
        )

        description = request.args.get(
            "error_description",
            ""
        )

        return (

            f"""
            <h2>❌ Google rechazó la autorización</h2>

            <p>
            Error:
            <b>{error}</b>
            </p>

            <p>
            {description}
            </p>

            <p>
            <a href="/admin/login">
            Volver a intentar
            </a>
            </p>
            """,

            400
        )


    # --------------------------------------------------------
    # OBTENER TOKEN
    # --------------------------------------------------------

    try:

        flow.fetch_token(

            authorization_response=
                request.url
        )

    except Exception as e:

        print(
            "❌ ERROR GOOGLE OAUTH:",
            repr(e)
        )

        return (

            f"""
            <h2>❌ Error autenticando con Google</h2>

            <pre>{e}</pre>

            <hr>

            <p>
            Vuelve a comenzar desde:
            </p>

            <p>
            <a href="/admin/login">
            /admin/login
            </a>
            </p>
            """,

            400
        )


    credentials = (
        flow.credentials
    )


    if not credentials:

        return (

            "No se pudo obtener "
            "las credenciales de Google.",

            400
        )


    refresh_token = (
        credentials.refresh_token
    )


    # --------------------------------------------------------
    # GOOGLE NO ENTREGÓ REFRESH TOKEN
    # --------------------------------------------------------

    if not refresh_token:

        return (

            """
            <!DOCTYPE html>

            <html lang="es">

            <head>

            <meta charset="UTF-8">

            <title>
            Refresh Token no recibido
            </title>

            </head>

            <body>

            <h2>
            ⚠️ Google no entregó refresh token
            </h2>

            <p>
            Vuelve a comenzar desde:
            </p>

            <p>
            <a href="/admin/login">
            /admin/login
            </a>
            </p>

            <p>
            Si Google ya había autorizado esta
            aplicación, revoca el acceso anterior
            y vuelve a autorizar.
            </p>

            </body>

            </html>
            """,

            400
        )


    # --------------------------------------------------------
    # LIMPIAR SESIÓN OAUTH
    # --------------------------------------------------------

    session.pop(
        "oauth_state",
        None
    )

    session.pop(
        "oauth_code_verifier",
        None
    )

    session.modified = True


    # --------------------------------------------------------
    # MOSTRAR REFRESH TOKEN
    # --------------------------------------------------------

    return render_template_string(

        """
        <!DOCTYPE html>

        <html lang="es">

        <head>

        <meta charset="UTF-8">

        <meta name="viewport"
              content="width=device-width, initial-scale=1">

        <title>
        Google Calendar autorizado
        </title>

        <style>

        body {

            font-family: Arial, sans-serif;

            max-width: 800px;

            margin: 50px auto;

            padding: 20px;

            background: #f5f5f5;
        }

        .box {

            background: white;

            padding: 30px;

            border-radius: 15px;

            box-shadow:
                0 5px 20px
                rgba(0,0,0,.10);
        }

        textarea {

            width: 100%;

            height: 120px;

            margin-top: 10px;

            padding: 10px;

            box-sizing: border-box;
        }

        .ok {

            color: #087f23;
        }

        </style>

        </head>

        <body>

        <div class="box">

        <h1 class="ok">
        ✅ Google Calendar autorizado
        </h1>

        <p>
        La cuenta de Google fue autorizada
        correctamente.
        </p>

        <p>
        Este es el
        <b>GOOGLE_REFRESH_TOKEN</b>:
        </p>

        <textarea readonly>{{ token }}</textarea>

        <h3>
        Ahora debes copiarlo a Render
        </h3>

        <p>
        Render → Environment →
        <b>GOOGLE_REFRESH_TOKEN</b>
        </p>

        <p>
        Después de guardar la variable,
        haz un nuevo deploy.
        </p>

        <p>
        ⚠️ No compartas este token.
        </p>

        </div>

        </body>

        </html>
        """,

        token=refresh_token
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

<link
href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"
rel="stylesheet"
>

<style>

* {
    box-sizing: border-box;
}

body {

    font-family:
        'Inter',
        sans-serif;

    background:
        #f3f4f6;

    margin: 0;

    padding: 0;
}

#chat-toggle-btn {

    position: fixed;

    bottom: 20px;

    right: 20px;

    width: 64px;

    height: 64px;

    border: none;

    border-radius: 50%;

    background:
        #111827;

    color: white;

    font-size: 28px;

    cursor: pointer;

    box-shadow:
        0 5px 20px
        rgba(0,0,0,.20);

    z-index: 1000;
}

#chat-container {

    position: fixed;

    bottom: 95px;

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

    z-index: 999;
}

#chat-header {

    padding: 16px;

    background:
        #111827;

    color: white;

    display: flex;

    align-items: center;
}

.avatar {

    width: 46px;

    height: 46px;

    border-radius: 50%;

    background:
        #374151;

    display: flex;

    align-items: center;

    justify-content: center;

    font-size: 23px;

    margin-right: 12px;
}

.name {

    font-weight: 700;

    font-size: 15px;
}

.subtitle {

    font-size: 11px;

    opacity: .75;

    margin-top: 3px;
}

#chat-messages {

    flex: 1;

    padding: 15px;

    overflow-y: auto;

    background:
        #f9fafb;

    display: flex;

    flex-direction: column;
}

.msg {

    margin-bottom: 10px;

    padding:
        10px 13px;

    border-radius: 16px;

    max-width: 84%;

    word-wrap: break-word;

    white-space: pre-wrap;

    font-size: 14px;

    line-height: 1.4;
}

.bot {

    background:
        #111827;

    color: white;

    align-self: flex-start;

    border-bottom-left-radius: 4px;
}

.user {

    background:
        #e5e7eb;

    color: #111827;

    align-self: flex-end;

    border-bottom-right-radius: 4px;
}

#chat-input-form {

    display: flex;

    border-top:
        1px solid #e5e7eb;

    background: white;

    padding: 8px;
}

#chat-input {

    flex: 1;

    border: none;

    outline: none;

    padding: 12px;

    font-size: 14px;
}

#chat-send {

    border: none;

    background:
        #111827;

    color: white;

    width: 46px;

    height: 42px;

    border-radius: 10px;

    cursor: pointer;

    font-size: 18px;
}

@media (max-width: 600px) {

    #chat-container {

        right: 10px;

        left: 10px;

        bottom: 85px;

        width: auto;

        height: 70vh;
    }

}

</style>

</head>

<body>

<button
    id="chat-toggle-btn">
    💬
</button>

<div
    id="chat-container"
>

    <div
        id="chat-header"
    >

        <div
            class="avatar"
        >
            ✂️
        </div>

        <div>

            <div
                class="name"
            >
                Asistente Virtual
                de Estilista Diego
            </div>

            <div
                class="subtitle"
            >
                Agenda de horas disponibles
            </div>

        </div>

    </div>

    <div
        id="chat-messages"
    >

        {% for m in historial %}

            <div
                class="msg
                {% if m['role'] == 'user' %}
                    user
                {% else %}
                    bot
                {% endif %}"
            >

                {{ m['content'] | e }}

            </div>

        {% endfor %}

    </div>

    <form
        id="chat-input-form"
        method="POST"
    >

        <input
            type="text"
            id="chat-input"
            name="pregunta"
            placeholder="Ej: Quiero agendar un corte..."
            autocomplete="off"
            required
        >

        <button
            id="chat-send"
            type="submit"
        >
            ➤
        </button>

    </form>

</div>

<script>

const toggleBtn =
    document.getElementById(
        'chat-toggle-btn'
    );

const chatBox =
    document.getElementById(
        'chat-container'
    );

const chatMessages =
    document.getElementById(
        'chat-messages'
    );

const input =
    document.getElementById(
        'chat-input'
    );

toggleBtn.onclick = () => {

    if (
        chatBox.style.display
        === 'none'
    ) {

        chatBox.style.display =
            'flex';

        scrollToBottom();

        input.focus();

    } else {

        chatBox.style.display =
            'none';

    }

};

function scrollToBottom() {

    chatMessages.scrollTop =
        chatMessages.scrollHeight;

}

window.onload = () => {

    chatBox.style.display =
        'flex';

    scrollToBottom();

    input.focus();

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

    debug = (
        os.getenv(
            "FLASK_ENV"
        )
        == "development"
    )

    app.run(

        host="0.0.0.0",

        port=port,

        debug=debug
    )
