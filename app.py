import os
import re
import uuid
import requests
import pytz
import openai
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
    raise Exception("Falta SECRET_KEY en Render.")

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

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-4o-mini"
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
# ADMIN
# ============================================================

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD"
)

if not ADMIN_PASSWORD:
    raise Exception(
        "Falta ADMIN_PASSWORD en Render."
    )


# ============================================================
# POSTGRESQL
# ============================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL"
)

if not DATABASE_URL:
    raise Exception(
        "Falta DATABASE_URL en Render."
    )


def obtener_db():

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


# ============================================================
# BASE DE DATOS
# ============================================================

def inicializar_base_datos():

    conn = None

    try:

        conn = obtener_db()

        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversaciones (
                id UUID PRIMARY KEY,
                canal VARCHAR(30) NOT NULL,
                identificador VARCHAR(255),
                nombre VARCHAR(255),
                telefono VARCHAR(100),
                email VARCHAR(255),
                servicio VARCHAR(255),
                fecha_reserva TIMESTAMPTZ,
                meet_url TEXT,
                estado VARCHAR(50) DEFAULT 'activa',
                creada_en TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                actualizada_en TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS mensajes (
                id BIGSERIAL PRIMARY KEY,
                conversacion_id UUID NOT NULL
                    REFERENCES conversaciones(id)
                    ON DELETE CASCADE,
                rol VARCHAR(30) NOT NULL,
                mensaje TEXT NOT NULL,
                creado_en TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_actualizada
            ON conversaciones(actualizada_en DESC);
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_conv_canal
            ON conversaciones(canal);
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_msg_conv
            ON mensajes(conversacion_id);
        """)

        conn.commit()

        cur.close()

        print("POSTGRESQL: tablas listas.")

    except Exception as e:

        print(
            "POSTGRESQL ERROR:",
            repr(e)
        )

        if conn:
            conn.rollback()

    finally:

        if conn:
            conn.close()


def crear_conversacion(
    canal,
    identificador=None
):

    try:

        conversation_id = str(
            uuid.uuid4()
        )

        conn = obtener_db()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO conversaciones
            (
                id,
                canal,
                identificador
            )
            VALUES
            (
                %s,
                %s,
                %s
            )
            """,
            (
                conversation_id,
                canal,
                identificador
            )
        )

        conn.commit()

        cur.close()
        conn.close()

        return conversation_id

    except Exception as e:

        print(
            "CREAR CONVERSACION ERROR:",
            repr(e)
        )

        return None


def guardar_mensaje(
    conversation_id,
    rol,
    mensaje
):

    if not conversation_id:
        return

    try:

        conn = obtener_db()
        cur = conn.cursor()

        cur.execute(
            """
            INSERT INTO mensajes
            (
                conversacion_id,
                rol,
                mensaje
            )
            VALUES
            (
                %s,
                %s,
                %s
            )
            """,
            (
                conversation_id,
                rol,
                mensaje
            )
        )

        cur.execute(
            """
            UPDATE conversaciones
            SET actualizada_en = CURRENT_TIMESTAMP
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
            "GUARDAR MENSAJE ERROR:",
            repr(e)
        )


def actualizar_conversacion(
    conversation_id,
    nombre=None,
    telefono=None,
    email=None,
    servicio=None,
    fecha_reserva=None,
    meet_url=None,
    estado=None
):

    if not conversation_id:
        return

    try:

        conn = obtener_db()
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE conversaciones
            SET
                nombre =
                    COALESCE(%s, nombre),

                telefono =
                    COALESCE(%s, telefono),

                email =
                    COALESCE(%s, email),

                servicio =
                    COALESCE(%s, servicio),

                fecha_reserva =
                    COALESCE(%s, fecha_reserva),

                meet_url =
                    COALESCE(%s, meet_url),

                estado =
                    COALESCE(%s, estado),

                actualizada_en =
                    CURRENT_TIMESTAMP

            WHERE id = %s
            """,
            (
                nombre,
                telefono,
                email,
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
            "ACTUALIZAR CONVERSACION ERROR:",
            repr(e)
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

                "client_id":
                    GOOGLE_CLIENT_ID,

                "client_secret":
                    GOOGLE_CLIENT_SECRET,

                "auth_uri":
                    "https://accounts.google.com/o/oauth2/auth",

                "token_uri":
                    "https://oauth2.googleapis.com/token",

                "redirect_uris":
                    [
                        GOOGLE_REDIRECT_URI
                    ],
            }
        },

        scopes=SCOPES,

        redirect_uri=
            GOOGLE_REDIRECT_URI
    )


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

    return build(

        "calendar",

        "v3",

        credentials=
            obtener_credentials_diego(),

        cache_discovery=False
    )


# ============================================================
# FECHA / HORA
# ============================================================

def obtener_zona():

    return pytz.timezone(
        TIMEZONE
    )


def ahora_local():

    return datetime.now(
        obtener_zona()
    )


DIAS_NOMBRES = [
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
]


def es_dia_atencion(
    fecha
):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        fecha.weekday()
        in DIAS_ATENCION
    )


