import os
import re
import hashlib
import requests
import pytz
from openai import OpenAI
import psycopg2
import psycopg2.extras

from flask import (
    Flask,
    redirect,
    url_for,
    session,
    request,
    render_template_string,
)

from twilio.twiml.messaging_response import MessagingResponse

from datetime import timedelta, datetime
from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from werkzeug.middleware.proxy_fix import ProxyFix

APP_VERSION = "2026-08-16-DEFINITIVO-TWILIO-RESERVAS-V9"


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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

if not OPENAI_API_KEY:
    print("ADVERTENCIA: falta OPENAI_API_KEY.")

client = None

if OPENAI_API_KEY:
    client = OpenAI(
        api_key=OPENAI_API_KEY
    )


# ============================================================
# POSTGRESQL
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print(
        "ADVERTENCIA: falta DATABASE_URL. "
        "Las conversaciones no podrán guardarse."
    )


def db_connect():
    if not DATABASE_URL:
        return None

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


def init_database():

    if not DATABASE_URL:
        print(
            "DATABASE_URL no configurada. "
            "Se omitirá PostgreSQL."
        )
        return

    conn = None

    try:

        conn = db_connect()

        cur = conn.cursor()

        # ====================================================
        # CONVERSACIONES
        # ====================================================

        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversaciones (
                id SERIAL PRIMARY KEY,
                cliente_id VARCHAR(120) NOT NULL,
                canal VARCHAR(30) NOT NULL DEFAULT 'web',
                nombre VARCHAR(255),
                telefono VARCHAR(100),
                correo VARCHAR(255),
                servicio VARCHAR(255),
                fecha_reserva TIMESTAMPTZ,
                meet_url TEXT,
                estado VARCHAR(50) DEFAULT 'activa',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # ====================================================
        # MENSAJES
        # ====================================================

        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensajes (
                id SERIAL PRIMARY KEY,
                conversacion_id INTEGER
                    REFERENCES conversaciones(id)
                    ON DELETE CASCADE,
                role VARCHAR(30) NOT NULL,
                contenido TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        # ====================================================
        # RESERVAS
        # ====================================================

        cur.execute("""
            CREATE TABLE IF NOT EXISTS reservas (
                id SERIAL PRIMARY KEY,
                conversacion_id INTEGER
                    REFERENCES conversaciones(id)
                    ON DELETE SET NULL,
                cliente_id VARCHAR(120),
                nombre VARCHAR(255),
                telefono VARCHAR(100),
                correo VARCHAR(255),
                servicio VARCHAR(255),
                servicio_codigo VARCHAR(100),
                inicio TIMESTAMPTZ,
                fin TIMESTAMPTZ,
                google_event_id VARCHAR(255),
                meet_url TEXT,
                estado VARCHAR(50) DEFAULT 'confirmada',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversaciones_cliente
            ON conversaciones(cliente_id);
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_mensajes_conversacion
            ON mensajes(conversacion_id);
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservas_inicio
            ON reservas(inicio);
        """)

        conn.commit()

        cur.close()

        print("PostgreSQL inicializado correctamente.")

    except Exception as e:

        print(
            "ERROR INICIALIZANDO POSTGRES:",
            repr(e)
        )

        if conn:
            conn.rollback()

    finally:

        if conn:
            conn.close()


# ============================================================
# BASE DE DATOS - CONVERSACIONES
# ============================================================

def obtener_conversacion(
    cliente_id,
    canal="web"
):

    if not DATABASE_URL:
        return None

    conn = None

    try:

        conn = db_connect()

        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute(
            """
            SELECT *
            FROM conversaciones
            WHERE cliente_id = %s
              AND canal = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                cliente_id,
                canal,
            )
        )

        row = cur.fetchone()

        cur.close()

        return row

    except Exception as e:

        print(
            "ERROR OBTENIENDO CONVERSACIÓN:",
            repr(e)
        )

        return None

    finally:

        if conn:
            conn.close()


def crear_conversacion(
    cliente_id,
    canal="web"
):

    if not DATABASE_URL:
        return None

    conn = None

    try:

        conn = db_connect()

        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO conversaciones
            (
                cliente_id,
                canal
            )
            VALUES (%s, %s)
            RETURNING id
            """,
            (
                cliente_id,
                canal,
            )
        )

        conversation_id = cur.fetchone()[0]

        conn.commit()

        cur.close()

        return conversation_id

    except Exception as e:

        print(
            "ERROR CREANDO CONVERSACIÓN:",
            repr(e)
        )

        if conn:
            conn.rollback()

        return None

    finally:

        if conn:
            conn.close()


def asegurar_conversacion(
    cliente_id,
    canal="web"
):

    existente = obtener_conversacion(
        cliente_id,
        canal
    )

    if existente:
        return existente["id"]

    return crear_conversacion(
        cliente_id,
        canal
    )


def guardar_mensaje(
    cliente_id,
    canal,
    role,
    contenido
):

    if not DATABASE_URL:
        return

    try:

        conversation_id = asegurar_conversacion(
            cliente_id,
            canal
        )

        if not conversation_id:
            return

        conn = db_connect()

        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO mensajes
            (
                conversacion_id,
                role,
                contenido
            )
            VALUES (%s, %s, %s)
            """,
            (
                conversation_id,
                role,
                contenido,
            )
        )

        cur.execute(
            """
            UPDATE conversaciones
            SET updated_at = NOW()
            WHERE id = %s
            """,
            (
                conversation_id,
            )
        )

        conn.commit()

        cur.close()
        conn.close()

    except Exception as e:

        print(
            "ERROR GUARDANDO MENSAJE:",
            repr(e)
        )


def actualizar_conversacion_datos(
    cliente_id,
    canal,
    nombre=None,
    telefono=None,
    correo=None,
    servicio=None,
    fecha_reserva=None,
    meet_url=None,
    estado=None
):

    if not DATABASE_URL:
        return

    try:

        conversation_id = asegurar_conversacion(
            cliente_id,
            canal
        )

        conn = db_connect()

        cur = conn.cursor()

        cur.execute(
            """
            UPDATE conversaciones
            SET
                nombre = COALESCE(%s, nombre),
                telefono = COALESCE(%s, telefono),
                correo = COALESCE(%s, correo),
                servicio = COALESCE(%s, servicio),
                fecha_reserva = COALESCE(%s, fecha_reserva),
                meet_url = COALESCE(%s, meet_url),
                estado = COALESCE(%s, estado),
                updated_at = NOW()
            WHERE id = %s
            """,
            (
                nombre,
                telefono,
                correo,
                servicio,
                fecha_reserva,
                meet_url,
                estado,
                conversation_id,
            )
        )

        conn.commit()

        cur.close()
        conn.close()

    except Exception as e:

        print(
            "ERROR ACTUALIZANDO CONVERSACIÓN:",
            repr(e)
        )


def guardar_reserva_db(
    cliente_id,
    canal,
    datos,
    inicio,
    fin,
    evento_id,
    meet_url
):

    if not DATABASE_URL:
        return

    try:

        conversation_id = asegurar_conversacion(
            cliente_id,
            canal
        )

        servicio = obtener_servicio(
            datos["servicio"]
        )

        conn = db_connect()

        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO reservas
            (
                conversacion_id,
                cliente_id,
                nombre,
                telefono,
                correo,
                servicio,
                servicio_codigo,
                inicio,
                fin,
                google_event_id,
                meet_url
            )
            VALUES
            (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                conversation_id,
                cliente_id,
                datos["nombre"],
                datos["telefono"],
                datos["correo"],
                servicio["nombre"],
                datos["servicio"],
                inicio,
                fin,
                evento_id,
                meet_url,
            )
        )

        conn.commit()

        cur.close()
        conn.close()

    except Exception as e:

        print(
            "ERROR GUARDANDO RESERVA:",
            repr(e)
        )


# ============================================================
# CONFIGURACIÓN DEL NEGOCIO
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
# FECHA / HORA
# ============================================================

def obtener_zona():
    return pytz.timezone(TIMEZONE)


def ahora_local():

    return datetime.now(
        obtener_zona()
    )


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

    for a, b in reemplazos.items():
        texto = texto.replace(a, b)

    return texto


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

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return fecha.weekday() in DIAS_ATENCION


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
# SERVICIOS
# ============================================================

def obtener_servicio(codigo):

    return SERVICIOS.get(
        codigo,
        {
            "nombre": "Servicio",
            "duracion": 60,
            "precio": 20000,
        }
    )


def mostrar_servicios():

    return (
        "Claro  Estos son nuestros servicios:\n\n"
        "1. Corte de cabello — $20.000\n"
        "2. Corte + barba — $20.000\n"
        "3. Arreglo de barba — $20.000\n"
        "4. Corte de niño — $20.000\n"
        "5. Perfilado — $20.000\n\n"
        "Si quieres reservar, escríbeme el número "
        "del servicio que prefieres. "
    )


def detectar_servicio_por_numero(texto):

    match = re.fullmatch(
        r"\s*([1-5])\s*",
        texto or ""
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
# GOOGLE CALENDAR DISPONIBILIDAD
# ============================================================

def verificar_disponibilidad(
    inicio,
    duracion=60
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

        busy = calendario.get(
            "busy",
            []
        )

        return len(busy) == 0

    except Exception as e:

        print(
            "ERROR FREEBUSY:",
            repr(e)
        )

        return None


# ============================================================
# 10 PRÓXIMAS HORAS
# ============================================================

def buscar_proximas_10_horas():

    ahora = ahora_local()

    resultados = []

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

            if inicio <= ahora:
                continue

            disponible = verificar_disponibilidad(
                inicio,
                DURACION_RESERVA
            )

            print(
                "HORA:",
                inicio,
                "DISPONIBLE:",
                disponible
            )

            if disponible is True:

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
# DETECTAR INTENCIONES
# ============================================================

def pregunta_servicios(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "servicios",
        "servicio",
        "precios",
        "precio",
        "cuanto cuesta",
        "cuanto sale",
        "valor",
        "valores",
        "tarifa",
        "cortes",
        "barberia",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


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


def usuario_no_quiere(texto):

    texto_n = normalizar_texto(texto)

    patrones = [
        "no quiero",
        "no gracias",
        "gracias no",
        "dejalo",
        "olvidalo",
        "cancelar",
        "cancela",
        "no por ahora",
        "despues",
        "no necesito",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


# ============================================================
# OPENAI - CONVERSACIÓN NATURAL
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    if not client:

        return (
            "¡Hola! 😊 Qué gusto saludarte. "
            "Si quieres, puedo mostrarte los servicios "
            "o ayudarte a reservar una hora."
        )

    system_prompt = f"""
Eres el Asistente Virtual de Estilista {ESTILISTA_NOMBRE}.

Atiendes clientes por WhatsApp en español natural de Chile.

Tu conversación debe sentirse natural, breve y humana.

IMPORTANTE:

Si el cliente dice "hola", saluda normalmente.

Si continúa la conversación, no vuelvas a empezar desde cero
ni repitas el saludo anterior.

Tu objetivo es ayudar al cliente con:

1. Conocer los servicios.
2. Consultar precios.
3. Reservar una hora.

SERVICIOS:

1. Corte de cabello — $20.000
2. Corte + barba — $20.000
3. Arreglo de barba — $20.000
4. Corte de niño — $20.000
5. Perfilado — $20.000

HORARIO:

Lunes a sábado.
10:00 a 18:00.
Cada reserva dura 1 hora.
Última hora de inicio: 17:00.
Domingo cerrado.

Si el cliente quiere reservar, la aplicación se encargará
del proceso de agenda y de consultar disponibilidad real.

Nunca inventes horas disponibles.

No confirmes una reserva por tu cuenta.

No hables de APIs, programación, bases de datos,
Google Calendar, Twilio ni sistemas internos.

Si falta información para entender al cliente,
pregunta de manera breve y natural.

Nombre del estilista:
{ESTILISTA_NOMBRE}
"""

    mensajes = [
        {
            "role": "system",
            "content": system_prompt
        }
    ]

    ultimos = historial[-15:]

    for item in ultimos:

        if (
            item.get("role")
            in ("user", "assistant")
            and item.get("content")
        ):

            mensajes.append({
                "role": item["role"],
                "content": item["content"]
            })

    # Evita duplicar el mismo mensaje de usuario
    if not (
        mensajes
        and mensajes[-1]["role"] == "user"
        and mensajes[-1]["content"] == pregunta
    ):

        mensajes.append({
            "role": "user",
            "content": pregunta
        })

    try:

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=mensajes,
        )

        respuesta = (
            response
            .choices[0]
            .message
            .content
            or ""
        ).strip()

        if respuesta:
            return respuesta

        return (
            "¿Te gustaría conocer los servicios "
            "o reservar una hora? 😊"
        )

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

        import traceback

        print(
            traceback.format_exc()
        )

        return (
            "Disculpa 🙏 Tuve un problema procesando "
            "el mensaje. Intenta nuevamente."
        )


# ============================================================
# ESTADO DE RESERVA
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
        "correo": None,
    }


# ============================================================
# GOOGLE MEET + CALENDAR
# ============================================================

def crear_evento_diego(
    inicio,
    servicio_codigo,
    nombre_cliente,
    telefono_cliente,
    correo_cliente
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
                    "Reserva creada por el "
                    "Asistente Virtual de "
                    f"Estilista {ESTILISTA_NOMBRE}.\n\n"
                    f"Cliente: {nombre_cliente}\n"
                    f"Teléfono: {telefono_cliente}\n"
                    f"Correo: {correo_cliente}\n"
                    f"Servicio: {servicio['nombre']}\n"
                    f"Valor: ${servicio['precio']}\n"
                    f"Duración: {DURACION_RESERVA} minutos\n"
                    "Origen: Asistente Virtual"
                ),

            "start": {
                "dateTime": inicio.isoformat(),
                "timeZone": TIMEZONE,
            },

            "end": {
                "dateTime": fin.isoformat(),
                "timeZone": TIMEZONE,
            },

            "attendees": [
                {
                    "email": correo_cliente,
                    "displayName": nombre_cliente,
                }
            ],

            "conferenceData": {

                "createRequest": {

                    "requestId":
                        hashlib.sha256(
                            (
                                str(inicio)
                                + correo_cliente
                                + str(datetime.now())
                            ).encode()
                        ).hexdigest()[:32],

                    "conferenceSolutionKey": {
                        "type": "hangoutsMeet"
                    }
                }
            },

            "extendedProperties": {

                "private": {

                    "cliente":
                        nombre_cliente,

                    "telefono":
                        telefono_cliente,

                    "correo":
                        correo_cliente,

                    "servicio":
                        servicio["nombre"],

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
                body=evento,
                conferenceDataVersion=1,
                sendUpdates="all",
            )
            .execute()
        )

        meet_url = None

        conference_data = resultado.get(
            "conferenceData",
            {}
        )

        entry_points = conference_data.get(
            "entryPoints",
            []
        )

        for entry in entry_points:

            if (
                entry.get("entryPointType")
                == "video"
            ):

                meet_url = entry.get(
                    "uri"
                )

                break

        if not meet_url:

            meet_url = (
                resultado
                .get("hangoutLink")
            )

        print(
            "EVENTO GOOGLE CREADO:",
            resultado.get("id")
        )

        print(
            "GOOGLE MEET:",
            meet_url
        )

        return {
            "ok": True,
            "evento_id":
                resultado.get("id"),
            "link":
                resultado.get("htmlLink"),
            "meet_url":
                meet_url,
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
# RESERVA CON PROTECCIÓN DE CONCURRENCIA
# ============================================================

def crear_reserva_segura(
    inicio,
    datos,
    cliente_id,
    canal
):

    """
    PostgreSQL advisory lock evita que dos clientes
    puedan intentar reservar simultáneamente la misma
    hora desde nuestra aplicación.
    """

    if not DATABASE_URL:

        return {
            "ok": False,
            "error":
                "DATABASE_URL no configurada."
        }

    conn = None

    try:

        conn = db_connect()

        conn.autocommit = False

        cur = conn.cursor()

        # ====================================================
        # LOCK POR HORA
        # ====================================================

        clave = int(
            hashlib.sha256(
                inicio.isoformat().encode()
            ).hexdigest()[:15],
            16
        )

        cur.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (clave,)
        )

        # ====================================================
        # VOLVER A COMPROBAR GOOGLE
        # ====================================================

        disponible = verificar_disponibilidad(
            inicio,
            DURACION_RESERVA
        )

        if disponible is not True:

            conn.rollback()

            return {
                "ok": False,
                "ocupada": True,
            }

        # ====================================================
        # CREAR EVENTO
        # ====================================================

        resultado = crear_evento_diego(
            inicio=inicio,
            servicio_codigo=datos["servicio"],
            nombre_cliente=datos["nombre"],
            telefono_cliente=datos["telefono"],
            correo_cliente=datos["correo"],
        )

        if not resultado["ok"]:

            conn.rollback()

            return {
                "ok": False,
                "error":
                    resultado.get("error")
            }

        fin = inicio + timedelta(
            minutes=DURACION_RESERVA
        )

        servicio = obtener_servicio(
            datos["servicio"]
        )

        conversation_id = asegurar_conversacion(
            cliente_id,
            canal
        )

        # ====================================================
        # GUARDAR RESERVA
        # ====================================================

        cur.execute(
            """
            INSERT INTO reservas
            (
                conversacion_id,
                cliente_id,
                nombre,
                telefono,
                correo,
                servicio,
                servicio_codigo,
                inicio,
                fin,
                google_event_id,
                meet_url
            )
            VALUES
            (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                conversation_id,
                cliente_id,
                datos["nombre"],
                datos["telefono"],
                datos["correo"],
                servicio["nombre"],
                datos["servicio"],
                inicio,
                fin,
                resultado["evento_id"],
                resultado["meet_url"],
            )
        )

        conn.commit()

        cur.close()

        return {
            "ok": True,
            "evento_id":
                resultado["evento_id"],
            "meet_url":
                resultado["meet_url"],
        }

    except Exception as e:

        print(
            "ERROR RESERVA SEGURA:",
            repr(e)
        )

        if conn:
            conn.rollback()

        return {
            "ok": False,
            "error": str(e)
        }

    finally:

        if conn:
            conn.close()


# ============================================================
# PROCESAR AGENDA
# ============================================================

def procesar_agenda(
    estado,
    texto,
    cliente_id,
    canal
):

    datos = estado["datos_reserva"]

    texto = (
        texto or ""
    ).strip()


    # ========================================================
    # CANCELAR
    # ========================================================

    if usuario_no_quiere(texto):

        resetear_reserva(
            estado
        )

        return (
            "No hay problema  "
            "Cuando quieras conocer los servicios "
            "o reservar una hora con Diego, "
            "aquí estaré. ¡Que estés muy bien! "
        )


    # ========================================================
    # SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if servicio:

            datos["servicio"] = servicio

            servicio_info = obtener_servicio(
                servicio
            )

            horas = buscar_proximas_10_horas()

            if not horas:

                return (
                    f"Perfecto  Elegiste "
                    f"{servicio_info['nombre']}.\n\n"
                    "Pero por ahora no encontré "
                    "horas disponibles."
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            estado["paso"] = "seleccionar_hora"

            precio = (
                f"${servicio_info['precio']:,}"
                .replace(",", ".")
            )

            return (
                f"Perfecto \n\n"
                f" {servicio_info['nombre']}\n"
                f" {precio}\n\n"
                "Estas son las próximas "
                "10 horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "Respóndeme con el número de la hora "
                "que prefieras, del 1 al 10."
            )

        return mostrar_servicios()


    # ========================================================
    # HORA
    # ========================================================

    if estado["paso"] == "seleccionar_hora":

        match = re.fullmatch(
            r"\s*(\d{1,2})\s*",
            texto
        )

        if not match:

            return (
                "Indícame el número de la hora "
                "que prefieres .\n\n"
                "Por ejemplo: 1"
            )

        numero = int(
            match.group(1)
        )

        horas_guardadas = (
            estado
            .get(
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
                f"{len(horas_guardadas)}, por favor ."
            )

        fecha_hora = datetime.fromisoformat(
            horas_guardadas[numero - 1]
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
                "en este momento .\n\n"
                "Intenta nuevamente en unos segundos."
            )

        if not disponible:

            horas = buscar_proximas_10_horas()

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Esa hora acaba de ocuparse .\n\n"
                "Actualicé las horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
            )

        datos["fecha_hora"] = (
            fecha_hora.isoformat()
        )

        estado["paso"] = "nombre"

        return (
            "¡Perfecto! \n\n"
            f"Te reservamos "
            f"{formato_fecha_larga(fecha_hora)}.\n\n"
            "¿Me indicas tu nombre?"
        )


    # ========================================================
    # NOMBRE
    # ========================================================

    if estado["paso"] == "nombre":

        if len(texto) < 2:

            return (
                "¿Me indicas tu nombre, por favor? "
            )

        datos["nombre"] = texto

        actualizar_conversacion_datos(
            cliente_id,
            canal,
            nombre=texto
        )

        if (
            canal == "whatsapp"
            and datos.get("telefono")
        ):

            actualizar_conversacion_datos(
                cliente_id,
                canal,
                telefono=datos["telefono"]
            )

            estado["paso"] = "correo"

            return (
                f"Perfecto, {texto} 😊\n\n"
                "Ya tengo tu número de WhatsApp.\n\n"
                "¿Cuál es tu correo electrónico?\n\n"
                "Lo usaremos para enviarte la invitación "
                "de Google Calendar con la cita y el enlace "
                "de Google Meet."
            )

        estado["paso"] = "telefono"

        return (
            f"Perfecto, {texto} 😊\n\n"
            "¿Cuál es tu número de teléfono? "
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if estado["paso"] == "telefono":

        # En WhatsApp/Twilio el número ya viene identificado
        # automáticamente en el campo From.
        if (
            canal == "whatsapp"
            and datos.get("telefono")
        ):

            actualizar_conversacion_datos(
                cliente_id,
                canal,
                telefono=datos["telefono"]
            )

            estado["paso"] = "correo"

            return (
                "Perfecto 😊\n\n"
                "Ya tengo tu número de WhatsApp.\n\n"
                "¿Cuál es tu correo electrónico?\n\n"
                "Lo usaremos para enviarte la invitación "
                "de Google Calendar con la cita y el enlace "
                "de Google Meet."
            )

        telefono_limpio = re.sub(
            r"[^\d+]",
            "",
            texto
        )

        if len(
            re.sub(
                r"\D",
                "",
                telefono_limpio
            )
        ) < 8:

            return (
                "¿Me indicas un número de teléfono "
                "válido, por favor? "
            )

        datos["telefono"] = telefono_limpio

        actualizar_conversacion_datos(
            cliente_id,
            canal,
            telefono=telefono_limpio
        )

        estado["paso"] = "correo"

        return (
            "Perfecto 😊\n\n"
            "¿Cuál es tu correo electrónico?\n\n"
            "Lo usaremos para enviarte la invitación "
            "de Google Calendar con la cita y el enlace "
            "de Google Meet."
        )


    # ========================================================
    # CORREO
    # ========================================================

    if estado["paso"] == "correo":

        correo = texto.lower().strip()

        patron_correo = (
            r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        )

        if not re.match(
            patron_correo,
            correo
        ):

            return (
                "Parece que el correo no está correcto .\n\n"
                "Escríbelo nuevamente, por ejemplo:\n"
                "nombre@gmail.com"
            )

        datos["correo"] = correo

        actualizar_conversacion_datos(
            cliente_id,
            canal,
            correo=correo
        )

        estado["paso"] = "confirmar"

        return completar_reserva(
            estado,
            cliente_id,
            canal
        )


    return completar_reserva(
        estado,
        cliente_id,
        canal
    )


# ============================================================
# COMPLETAR RESERVA
# ============================================================

def completar_reserva(
    estado,
    cliente_id,
    canal
):

    datos = estado["datos_reserva"]

    if not datos["servicio"]:

        estado["paso"] = "servicio"

        return mostrar_servicios()

    if not datos["fecha_hora"]:

        estado["paso"] = "seleccionar_hora"

        horas = buscar_proximas_10_horas()

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Estas son las próximas horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}"
        )

    if not datos["nombre"]:

        estado["paso"] = "nombre"

        return (
            "¿Me indicas tu nombre? "
        )

    if not datos["telefono"]:

        estado["paso"] = "telefono"

        return (
            "¿Cuál es tu número de teléfono? "
        )

    if not datos["correo"]:

        estado["paso"] = "correo"

        return (
            "¿Cuál es tu correo electrónico? "
        )

    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )

    # ========================================================
    # RESERVA SEGURA
    # ========================================================

    resultado = crear_reserva_segura(
        inicio=inicio,
        datos=datos,
        cliente_id=cliente_id,
        canal=canal
    )

    if resultado.get("ocupada"):

        datos["fecha_hora"] = None

        estado["paso"] = "seleccionar_hora"

        horas = buscar_proximas_10_horas()

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Justo esa hora acaba de ocuparse .\n\n"
            "Estas son las nuevas próximas "
            "horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "¿Cuál prefieres?"
        )

    if not resultado["ok"]:

        print(
            "ERROR RESERVANDO:",
            resultado.get("error")
        )

        return (
            "No pude completar la reserva "
            "en este momento .\n\n"
            "Intenta nuevamente en unos segundos."
        )

    servicio = obtener_servicio(
        datos["servicio"]
    )

    meet_url = resultado.get(
        "meet_url"
    )

    actualizar_conversacion_datos(
        cliente_id,
        canal,
        nombre=datos["nombre"],
        telefono=datos["telefono"],
        correo=datos["correo"],
        servicio=servicio["nombre"],
        fecha_reserva=inicio,
        meet_url=meet_url,
        estado="reserva_confirmada"
    )

    fecha_texto = formato_fecha_larga(
        inicio
    )

    # ========================================================
    # GUARDAR MENSAJE DE CONFIRMACIÓN
    # ========================================================

    # Antes de resetear guardamos el estado.
    telefono_guardar = datos["telefono"]

    resetear_reserva(
        estado
    )

    estado["datos_reserva"]["telefono"] = (
        telefono_guardar
    )

    precio = (
        f"${servicio['precio']:,}"
        .replace(",", ".")
    )

    respuesta = (
        " ¡Reserva confirmada!\n\n"
        f" Servicio: {servicio['nombre']}\n"
        f" Valor: {precio}\n"
        f" Cliente: {datos['nombre']}\n"
        f" Teléfono: {datos['telefono']}\n"
        f" Correo: {datos['correo']}\n"
        f" {fecha_texto}\n\n"
        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"
    )

    if meet_url:

        respuesta += (
            " Google Meet:\n"
            f"{meet_url}\n\n"
            "La invitación de Google Calendar fue enviada "
            "al correo indicado.\n\n"
        )

    else:

        respuesta += (
            "La invitación de Google Calendar fue enviada "
            "al correo indicado.\n\n"
        )

    respuesta += (
        "La atención dura 1 hora.\n\n"
        "¡Te esperamos! "
    )

    return respuesta


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
                "correo": None,
            },
        }

    return WA_SESSIONS[wa_id]