def formato_fecha_corta(
    fecha
):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day}/{fecha.month} "
        f"{fecha.strftime('%H:%M')}"
    )


def formato_fecha_larga(
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
# SERVICIOS
# ============================================================

def obtener_servicio(
    codigo
):

    return SERVICIOS[codigo]


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


def detectar_servicio_por_numero(
    texto
):

    match = re.fullmatch(
        r"\s*([1-5])\s*",
        normalizar_texto(texto)
    )

    if not match:

        return None

    numero = int(
        match.group(1)
    )

    return SERVICIO_POR_NUMERO.get(
        numero
    )


def detectar_servicio(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

    servicio = (
        detectar_servicio_por_numero(
            texto
        )
    )

    if servicio:

        return servicio

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
# INTENCIONES
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

        "precios",
        "precio",

        "valor",
        "valores",

        "cuanto sale",
        "cuanto cuesta",

        "que haces",
        "que ofrecen",
        "que tienes",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


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


def usuario_no_quiere(
    texto
):

    texto_n = normalizar_texto(
        texto
    )

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


def email_valido(
    email
):

    return bool(
        re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email.strip()
        )
    )


# ============================================================
# DISPONIBILIDAD
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

        if not es_dia_atencion(
            inicio
        ):

            return False

        if inicio.minute != 0:

            return False

        if (
            inicio.hour < HORA_APERTURA
            or inicio.hour >= HORA_CIERRE
        ):

            return False

        fin = (
            inicio
            + timedelta(minutes=duracion)
        )

        limite = inicio.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        )

        if fin > limite:

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

        busy = calendario.get(
            "busy",
            []
        )

        return not bool(
            busy
        )

    except Exception as e:

        print(
            "CALENDAR AVAILABILITY ERROR:",
            repr(e)
        )

        return None


def buscar_proximas_10_horas():

    ahora = ahora_local()

    resultados = []

    for offset in range(31):

        fecha = (
            ahora
            + timedelta(
                days=offset
            )
        ).replace(
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
                    inicio
                )
            )

            print(
                "BUSCANDO:",
                inicio,
                "LIBRE:",
                disponible
            )

            if disponible is True:

                resultados.append(
                    inicio
                )

                if len(resultados) >= 10:

                    return resultados

    return resultados


def formatear_opciones_horas(
    horas
):

    return "\n".join(

        f"{i}. {formato_fecha_corta(hora)}"

        for i, hora
        in enumerate(
            horas,
            start=1
        )
    )


def mostrar_proximas_horas():

    horas = (
        buscar_proximas_10_horas()
    )

    if not horas:

        return (
            "No encontré horas disponibles "
            "en los próximos días 😕."
        )

    return (
        "Estas son las próximas 10 horas "
        "disponibles:\n\n"

        f"{formatear_opciones_horas(horas)}"

        "\n\n"

        "Respóndeme con el número de la hora "
        "que prefieras, del 1 al 10."
    )


# ============================================================
# GOOGLE EVENT + MEET + INVITACIÓN
# ============================================================