# ============================================================
# TWILIO / WHATSAPP
# ============================================================

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.getenv(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886"
)

if (
    TWILIO_WHATSAPP_FROM
    and not TWILIO_WHATSAPP_FROM.startswith("whatsapp:")
):
    TWILIO_WHATSAPP_FROM = (
        "whatsapp:" + TWILIO_WHATSAPP_FROM
    )


def normalizar_telefono_twilio(valor):
    """
    Twilio entrega:
    whatsapp:+56912345678

    Para guardar la reserva usamos:
    +56912345678
    """
    valor = (valor or "").strip()

    if valor.startswith("whatsapp:"):
        return valor[len("whatsapp:"):]

    return valor


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

    cliente_id = (
        session.get("cliente_id")
    )

    if not cliente_id:

        cliente_id = (
            "web_"
            + hashlib.sha256(
                os.urandom(32)
            ).hexdigest()[:30]
        )

        session["cliente_id"] = cliente_id


    if "historial" not in session:

        session["historial"] = [

            {
                "role":
                    "assistant",

                "content":
                    (
                        "¡Hola!  "
                        "Soy el Asistente Virtual "
                        "de Estilista Diego \n\n"
                        "¿Cómo estás?"
                    ),
            }
        ]

        guardar_mensaje(
            cliente_id,
            "web",
            "assistant",
            session["historial"][0]["content"]
        )


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
            "correo": None,
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

            guardar_mensaje(
                cliente_id,
                "web",
                "user",
                pregunta
            )


            # =================================================
            # AGENDA ACTIVA
            # =================================================

            if session.get(
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

                respuesta = procesar_agenda(
                    estado,
                    pregunta,
                    cliente_id,
                    "web"
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

                respuesta = procesar_agenda(
                    estado,
                    pregunta,
                    cliente_id,
                    "web"
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
            # SERVICIOS
            # =================================================

            elif pregunta_servicios(
                pregunta
            ):

                respuesta = mostrar_servicios()


            # =================================================
            # OPENAI
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

            guardar_mensaje(
                cliente_id,
                "web",
                "assistant",
                respuesta
            )

            session.modified = True


    return render_template_string(
        TEMPLATE,
        historial=session["historial"]
    )


# ============================================================
# WHATSAPP / TWILIO WEBHOOK
# ============================================================

@app.route(
    "/whatsapp/webhook",
    methods=["POST"]
)
@app.route(
    "/webhook/whatsapp",
    methods=["POST"]
)
def whatsapp_webhook():

    try:

        telefono_twilio = (
            request.form
            .get(
                "From",
                ""
            )
            .strip()
        )

        text = (
            request.form
            .get(
                "Body",
                ""
            )
            .strip()
        )

        msg_id = (
            request.form
            .get(
                "MessageSid",
                ""
            )
            .strip()
        )

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono_twilio)
        print("Body:", text)
        print("MessageSid:", msg_id)
        print("=" * 60)

        twiml = MessagingResponse()

        if not telefono_twilio:

            twiml.message(
                "No pude identificar tu número de WhatsApp."
            )

            return (
                str(twiml),
                200,
                {
                    "Content-Type":
                        "application/xml; charset=utf-8"
                }
            )

        if not text:

            twiml.message(
                "Por ahora puedo ayudarte por mensaje de texto 😊."
            )

            return (
                str(twiml),
                200,
                {
                    "Content-Type":
                        "application/xml; charset=utf-8"
                }
            )


        # ====================================================
        # DEDUPLICACIÓN TWILIO
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

                # Twilio puede reintentar webhooks.
                # Devolvemos TwiML vacío para no responder dos veces.
                return (
                    str(twiml),
                    200,
                    {
                        "Content-Type":
                            "application/xml; charset=utf-8"
                    }
                )

            PROCESSED_MSG_IDS[
                msg_id
            ] = ahora_timestamp


        # ====================================================
        # SESIÓN POR NÚMERO
        # ====================================================

        cliente_id = telefono_twilio

        telefono_cliente = (
            normalizar_telefono_twilio(
                telefono_twilio
            )
        )

        estado = get_wa_session(
            cliente_id
        )

        # Twilio ya nos entrega el teléfono del cliente.
        # No necesitamos volver a pedirlo durante la reserva.
        estado[
            "datos_reserva"
        ][
            "telefono"
        ] = telefono_cliente

        estado["historial"].append({
            "role":
                "user",
            "content":
                text,
        })

        guardar_mensaje(
            cliente_id,
            "whatsapp",
            "user",
            text
        )


        # ====================================================
        # PROCESAR CON LA LÓGICA ORIGINAL
        # ====================================================

        if estado["modo_agendar"]:

            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        elif es_intencion_agendar(
            text
        ):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"

            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        elif pregunta_servicios(
            text
        ):

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

        guardar_mensaje(
            cliente_id,
            "whatsapp",
            "assistant",
            respuesta
        )

        twiml.message(
            respuesta
        )

        return (
            str(twiml),
            200,
            {
                "Content-Type":
                    "application/xml; charset=utf-8"
            }
        )

    except Exception as e:

        print(
            "TWILIO WHATSAPP ERROR:",
            repr(e)
        )

        import traceback
        print(
            traceback.format_exc()
        )

        twiml = MessagingResponse()

        twiml.message(
            "Disculpa 🙏 Estoy teniendo un problema técnico. "
            "Intenta nuevamente en unos segundos."
        )

        return (
            str(twiml),
            200,
            {
                "Content-Type":
                    "application/xml; charset=utf-8"
            }
        )


# ============================================================
# ADMIN PASSWORD
# ============================================================

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD"
)


def admin_autorizado():

    return session.get(
        "admin_auth",
        False
    )


@app.route(
    "/admin",
    methods=["GET", "POST"]
)
def admin():

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if (
            ADMIN_PASSWORD
            and password == ADMIN_PASSWORD
        ):

            session["admin_auth"] = True

            return redirect(
                url_for(
                    "admin_conversaciones"
                )
            )

        return render_template_string(
            ADMIN_LOGIN_TEMPLATE,
            error="Contraseña incorrecta."
        )

    if admin_autorizado():

        return redirect(
            url_for(
                "admin_conversaciones"
            )
        )

    return render_template_string(
        ADMIN_LOGIN_TEMPLATE,
        error=""
    )


# ============================================================
# ADMIN CONVERSACIONES
# ============================================================

@app.route(
    "/admin/conversaciones"
)
def admin_conversaciones():

    if not admin_autorizado():

        return redirect(
            url_for("admin")
        )

    if not DATABASE_URL:

        return (
            "DATABASE_URL no está configurada.",
            500
        )

    conn = None

    try:

        conn = db_connect()

        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute(
            """
            SELECT
                id,
                cliente_id,
                canal,
                nombre,
                telefono,
                correo,
                servicio,
                fecha_reserva,
                meet_url,
                estado,
                created_at,
                updated_at
            FROM conversaciones
            ORDER BY updated_at DESC
            """
        )

        conversaciones = cur.fetchall()

        cur.close()

        return render_template_string(
            ADMIN_CONVERSACIONES_TEMPLATE,
            conversaciones=conversaciones
        )

    except Exception as e:

        print(
            "ADMIN ERROR:",
            repr(e)
        )

        return (
            f"Error: {e}",
            500
        )

    finally:

        if conn:
            conn.close()


# ============================================================
# DETALLE CONVERSACIÓN
# ============================================================

@app.route(
    "/admin/conversaciones/<int:conversation_id>"
)
def admin_conversacion_detalle(
    conversation_id
):

    if not admin_autorizado():

        return redirect(
            url_for("admin")
        )

    conn = None

    try:

        conn = db_connect()

        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute(
            """
            SELECT *
            FROM conversaciones
            WHERE id = %s
            """,
            (
                conversation_id,
            )
        )

        conversacion = cur.fetchone()

        if not conversacion:

            return (
                "Conversación no encontrada.",
                404
            )

        cur.execute(
            """
            SELECT
                role,
                contenido,
                created_at
            FROM mensajes
            WHERE conversacion_id = %s
            ORDER BY created_at ASC
            """,
            (
                conversation_id,
            )
        )

        mensajes = cur.fetchall()

        cur.close()

        return render_template_string(
            ADMIN_DETALLE_TEMPLATE,
            conversacion=conversacion,
            mensajes=mensajes
        )

    except Exception as e:

        print(
            "ADMIN DETALLE ERROR:",
            repr(e)
        )

        return (
            f"Error: {e}",
            500
        )

    finally:

        if conn:
            conn.close()


# ============================================================
# LOGOUT ADMIN
# ============================================================

@app.route(
    "/admin/logout"
)
def admin_logout():

    session.pop(
        "admin_auth",
        None
    )

    return redirect(
        url_for("admin")
    )


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

@app.route(
    "/logout"
)
def logout():

    session.clear()

    return redirect(
        url_for("home")
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
 Asistente Virtual de Estilista Diego
</div>

<div class="subtitle">
Lunes a sábado · 10:00 a 18:00
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
# ADMIN LOGIN TEMPLATE
# ============================================================

ADMIN_LOGIN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Administrador</title>

<style>

body {
    font-family: Arial;
    background: #f3f4f6;
    display:flex;
    align-items:center;
    justify-content:center;
    min-height:100vh;
}

.box {
    background:white;
    padding:35px;
    border-radius:16px;
    width:350px;
    box-shadow:0 10px 30px rgba(0,0,0,.15);
}

input {
    width:100%;
    padding:12px;
    margin:10px 0;
    border:1px solid #ddd;
    border-radius:8px;
}

button {
    width:100%;
    padding:12px;
    border:0;
    border-radius:8px;
    background:#111827;
    color:white;
}

.error {
    color:#b91c1c;
}

</style>

</head>

<body>

<div class="box">

<h2> Conversaciones</h2>

<p>
Panel privado de Estilista Diego
</p>

{% if error %}
<p class="error">
{{ error }}
</p>
{% endif %}

<form method="POST">

<input
type="password"
name="password"
placeholder="Contraseña"
required
>

<button>
Entrar
</button>

</form>

</div>

</body>

</html>
"""


# ============================================================
# ADMIN CONVERSACIONES TEMPLATE
# ============================================================

ADMIN_CONVERSACIONES_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Conversaciones</title>

<style>

body {
    font-family:Arial;
    background:#f3f4f6;
    margin:0;
    padding:30px;
}

.container {
    max-width:1200px;
    margin:auto;
}

.top {
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin-bottom:25px;
}

.card {
    background:white;
    border-radius:14px;
    padding:18px;
    margin-bottom:12px;
    box-shadow:0 4px 15px rgba(0,0,0,.08);
}

a {
    color:#111827;
    text-decoration:none;
}

.badge {
    display:inline-block;
    padding:5px 9px;
    border-radius:8px;
    background:#e5e7eb;
    font-size:12px;
}

.logout {
    color:#b91c1c;
}

</style>

</head>

<body>

<div class="container">

<div class="top">

<h1>
 Conversaciones
</h1>

<a class="logout"
href="/admin/logout">
Cerrar sesión
</a>

</div>

{% if not conversaciones %}

<div class="card">
No hay conversaciones todavía.
</div>

{% endif %}

{% for c in conversaciones %}

<div class="card">

<h3>

<a href="/admin/conversaciones/{{ c['id'] }}">

{% if c['nombre'] %}
{{ c['nombre'] }}
{% else %}
Cliente {{ c['cliente_id'] }}
{% endif %}

</a>

</h3>

<p>

<span class="badge">
{{ c['canal'] }}
</span>

{% if c['estado'] %}

<span class="badge">
{{ c['estado'] }}
</span>

{% endif %}

</p>

<p>

 {{ c['telefono'] or '-' }}

<br>

 {{ c['correo'] or '-' }}

<br>

 {{ c['servicio'] or '-' }}

</p>

{% if c['fecha_reserva'] %}

<p>
 {{ c['fecha_reserva'] }}
</p>

{% endif %}

<p>
 Actualizado:
{{ c['updated_at'] }}
</p>

</div>

{% endfor %}

</div>

</body>

</html>
"""


# ============================================================
# ADMIN DETALLE TEMPLATE
# ============================================================

ADMIN_DETALLE_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<meta name="viewport"
content="width=device-width,initial-scale=1">

<title>Conversación</title>

<style>

body {
    font-family:Arial;
    background:#f3f4f6;
    margin:0;
    padding:25px;
}

.container {
    max-width:850px;
    margin:auto;
}

.card {
    background:white;
    border-radius:14px;
    padding:20px;
    margin-bottom:20px;
}

.message {
    padding:12px;
    margin:10px 0;
    border-radius:12px;
    white-space:pre-wrap;
}

.user {
    background:#e5e7eb;
    margin-left:60px;
}

.assistant {
    background:#111827;
    color:white;
    margin-right:60px;
}

.small {
    font-size:12px;
    opacity:.7;
}

a {
    color:#111827;
}

</style>

</head>

<body>

<div class="container">

<p>
<a href="/admin/conversaciones">
← Volver a conversaciones
</a>
</p>

<div class="card">

<h2>
 Conversación #{{ conversacion['id'] }}
</h2>

<p>
 <b>{{ conversacion['nombre'] or 'Sin nombre' }}</b>
</p>

<p>
 {{ conversacion['telefono'] or '-' }}
</p>

<p>
 {{ conversacion['correo'] or '-' }}
</p>

<p>
 {{ conversacion['servicio'] or '-' }}
</p>

{% if conversacion['fecha_reserva'] %}

<p>
 {{ conversacion['fecha_reserva'] }}
</p>

{% endif %}

{% if conversacion['meet_url'] %}

<p>

<a href="{{ conversacion['meet_url'] }}"
target="_blank">
Abrir Google Meet
</a>
</p>

{% endif %}

</div>

<div class="card">

<h2>
Conversación
</h2>

{% for m in mensajes %}

<div class="message
{% if m['role'] == 'user' %}
user
{% else %}
assistant
{% endif %}
">

{{ m['contenido'] }}

<div class="small">
{{ m['created_at'] }}
</div>

</div>

{% endfor %}

</div>

</div>

</body>

</html>
"""


# ============================================================
# GOOGLE TOKEN TEMPLATE
# ============================================================

TOKEN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>Google Calendar autorizado</title>

<style>

body {
    font-family:Arial;
    max-width:850px;
    margin:50px auto;
    padding:20px;
    background:#f5f5f5;
}

.box {
    background:white;
    padding:30px;
    border-radius:15px;
}

textarea {
    width:100%;
    height:120px;
    margin-top:15px;
}

.success {
    color:#087f23;
}

</style>

</head>

<body>

<div class="box">

<h1 class="success">
 Google Calendar autorizado
</h1>

<p>
La autorización fue completada correctamente.
</p>

<p>
Copia este valor en Render como:
</p>

<b>
GOOGLE_REFRESH_TOKEN
</b>

<textarea readonly>{{ token }}</textarea>

<h3>
En Render:
</h3>

<ol>

<li>Environment</li>

<li>GOOGLE_REFRESH_TOKEN</li>

<li>Pega el token</li>

<li>Guarda</li>

<li>Espera el deploy</li>

</ol>

<p>
 No compartas este token.
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
    font-family:Arial;
    max-width:800px;
    margin:50px auto;
    padding:20px;
}

.box {
    padding:30px;
    border-radius:15px;
    background:#fff3f3;
    border:1px solid #ffcccc;
}

pre {
    white-space:pre-wrap;
}

</style>

</head>

<body>

<div class="box">

<h1>
 {{ titulo }}
</h1>

<pre>{{ mensaje }}</pre>

<hr>

<a href="/admin/login">
Volver a iniciar autorización con Google
</a>

</div>

</body>

</html>
"""


# ============================================================
# INICIALIZAR BASE DE DATOS
# ============================================================

try:
    init_database()
except Exception as e:
    print(
        "ERROR INIT DATABASE:",
        repr(e)
    )


# ============================================================
# ARRANQUE
# ============================================================

print("APP_VERSION:", APP_VERSION)
print("WHATSAPP: TWILIO + LOGICA COMPLETA DE RESERVAS")

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