def crear_evento_diego(
    inicio,
    servicio_codigo,
    nombre_cliente,
    telefono_cliente,
    email_cliente
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

        fin = (
            inicio
            + timedelta(
                minutes=DURACION_RESERVA
            )
        )

        request_id = (
            uuid.uuid4().hex
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
                    f"Estilista "
                    f"{ESTILISTA_NOMBRE}.\n\n"

                    f"Cliente: "
                    f"{nombre_cliente}\n"

                    f"Teléfono: "
                    f"{telefono_cliente}\n"

                    f"Correo: "
                    f"{email_cliente}\n"

                    f"Servicio: "
                    f"{servicio['nombre']}\n"

                    f"Valor: "
                    f"${servicio['precio']}\n"

                    f"Duración: "
                    f"{DURACION_RESERVA} minutos"
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

            "attendees": [

                {

                    "email":
                        email_cliente,

                    "displayName":
                        nombre_cliente,
                }
            ],

            "conferenceData": {

                "createRequest": {

                    "requestId":
                        request_id,

                    "conferenceSolutionKey": {

                        "type":
                            "hangoutsMeet"
                    }
                }
            },

            "extendedProperties": {

                "private": {

                    "cliente":
                        nombre_cliente,

                    "telefono":
                        telefono_cliente,

                    "email":
                        email_cliente,

                    "servicio":
                        servicio["nombre"],

                    "origen":
                        "Asistente Virtual",
                }
            }
        }

        resultado = (
            service
            .events()
            .insert(

                calendarId=
                    CALENDAR_ID,

                body=
                    evento,

                conferenceDataVersion=
                    1,

                sendUpdates=
                    "all"
            )
            .execute()
        )

        meet_url = None

        conference_data = (
            resultado
            .get(
                "conferenceData",
                {}
            )
        )

        entry_points = (
            conference_data
            .get(
                "entryPoints",
                []
            )
        )

        for entry in entry_points:

            if (
                entry.get(
                    "entryPointType"
                )
                == "video"
            ):

                meet_url = (
                    entry.get(
                        "uri"
                    )
                )

                break

        if not meet_url:

            meet_url = (
                resultado
                .get(
                    "hangoutLink"
                )
            )

        print(
            "EVENTO GOOGLE:",
            resultado.get(
                "id"
            )
        )

        print(
            "GOOGLE MEET:",
            meet_url
        )

        return {

            "ok":
                True,

            "evento_id":
                resultado.get(
                    "id"
                ),

            "meet":
                meet_url,
        }

    except Exception as e:

        print(
            "GOOGLE EVENT ERROR:",
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
Eres el Asistente Virtual de Estilista {ESTILISTA_NOMBRE}.

Hablas español natural de Chile.

Tu conversación debe parecer una conversación
fluida y humana.

No repitas frases automáticamente.

Ejemplo:

Cliente:
Hola

Asistente:
¡Hola! 👋 Qué gusto saludarte. ¿Cómo estás?

Cliente:
Bien y tú?

Asistente:
¡Muy bien también, gracias! 😄
Si quieres, puedo contarte los servicios de Diego
o podemos buscarte una hora.

Si el cliente dice:
"qué tal?"

responde al contenido, no vuelvas a saludar.

Si dice:
"sí"

y el contexto es ambiguo:

"¡Perfecto! 😊 ¿Quieres conocer los servicios
o prefieres que busquemos una hora?"

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

La disponibilidad real la comprueba el sistema.

Nunca inventes horarios.

No hables de código, APIs ni sistemas internos.

Tu objetivo es conducir naturalmente al cliente
hacia los servicios o la reserva.
"""

        mensajes = [

            {
                "role":
                    "system",

                "content":
                    system_prompt
            }
        ]

        for mensaje in historial[-12:]:

            if (
                mensaje.get("role")
                in [
                    "user",
                    "assistant"
                ]
            ):

                mensajes.append(
                    {
                        "role":
                            mensaje["role"],

                        "content":
                            mensaje["content"]
                    }
                )

        if not (
            mensajes
            and mensajes[-1].get("role")
            == "user"
            and mensajes[-1].get("content")
            == pregunta
        ):

            mensajes.append({

                "role":
                    "user",

                "content":
                    pregunta
            })

        completion = (
            client
            .chat
            .completions
            .create(

                model=
                    OPENAI_MODEL,

                messages=
                    mensajes,

                max_tokens=
                    250,

                temperature=
                    0.8
            )
        )

        respuesta = (
            completion
            .choices[0]
            .message
            .content
        )

        if respuesta:

            return respuesta.strip()

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e)
        )

    texto = normalizar_texto(
        pregunta
    )

    if texto in [
        "hola",
        "holaa",
        "holi",
        "buenas"
    ]:

        return (
            "¡Hola! 👋 Qué gusto saludarte. "
            "¿Cómo estás?"
        )

    if any(
        frase in texto
        for frase in [
            "bien y tu",
            "bien, y tu",
            "super bien y tu",
            "muy bien y tu",
            "todo bien y tu",
        ]
    ):

        return (
            "¡Muy bien también, gracias! 😄 "
            "¿Quieres conocer los servicios "
            "o prefieres reservar una hora?"
        )

    if texto in [
        "si",
        "sí",
        "dale",
        "ok",
        "bueno"
    ]:

        return (
            "¡Perfecto! 😊 "
            "¿Quieres conocer los servicios "
            "o prefieres que busquemos una hora?"
        )

    return (
        "Claro 😊 "
        "Puedo mostrarte los servicios de Diego "
        "o ayudarte a reservar una hora."
    )


# ============================================================
# ESTADO DE RESERVA
# ============================================================

def resetear_reserva(
    estado
):

    telefono = (
        estado
        .get(
            "datos_reserva",
            {}
        )
        .get(
            "telefono"
        )
    )

    estado["modo_agendar"] = False

    estado["paso"] = "inicio"

    estado["horas_ofrecidas"] = []

    estado["datos_reserva"] = {

        "servicio":
            None,

        "fecha_hora":
            None,

        "nombre":
            None,

        "telefono":
            telefono,

        "email":
            None,
    }


# ============================================================
# PROCESAR AGENDA
# ============================================================

def procesar_agenda(
    estado,
    texto,
    conversation_id
):

    texto = (
        texto or ""
    ).strip()

    datos = (
        estado["datos_reserva"]
    )


    if usuario_no_quiere(
        texto
    ):

        resetear_reserva(
            estado
        )

        actualizar_conversacion(
            conversation_id,
            estado="cancelada"
        )

        return (
            "No hay problema 😊 "
            "Cuando quieras volver, aquí estaré. "
            "¡Que estés muy bien! 👋"
        )


    # ========================================================
    # SERVICIO
    # ========================================================

    if not datos["servicio"]:

        servicio = detectar_servicio(
            texto
        )

        if not servicio:

            return mostrar_servicios()

        datos["servicio"] = (
            servicio
        )

        servicio_info = (
            obtener_servicio(
                servicio
            )
        )

        horas = (
            buscar_proximas_10_horas()
        )

        if not horas:

            return (
                f"Perfecto 😊\n\n"
                f"✂️ "
                f"{servicio_info['nombre']}\n"
                f"💰 $20.000\n\n"
                "Por ahora no encontré horas "
                "disponibles."
            )

        estado[
            "horas_ofrecidas"
        ] = [
            h.isoformat()
            for h in horas
        ]

        estado[
            "paso"
        ] = "seleccionar_hora"

        return (
            f"Perfecto 😊\n\n"

            f"✂️ "
            f"{servicio_info['nombre']}\n"

            "💰 $20.000\n\n"

            "Estas son las próximas "
            "10 horas disponibles:\n\n"

            f"{formatear_opciones_horas(horas)}"

            "\n\n"

            "Respóndeme con el número "
            "de la hora que prefieras, "
            "del 1 al 10."
        )


    # ========================================================
    # HORA
    # ========================================================

    if (
        estado["paso"]
        == "seleccionar_hora"
    ):

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
            estado
            .get(
                "horas_ofrecidas",
                []
            )
        )

        if (
            numero < 1
            or numero >
            len(horas_guardadas)
        ):

            return (
                f"Elige un número entre 1 y "
                f"{len(horas_guardadas)}, "
                "por favor 😊."
            )

        fecha_hora = datetime.fromisoformat(
            horas_guardadas[
                numero - 1
            ]
        )

        disponible = (
            verificar_disponibilidad(
                fecha_hora
            )
        )

        if disponible is None:

            return (
                "No pude comprobar la agenda "
                "en este momento 😕."
            )

        if not disponible:

            horas = (
                buscar_proximas_10_horas()
            )

            estado[
                "horas_ofrecidas"
            ] = [
                h.isoformat()
                for h in horas
            ]

            return (
                "Esa hora acaba de ocuparse 😕.\n\n"
                "Actualicé las horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas)}\n\n"
                "¿Cuál prefieres?"
            )

        datos[
            "fecha_hora"
        ] = fecha_hora.isoformat()

        estado[
            "paso"
        ] = "nombre"

        return (
            "¡Perfecto! 🙌\n\n"

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
                "¿Me indicas tu nombre, "
                "por favor? 😊"
            )

        datos[
            "nombre"
        ] = texto

        actualizar_conversacion(
            conversation_id,
            nombre=texto
        )

        estado[
            "paso"
        ] = "telefono"

        return (
            f"Perfecto, {texto} 👍\n\n"
            "¿Cuál es tu número de teléfono? 📞"
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if estado["paso"] == "telefono":

        numeros = re.sub(
            r"\D",
            "",
            texto
        )

        if len(numeros) < 8:

            return (
                "¿Me indicas un número de teléfono "
                "válido, por favor? 📞"
            )

        datos[
            "telefono"
        ] = texto

        actualizar_conversacion(
            conversation_id,
            telefono=texto
        )

        estado[
            "paso"
        ] = "email"

        return (
            "Perfecto 👍\n\n"
            "¿Cuál es tu correo electrónico? 📧\n\n"
            "Lo necesito para enviarte la invitación "
            "de Google Calendar."
        )


    # ========================================================
    # EMAIL
    # ========================================================

    if estado["paso"] == "email":

        email = texto.lower().strip()

        if not email_valido(
            email
        ):

            return (
                "Ese correo no parece válido 😕.\n\n"
                "Escríbelo nuevamente, por ejemplo:\n"
                "nombre@gmail.com"
            )

        datos[
            "email"
        ] = email

        actualizar_conversacion(
            conversation_id,
            email=email
        )

        estado[
            "paso"
        ] = "confirmar"

        return completar_reserva(
            estado,
            conversation_id
        )


    return completar_reserva(
        estado,
        conversation_id
    )


# ============================================================
# COMPLETAR RESERVA
# ============================================================

def completar_reserva(
    estado,
    conversation_id
):

    datos = (
        estado["datos_reserva"]
    )

    if not datos["servicio"]:

        estado[
            "paso"
        ] = "servicio"

        return mostrar_servicios()

    if not datos["fecha_hora"]:

        estado[
            "paso"
        ] = "seleccionar_hora"

        return mostrar_proximas_horas()

    if not datos["nombre"]:

        estado[
            "paso"
        ] = "nombre"

        return (
            "¿Me indicas tu nombre? 😊"
        )

    if not datos["telefono"]:

        estado[
            "paso"
        ] = "telefono"

        return (
            "¿Cuál es tu número de teléfono? 📞"
        )

    if not datos["email"]:

        estado[
            "paso"
        ] = "email"

        return (
            "¿Cuál es tu correo electrónico? 📧"
        )


    inicio = datetime.fromisoformat(
        datos["fecha_hora"]
    )


    # ========================================================
    # ÚLTIMA COMPROBACIÓN
    # ========================================================

    disponible = (
        verificar_disponibilidad(
            inicio
        )
    )

    if disponible is None:

        return (
            "No pude comprobar nuevamente "
            "la disponibilidad 😕."
        )


    if not disponible:

        datos[
            "fecha_hora"
        ] = None

        estado[
            "paso"
        ] = "seleccionar_hora"

        horas = (
            buscar_proximas_10_horas()
        )

        estado[
            "horas_ofrecidas"
        ] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Justo esa hora acaba de ocuparse 😕.\n\n"

            "Estas son las nuevas horas disponibles:\n\n"

            f"{formatear_opciones_horas(horas)}\n\n"

            "¿Cuál prefieres?"
        )


    # ========================================================
    # CREAR EVENTO
    # ========================================================

    resultado = crear_evento_diego(

        inicio=
            inicio,

        servicio_codigo=
            datos["servicio"],

        nombre_cliente=
            datos["nombre"],

        telefono_cliente=
            datos["telefono"],

        email_cliente=
            datos["email"]
    )


    if not resultado["ok"]:

        print(
            "ERROR RESERVA:",
            resultado.get(
                "error"
            )
        )

        return (
            "No pude completar la reserva "
            "en este momento 😕.\n\n"
            "Intenta nuevamente."
        )


    servicio = obtener_servicio(
        datos["servicio"]
    )

    meet = resultado.get(
        "meet"
    )

    nombre = datos[
        "nombre"
    ]

    telefono = datos[
        "telefono"
    ]

    email = datos[
        "email"
    ]

    fecha_texto = (
        formato_fecha_larga(
            inicio
        )
    )


    # ========================================================
    # ACTUALIZAR DB
    # ========================================================

    actualizar_conversacion(

        conversation_id,

        nombre=
            nombre,

        telefono=
            telefono,

        email=
            email,

        servicio=
            servicio["nombre"],

        fecha_reserva=
            inicio,

        meet_url=
            meet,

        estado=
            "reserva_confirmada"
    )


    # ========================================================
    # RESET
    # ========================================================

    telefono_guardar = telefono

    resetear_reserva(
        estado
    )

    estado[
        "datos_reserva"
    ][
        "telefono"
    ] = telefono_guardar


    # ========================================================
    # RESPUESTA
    # ========================================================

    respuesta = (
        "✅ ¡Reserva confirmada!\n\n"

        f"✂️ Servicio: "
        f"{servicio['nombre']}\n"

        "💰 Valor: $20.000\n"

        f"👤 Cliente: "
        f"{nombre}\n"

        f"📞 Teléfono: "
        f"{telefono}\n"

        f"📧 Correo: "
        f"{email}\n"

        f"📅 "
        f"{fecha_texto}\n\n"

        f"Tu hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n"
    )


    if meet:

        respuesta += (
            "\n🎥 Videollamada Google Meet:\n"
            f"{meet}\n"
        )


    respuesta += (
        "\n📅 La invitación de Google Calendar "
        "fue enviada a tu correo.\n\n"

        "La atención dura 1 hora.\n\n"

        "¡Te esperamos! 🙌"
    )


    return respuesta


# ============================================================
# WHATSAPP
# ============================================================

WA_SESSIONS = {}

PROCESSED_MSG_IDS = {}

DEDUP_TTL_SECONDS = 120


def get_wa_session(
    wa_id
):

    if wa_id not in WA_SESSIONS:

        conversation_id = crear_conversacion(
            "whatsapp",
            wa_id
        )

        WA_SESSIONS[
            wa_id
        ] = {

            "conversation_id":
                conversation_id,

            "historial":
                [],

            "modo_agendar":
                False,

            "paso":
                "inicio",

            "horas_ofrecidas":
                [],

            "datos_reserva":
                {

                    "servicio":
                        None,

                    "fecha_hora":
                        None,

                    "nombre":
                        None,

                    "telefono":
                        wa_id,

                    "email":
                        None,
                }
        }

    return WA_SESSIONS[
        wa_id
    ]


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
            "application/json"
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
        }
    }

    try:

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=20
        )

        print(
            "WhatsApp:",
            response.status_code
        )

        return response

    except Exception as e:

        print(
            "WHATSAPP ERROR:",
            repr(e)
        )

        return None


# ============================================================
# CHAT NUEVA
# ============================================================

def iniciar_nueva_conversacion_web():

    conversation_id = (
        crear_conversacion(
            "web",
            str(uuid.uuid4())
        )
    )

    session[
        "conversation_id"
    ] = conversation_id

    session[
        "historial"
    ] = []

    session[
        "modo_agendar"
    ] = False

    session[
        "paso"
    ] = "inicio"

    session[
        "horas_ofrecidas"
    ] = []

    session[
        "datos_reserva"
    ] = {

        "servicio":
            None,

        "fecha_hora":
            None,

        "nombre":
            None,

        "telefono":
            None,

        "email":
            None,
    }

    saludo = (
        "¡Hola! 👋 "
        "Soy el Asistente Virtual "
        "de Estilista Diego ✂️\n\n"
        "¿Cómo estás?"
    )

    session[
        "historial"
    ].append({

        "role":
            "assistant",

        "content":
            saludo
    })

    guardar_mensaje(
        conversation_id,
        "assistant",
        saludo
    )


@app.route(
    "/chat/nueva"
)
def chat_nueva():

    session.permanent = True

    iniciar_nueva_conversacion_web()

    return redirect(
        url_for(
            "chat"
        )
    )


# ============================================================
# CHAT
# ============================================================

@app.route(
    "/chat",
    methods=[
        "GET",
        "POST"
    ]
)
def chat():

    session.permanent = True

    if not session.get(
        "conversation_id"
    ):

        iniciar_nueva_conversacion_web()


    conversation_id = session[
        "conversation_id"
    ]


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
                    pregunta
            })

            guardar_mensaje(
                conversation_id,
                "user",
                pregunta
            )


            # =================================================
            # AGENDA
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
                        session[
                            "datos_reserva"
                        ],
                }

                respuesta = procesar_agenda(
                    estado,
                    pregunta,
                    conversation_id
                )

                session[
                    "modo_agendar"
                ] = estado[
                    "modo_agendar"
                ]

                session[
                    "paso"
                ] = estado[
                    "paso"
                ]

                session[
                    "horas_ofrecidas"
                ] = estado[
                    "horas_ofrecidas"
                ]

                session[
                    "datos_reserva"
                ] = estado[
                    "datos_reserva"
                ]


            # =================================================
            # INICIAR AGENDA
            # =================================================

            elif es_intencion_agendar(
                pregunta
            ):

                session[
                    "modo_agendar"
                ] = True

                session[
                    "paso"
                ] = "inicio"

                estado = {

                    "modo_agendar":
                        True,

                    "paso":
                        "inicio",

                    "horas_ofrecidas":
                        [],

                    "datos_reserva":
                        session[
                            "datos_reserva"
                        ],
                }

                respuesta = procesar_agenda(
                    estado,
                    pregunta,
                    conversation_id
                )

                session[
                    "paso"
                ] = estado[
                    "paso"
                ]

                session[
                    "horas_ofrecidas"
                ] = estado[
                    "horas_ofrecidas"
                ]

                session[
                    "datos_reserva"
                ] = estado[
                    "datos_reserva"
                ]


            # =================================================
            # SERVICIOS
            # =================================================

            elif pregunta_servicios(
                pregunta
            ):

                respuesta = (
                    mostrar_servicios()
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


            session[
                "historial"
            ].append({

                "role":
                    "assistant",

                "content":
                    respuesta
            })

            guardar_mensaje(
                conversation_id,
                "assistant",
                respuesta
            )

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
    methods=[
        "GET"
    ]
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
    methods=[
        "POST"
    ]
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

            if msg_id in PROCESSED_MSG_IDS:

                return "ok", 200

            PROCESSED_MSG_IDS[
                msg_id
            ] = ahora_timestamp


        estado = get_wa_session(
            wa_id
        )

        conversation_id = (
            estado[
                "conversation_id"
            ]
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
                text
        })

        guardar_mensaje(
            conversation_id,
            "user",
            text
        )


        if estado[
            "modo_agendar"
        ]:

            respuesta = procesar_agenda(
                estado,
                text,
                conversation_id
            )

        elif es_intencion_agendar(
            text
        ):

            estado[
                "modo_agendar"
            ] = True

            estado[
                "paso"
            ] = "inicio"

            respuesta = procesar_agenda(
                estado,
                text,
                conversation_id
            )

        elif pregunta_servicios(
            text
        ):

            respuesta = (
                mostrar_servicios()
            )

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
                respuesta
        })


        guardar_mensaje(
            conversation_id,
            "assistant",
            respuesta
        )

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
# ADMIN
# ============================================================

def admin_autorizado():

    return session.get(
        "admin_auth",
        False
    )


@app.route(
    "/admin",
    methods=[
        "GET",
        "POST"
    ]
)
def admin():

    if admin_autorizado():

        return redirect(
            url_for(
                "admin_conversaciones"
            )
        )

    error = None

    if request.method == "POST":

        password = request.form.get(
            "password",
            ""
        )

        if password == ADMIN_PASSWORD:

            session[
                "admin_auth"
            ] = True

            session.permanent = True

            return redirect(
                url_for(
                    "admin_conversaciones"
                )
            )

        error = (
            "Contraseña incorrecta."
        )

    return render_template_string(
        ADMIN_LOGIN_TEMPLATE,
        error=error
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
            url_for(
                "admin"
            )
        )

    conn = None

    conversaciones = []

    try:

        conn = obtener_db()

        cur = conn.cursor(
            cursor_factory=
                psycopg2.extras.DictCursor
        )

        cur.execute(
            """
            SELECT
                c.id,
                c.canal,
                c.identificador,
                c.nombre,
                c.telefono,
                c.email,
                c.servicio,
                c.fecha_reserva,
                c.meet_url,
                c.estado,
                c.creada_en,
                c.actualizada_en,
                COUNT(m.id)
                    AS cantidad_mensajes

            FROM conversaciones c

            LEFT JOIN mensajes m
                ON m.conversacion_id = c.id

            GROUP BY c.id

            ORDER BY
                c.actualizada_en DESC

            LIMIT 500
            """
        )

        conversaciones = (
            cur.fetchall()
        )

        cur.close()

    except Exception as e:

        print(
            "ADMIN ERROR:",
            repr(e)
        )

    finally:

        if conn:

            conn.close()


    return render_template_string(
        ADMIN_CONVERSACIONES_TEMPLATE,
        conversaciones=
            conversaciones
    )


# ============================================================
# ADMIN DETALLE
# ============================================================

@app.route(
    "/admin/conversacion/<conversation_id>"
)
def admin_conversacion(
    conversation_id
):

    if not admin_autorizado():

        return redirect(
            url_for(
                "admin"
            )
        )

    conn = None

    conversacion = None
    mensajes = []

    try:

        conn = obtener_db()

        cur = conn.cursor(
            cursor_factory=
                psycopg2.extras.DictCursor
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

        conversacion = (
            cur.fetchone()
        )

        if not conversacion:

            return (
                "Conversación no encontrada.",
                404
            )

        cur.execute(
            """
            SELECT
                rol,
                mensaje,
                creado_en

            FROM mensajes

            WHERE conversacion_id = %s

            ORDER BY creado_en ASC
            """,
            (
                conversation_id,
            )
        )

        mensajes = (
            cur.fetchall()
        )

        cur.close()

    except Exception as e:

        print(
            "ADMIN DETAIL ERROR:",
            repr(e)
        )

    finally:

        if conn:

            conn.close()


    return render_template_string(

        ADMIN_DETALLE_TEMPLATE,

        conversacion=
            conversacion,

        mensajes=
            mensajes
    )


# ============================================================
# ADMIN LOGOUT
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
        url_for(
            "admin"
        )
    )


# ============================================================
# GOOGLE LOGIN
# ============================================================

@app.route(
    "/admin/login"
)
def admin_login():

    try:

        flow = (
            crear_google_flow()
        )

        authorization_url, state = (
            flow.authorization_url(

                access_type=
                    "offline",

                include_granted_scopes=
                    "true",

                prompt=
                    "consent"
            )
        )

        session.permanent = True

        session[
            "google_oauth_state"
        ] = state

        session[
            "google_code_verifier"
        ] = (
            flow.code_verifier
        )

        session.modified = True

        return redirect(
            authorization_url
        )

    except Exception as e:

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
                "Se perdió el code_verifier OAuth."
            )


        flow = (
            crear_google_flow()
        )

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

        refresh_token = (
            credentials.refresh_token
        )


        if not refresh_token:

            raise Exception(
                "Google no entregó refresh token."
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
        url_for(
            "home"
        )
    )


# ============================================================
# ADMIN LOGIN TEMPLATE
# ============================================================

ADMIN_LOGIN_TEMPLATE = """

<!DOCTYPE html>

<html lang="es">

<head>

<meta charset="UTF-8">

<title>
Panel de conversaciones
</title>

<style>

body {

    font-family:
        Arial;

    background:
        #f3f4f6;

    display:
        flex;

    justify-content:
        center;

    align-items:
        center;

    height:
        100vh;
}

.box {

    background:
        white;

    padding:
        35px;

    border-radius:
        16px;

    width:
        360px;

    box-shadow:
        0 10px 35px
        rgba(0,0,0,.15);
}

input {

    width:
        100%;

    padding:
        12px;

    margin:
        10px 0;

    box-sizing:
        border-box;
}

button {

    width:
        100%;

    padding:
        12px;

    border:
        none;

    border-radius:
        8px;

    background:
        #111827;

    color:
        white;

    cursor:
        pointer;
}

.error {

    color:
        #b91c1c;
}

</style>

</head>

<body>

<div class="box">

<h2>
💬 Panel de conversaciones
</h2>

<p>
Ingresa tu contraseña.
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

<title>
Conversaciones
</title>

<style>

body {

    margin:
        0;

    font-family:
        Arial;

    background:
        #f3f4f6;
}

header {

    background:
        #111827;

    color:
        white;

    padding:
        20px;
}

.container {

    max-width:
        1100px;

    margin:
        25px auto;

    padding:
        0 15px;
}

.card {

    background:
        white;

    padding:
        18px;

    border-radius:
        14px;

    margin-bottom:
        12px;

    box-shadow:
        0 3px 14px
        rgba(0,0,0,.08);
}

.card:hover {

    box-shadow:
        0 5px 18px
        rgba(0,0,0,.12);
}

a {

    color:
        inherit;

    text-decoration:
        none;
}

.nombre {

    font-size:
        18px;

    font-weight:
        bold;
}

.meta {

    color:
        #666;

    margin-top:
        8px;

    line-height:
        1.6;
}

.badge {

    display:
        inline-block;

    padding:
        4px 8px;

    border-radius:
        8px;

    background:
        #e5e7eb;

    font-size:
        12px;

    margin-right:
        4px;
}

.top {

    display:
        flex;

    justify-content:
        space-between;

    align-items:
        center;
}

.logout {

    color:
        white;

    text-decoration:
        underline;
}

</style>

</head>

<body>

<header>

<div class="top">

<div>
💬 Conversaciones
</div>

<div>

<a
class="logout"
href="/admin/logout"
>

Cerrar sesión

</a>

</div>

</div>

</header>

<div class="container">

{% if not conversaciones %}

<div class="card">

No hay conversaciones guardadas.

</div>

{% endif %}


{% for c in conversaciones %}

<a
href="/admin/conversacion/{{ c['id'] }}"
>

<div class="card">

<div class="nombre">

{% if c['nombre'] %}

👤 {{ c['nombre'] }}

{% else %}

👤 Conversación web

{% endif %}

</div>

<div>

<span class="badge">

{{ c['canal'] }}

</span>

<span class="badge">

{{ c['cantidad_mensajes'] }}
mensajes

</span>

</div>

<div class="meta">

📱
{{ c['telefono'] or 'Sin teléfono' }}

<br>

📧
{{ c['email'] or 'Sin correo' }}

<br>

✂️
{{ c['servicio'] or 'Sin servicio' }}

<br>

{% if c['fecha_reserva'] %}

📅
{{ c['fecha_reserva'] }}

<br>

{% endif %}

🕐
{{ c['actualizada_en'] }}

</div>

</div>

</a>

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

<title>
Conversación
</title>

<style>

body {

    margin:
        0;

    font-family:
        Arial;

    background:
        #f3f4f6;
}

header {

    background:
        #111827;

    color:
        white;

    padding:
        18px;
}

header a {

    color:
        white;

    text-decoration:
        none;
}

.container {

    max-width:
        850px;

    margin:
        25px auto;

    padding:
        0 15px;
}

.info {

    background:
        white;

    padding:
        20px;

    border-radius:
        14px;

    margin-bottom:
        20px;
}

.message {

    padding:
        13px 16px;

    border-radius:
        15px;

    margin:
        12px 0;

    white-space:
        pre-wrap;

    max-width:
        80%;

    line-height:
        1.45;
}

.user {

    background:
        #e5e7eb;

    margin-left:
        auto;

    color:
        #111827;
}

.assistant {

    background:
        #111827;

    color:
        white;

    margin-right:
        auto;
}

.time {

    font-size:
        10px;

    opacity:
        .65;

    margin-top:
        8px;
}

</style>

</head>

<body>

<header>

<a
href="/admin/conversaciones"
>

← Volver a conversaciones

</a>

</header>

<div class="container">

<div class="info">

<h2>

{% if conversacion['nombre'] %}

👤 {{ conversacion['nombre'] }}

{% else %}

👤 Conversación

{% endif %}

</h2>

<p>

📱
{{ conversacion['telefono'] or 'Sin teléfono' }}

</p>

<p>

📧
{{ conversacion['email'] or 'Sin correo' }}

</p>

<p>

✂️
{{ conversacion['servicio'] or 'Sin servicio' }}

</p>

{% if conversacion['fecha_reserva'] %}

<p>

📅
{{ conversacion['fecha_reserva'] }}

</p>

{% endif %}

{% if conversacion['meet_url'] %}

<p>

🎥

<a
href="{{ conversacion['meet_url'] }}"
target="_blank"
>

Abrir Google Meet

</a>

</p>

{% endif %}

</div>


<h2>
💬 Conversación
</h2>


{% for m in mensajes %}

<div class="message

{% if m['rol'] == 'user' %}

user

{% else %}

assistant

{% endif %}

">

<b>

{% if m['rol'] == 'user' %}

Cliente

{% else %}

Asistente

{% endif %}

</b>

<br><br>

{{ m['mensaje'] }}

<div class="time">

{{ m['creado_en'] }}

</div>

</div>

{% endfor %}

</div>

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

<title>
Google Calendar autorizado
</title>

<style>

body {

    font-family:
        Arial;

    max-width:
        850px;

    margin:
        50px auto;

    padding:
        20px;
}

textarea {

    width:
        100%;

    height:
        120px;
}

</style>

</head>

<body>

<h1>
✅ Google Calendar autorizado
</h1>

<p>
Copia este token en Render:
</p>

<b>
GOOGLE_REFRESH_TOKEN
</b>

<textarea readonly>
{{ token }}
</textarea>

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
Error
</title>

</head>

<body>

<h1>
❌ {{ titulo }}
</h1>

<pre>
{{ mensaje }}
</pre>

<hr>

<a
href="/admin/login"
>
Volver
</a>

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
content="width=device-width,initial-scale=1">

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
        16px 18px;

    background:
        #111827;

    color:
        white;
}

.header-row {

    display:
        flex;

    justify-content:
        space-between;

    align-items:
        center;
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

.new-chat {

    color:
        white;

    font-size:
        11px;

    text-decoration:
        none;

    border:
        1px solid
        rgba(255,255,255,.35);

    padding:
        6px 8px;

    border-radius:
        7px;
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

@media(max-width:500px) {

    #chat-container {

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

<div class="header-row">

<div>

<div class="name">

✂️ Asistente Virtual de Estilista Diego

</div>

<div class="subtitle">

Lunes a sábado · 10:00 a 18:00

</div>

</div>

<a
class="new-chat"
href="/chat/nueva"
>

Nueva conversación

</a>

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

<button
type="submit"
>
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

inicializar_base_datos()


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
