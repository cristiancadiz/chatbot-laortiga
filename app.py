import os
import re
import uuid
import traceback
from datetime import datetime, timedelta, time

import pytz

from flask import (
    Flask,
    request,
    jsonify,
    render_template_string,
    redirect,
    url_for,
    session,
)

from twilio.twiml.messaging_response import MessagingResponse

from openai import OpenAI

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import dateparser


# ============================================================
# CONFIGURACIÓN
# ============================================================

app = Flask(__name__)

app.secret_key = os.environ.get(
    "FLASK_SECRET_KEY",
    "cambiar-esta-clave-en-render"
)

TZ = pytz.timezone(
    os.environ.get(
        "TIMEZONE",
        "America/Santiago"
    )
)

OPENAI_API_KEY = os.environ.get(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-5-mini"
)



# ============================================================
# TWILIO
# ============================================================

TWILIO_ACCOUNT_SID = os.environ.get(
    "TWILIO_ACCOUNT_SID"
)

TWILIO_AUTH_TOKEN = os.environ.get(
    "TWILIO_AUTH_TOKEN"
)

TWILIO_WHATSAPP_FROM = os.environ.get(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886"
)

# Normalizamos el número
if TWILIO_WHATSAPP_FROM:
    if not TWILIO_WHATSAPP_FROM.startswith(
        "whatsapp:"
    ):
        TWILIO_WHATSAPP_FROM = (
            "whatsapp:"
            + TWILIO_WHATSAPP_FROM
        )


# ============================================================
# GOOGLE CALENDAR
# ============================================================

GOOGLE_REFRESH_TOKEN = os.environ.get(
    "GOOGLE_REFRESH_TOKEN"
)

GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID"
)

GOOGLE_CLIENT_SECRET = os.environ.get(
    "GOOGLE_CLIENT_SECRET"
)

GOOGLE_CALENDAR_ID = os.environ.get(
    "GOOGLE_CALENDAR_ID",
    "primary"
)


# ============================================================
# ADMIN
# ============================================================

ADMIN_USER = os.environ.get(
    "ADMIN_USER",
    "admin"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "cambiar-password"
)


# ============================================================
# OPENAI
# ============================================================

client = None

if OPENAI_API_KEY:

    try:

        client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        print(
            "OPENAI: cliente inicializado"
        )

    except Exception:

        print(
            "OPENAI: ERROR inicializando cliente"
        )

        print(
            traceback.format_exc()
        )

else:

    print(
        "OPENAI: FALTA OPENAI_API_KEY"
    )


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte": "Corte de cabello",
    "corte de cabello": "Corte de cabello",
    "barba": "Barba",
    "corte y barba": "Corte y barba",
    "corte barba": "Corte y barba",
    "perfilado": "Perfilado de barba",
    "perfilado de barba": "Perfilado de barba",
}


PRECIO_SERVICIO = 20000

DURACION_CITA = 60


# ============================================================
# ALMACENAMIENTO TEMPORAL EN MEMORIA
# ============================================================

# Esta versión no usa base de datos.
# Los datos se conservan sólo mientras el proceso de Render esté activo.
# Si el servicio se reinicia o redeploya, esta información se pierde.

CLIENTES = {}
MENSAJES = {}
RESERVAS = []


# ============================================================
# CLIENTES
# ============================================================

def guardar_cliente(
    telefono,
    nombre=None
):

    cliente = CLIENTES.get(
        telefono,
        {
            "telefono": telefono,
            "nombre": None
        }
    )

    if nombre:
        cliente["nombre"] = nombre

    CLIENTES[telefono] = cliente


def obtener_cliente(
    telefono
):

    return CLIENTES.get(
        telefono
    )


# ============================================================
# MENSAJES
# ============================================================

def guardar_mensaje(
    telefono,
    rol,
    mensaje
):

    if telefono not in MENSAJES:
        MENSAJES[telefono] = []

    MENSAJES[telefono].append({
        "rol": rol,
        "mensaje": mensaje
    })

    # Dejamos sólo los últimos 50 mensajes por número.
    MENSAJES[telefono] = MENSAJES[telefono][-50:]


def obtener_historial(
    telefono,
    limite=20
):

    historial = MENSAJES.get(
        telefono,
        []
    )

    return historial[-limite:]


# ============================================================
# GOOGLE CALENDAR
# ============================================================

GOOGLE_REFRESH_TOKEN = os.environ.get(
    "GOOGLE_REFRESH_TOKEN"
)

GOOGLE_CLIENT_ID = os.environ.get(
    "GOOGLE_CLIENT_ID"
)

GOOGLE_CLIENT_SECRET = os.environ.get(
    "GOOGLE_CLIENT_SECRET"
)

GOOGLE_CALENDAR_ID = os.environ.get(
    "GOOGLE_CALENDAR_ID",
    "primary"
)


# ============================================================
# ADMIN
# ============================================================

ADMIN_USER = os.environ.get(
    "ADMIN_USER",
    "admin"
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    "cambiar-password"
)


# ============================================================
# OPENAI
# ============================================================

client = None

if OPENAI_API_KEY:

    try:

        client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        print(
            "OPENAI: cliente inicializado"
        )

    except Exception:

        print(
            "OPENAI: ERROR inicializando cliente"
        )

        print(
            traceback.format_exc()
        )

else:

    print(
        "OPENAI: FALTA OPENAI_API_KEY"
    )


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte": "Corte de cabello",
    "corte de cabello": "Corte de cabello",
    "barba": "Barba",
    "corte y barba": "Corte y barba",
    "corte barba": "Corte y barba",
    "perfilado": "Perfilado de barba",
    "perfilado de barba": "Perfilado de barba",
}


PRECIO_SERVICIO = 20000

DURACION_CITA = 60


# ============================================================
# BASE DE DATOS
# ============================================================

def get_db():

    if not DATABASE_URL:

        raise Exception(
            "DATABASE_URL no está configurada"
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


def columna_mensajes():

    """
    Detecta si la tabla existente usa:

    rol

    o

    role

    Esto evita el error:
    column "rol" does not exist
    """

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = 'mensajes'
        ORDER BY ordinal_position
    """)

    columnas = [
        row[0]
        for row in cur.fetchall()
    ]

    cur.close()
    conn.close()

    if "rol" in columnas:
        return "rol"

    if "role" in columnas:
        return "role"

    return None


def init_db():

    print(
        "INICIANDO BASE DE DATOS..."
    )

    conn = get_db()

    cur = conn.cursor()

    # --------------------------------------------------------
    # CLIENTES
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes (

            id SERIAL PRIMARY KEY,

            telefono VARCHAR(100)
                UNIQUE NOT NULL,

            nombre VARCHAR(255),

            creado_en TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP,

            actualizado_en TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --------------------------------------------------------
    # MENSAJES
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mensajes (

            id SERIAL PRIMARY KEY,

            telefono VARCHAR(100)
                NOT NULL,

            rol VARCHAR(30)
                NOT NULL,

            mensaje TEXT
                NOT NULL,

            creado_en TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --------------------------------------------------------
    # RESERVAS
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reservas (

            id SERIAL PRIMARY KEY,

            telefono VARCHAR(100),

            nombre VARCHAR(255),

            servicio VARCHAR(255),

            precio INTEGER,

            inicio TIMESTAMP,

            fin TIMESTAMP,

            google_event_id VARCHAR(255),

            meet_url TEXT,

            creado_en TIMESTAMP
                DEFAULT CURRENT_TIMESTAMP
        );
    """)

    conn.commit()

    cur.close()
    conn.close()

    print(
        "BASE DE DATOS: OK"
    )


# ============================================================
# CLIENTES
# ============================================================

def guardar_cliente(
    telefono,
    nombre=None
):

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO clientes
        (
            telefono,
            nombre
        )
        VALUES
        (
            %s,
            %s
        )

        ON CONFLICT (telefono)

        DO UPDATE SET

            nombre =
                COALESCE(
                    EXCLUDED.nombre,
                    clientes.nombre
                ),

            actualizado_en =
                CURRENT_TIMESTAMP
    """, (
        telefono,
        nombre
    ))

    conn.commit()

    cur.close()
    conn.close()


def obtener_cliente(
    telefono
):

    conn = get_db()

    cur = conn.cursor(
        cursor_factory=RealDictCursor
    )

    cur.execute("""
        SELECT *
        FROM clientes
        WHERE telefono = %s
        LIMIT 1
    """, (
        telefono,
    ))

    cliente = cur.fetchone()

    cur.close()
    conn.close()

    return cliente


# ============================================================
# MENSAJES
# ============================================================

def guardar_mensaje(
    telefono,
    rol,
    mensaje
):

    conn = get_db()

    cur = conn.cursor()

    columna = columna_mensajes()

    if columna is None:

        raise Exception(
            "La tabla mensajes no tiene "
            "columna rol ni role"
        )

    query = f"""
        INSERT INTO mensajes
        (
            telefono,
            {columna},
            mensaje
        )
        VALUES
        (
            %s,
            %s,
            %s
        )
    """

    cur.execute(
        query,
        (
            telefono,
            rol,
            mensaje
        )
    )

    conn.commit()

    cur.close()
    conn.close()


def obtener_historial(
    telefono,
    limite=20
):

    conn = get_db()

    cur = conn.cursor(
        cursor_factory=RealDictCursor
    )

    columna = columna_mensajes()

    if columna is None:

        raise Exception(
            "No existe columna rol/role"
        )

    query = f"""
        SELECT
            {columna} AS rol,
            mensaje
        FROM mensajes
        WHERE telefono = %s
        ORDER BY creado_en DESC
        LIMIT %s
    """

    cur.execute(
        query,
        (
            telefono,
            limite
        )
    )

    rows = cur.fetchall()

    cur.close()
    conn.close()

    rows.reverse()

    return rows


# ============================================================
# GOOGLE CALENDAR
# ============================================================

def get_calendar_service():

    if not GOOGLE_REFRESH_TOKEN:

        raise Exception(
            "GOOGLE_REFRESH_TOKEN no está configurado"
        )

    if not GOOGLE_CLIENT_ID:

        raise Exception(
            "GOOGLE_CLIENT_ID no está configurado"
        )

    if not GOOGLE_CLIENT_SECRET:

        raise Exception(
            "GOOGLE_CLIENT_SECRET no está configurado"
        )

    credentials = Credentials(

        token=None,

        refresh_token=GOOGLE_REFRESH_TOKEN,

        token_uri=(
            "https://oauth2.googleapis.com/token"
        ),

        client_id=GOOGLE_CLIENT_ID,

        client_secret=GOOGLE_CLIENT_SECRET,

        scopes=[
            "https://www.googleapis.com/auth/calendar"
        ]
    )

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


# ============================================================
# HORARIOS
# ============================================================

def es_horario_valido(
    inicio
):

    inicio = inicio.astimezone(
        TZ
    )

    # Domingo cerrado

    if inicio.weekday() == 6:

        return False

    hora = inicio.time()

    apertura = time(
        10,
        0
    )

    cierre = time(
        18,
        0
    )

    if hora < apertura:

        return False

    if hora >= cierre:

        return False

    return True


# ============================================================
# EVENTOS GOOGLE
# ============================================================

def obtener_eventos_dia(
    fecha
):

    service = get_calendar_service()

    inicio = TZ.localize(
        datetime.combine(
            fecha,
            time(
                0,
                0
            )
        )
    )

    fin = inicio + timedelta(
        days=1
    )

    eventos = service.events().list(

        calendarId=GOOGLE_CALENDAR_ID,

        timeMin=inicio.isoformat(),

        timeMax=fin.isoformat(),

        singleEvents=True,

        orderBy="startTime"

    ).execute()

    return eventos.get(
        "items",
        []
    )


# ============================================================
# DISPONIBILIDAD
# ============================================================

def esta_disponible(
    inicio
):

    inicio = inicio.astimezone(
        TZ
    )

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    if not es_horario_valido(
        inicio
    ):

        return False

    if fin.time() > time(
        18,
        0
    ):

        return False

    eventos = obtener_eventos_dia(
        inicio.date()
    )

    for evento in eventos:

        start_data = evento.get(
            "start",
            {}
        )

        end_data = evento.get(
            "end",
            {}
        )

        start_str = start_data.get(
            "dateTime"
        )

        end_str = end_data.get(
            "dateTime"
        )

        # Eventos de día completo

        if not start_str or not end_str:

            continue

        start = datetime.fromisoformat(
            start_str.replace(
                "Z",
                "+00:00"
            )
        ).astimezone(TZ)

        end = datetime.fromisoformat(
            end_str.replace(
                "Z",
                "+00:00"
            )
        ).astimezone(TZ)

        if (
            inicio < end
            and fin > start
        ):

            return False

    return True


def buscar_horarios_disponibles(
    fecha,
    cantidad=5
):

    resultados = []

    hora = TZ.localize(
        datetime.combine(
            fecha,
            time(
                10,
                0
            )
        )
    )

    cierre = TZ.localize(
        datetime.combine(
            fecha,
            time(
                18,
                0
            )
        )
    )

    while hora < cierre:

        fin = (
            hora
            + timedelta(
                minutes=DURACION_CITA
            )
        )

        if fin <= cierre:

            if esta_disponible(
                hora
            ):

                resultados.append(
                    hora
                )

                if len(resultados) >= cantidad:

                    break

        hora += timedelta(
            minutes=30
        )

    return resultados


# ============================================================
# CREAR RESERVA GOOGLE
# ============================================================

def crear_reserva_google(
    nombre,
    telefono,
    servicio,
    inicio
):

    service = get_calendar_service()

    inicio = inicio.astimezone(
        TZ
    )

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    titulo = (
        f"Cita - {nombre} - {servicio}"
    )

    precio_texto = (
        f"${PRECIO_SERVICIO:,}"
        .replace(
            ",",
            "."
        )
    )

    descripcion = (
        "Reserva realizada mediante "
        "Asistente Virtual de Estilista Diego.\n\n"
        f"Cliente: {nombre}\n"
        f"WhatsApp: {telefono}\n"
        f"Servicio: {servicio}\n"
        f"Precio: {precio_texto}"
    )

    evento = {

        "summary": titulo,

        "description": descripcion,

        "start": {

            "dateTime":
                inicio.isoformat(),

            "timeZone":
                str(TZ)
        },

        "end": {

            "dateTime":
                fin.isoformat(),

            "timeZone":
                str(TZ)
        },

        "conferenceData": {

            "createRequest": {

                "requestId":
                    str(uuid.uuid4()),

                "conferenceSolutionKey": {

                    "type":
                        "hangoutsMeet"
                }
            }
        }
    }

    creado = service.events().insert(

        calendarId=
            GOOGLE_CALENDAR_ID,

        body=evento,

        conferenceDataVersion=1,

        sendUpdates="all"

    ).execute()

    meet_url = None

    conference = creado.get(
        "conferenceData",
        {}
    )

    for entry in conference.get(
        "entryPoints",
        []
    ):

        if (
            entry.get(
                "entryPointType"
            )
            == "video"
        ):

            meet_url = entry.get(
                "uri"
            )

            break

    return creado, meet_url


# ============================================================
# GUARDAR RESERVA
# ============================================================

def guardar_reserva(
    telefono,
    nombre,
    servicio,
    inicio,
    evento,
    meet_url
):

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    RESERVAS.append({
        "telefono": telefono,
        "nombre": nombre,
        "servicio": servicio,
        "precio": PRECIO_SERVICIO,
        "inicio": inicio,
        "fin": fin,
        "google_event_id": evento.get("id"),
        "meet_url": meet_url
    })


# ============================================================
# GPT
# ============================================================

SYSTEM_PROMPT = """
Eres el Asistente Virtual de Estilista Diego.

Atiendes clientes por WhatsApp.

Hablas español natural de Chile.

Eres amable, cercano, breve y humano.

No debes sonar como robot.

Tu principal objetivo es ayudar al cliente a reservar
una hora con Estilista Diego.

HORARIO:

Lunes a sábado:
10:00 a 18:00.

Domingo:
cerrado.

DURACIÓN:

Cada cita dura 1 hora.

PRECIO:

Todos los servicios tienen un valor de $20.000.

SERVICIOS:

- Corte de cabello
- Barba
- Corte y barba
- Perfilado de barba

IMPORTANTE:

Puedes conversar normalmente.

Si el cliente solamente dice hola,
saluda y conversa.

NO fuerces el agendamiento.

Si quiere agendar, necesitas:

- nombre
- servicio
- día
- hora

Nunca inventes disponibilidad.

Cuando el sistema te entregue horarios disponibles,
solo puedes mencionar esos horarios.

La reserva se realiza solamente en el calendario
de Estilista Diego.

No necesitas el calendario del cliente.

Si falta un dato para reservar,
pregúntalo de manera natural.

No vuelvas a pedir información que el cliente
ya entregó.

Cuando el sistema confirme una reserva,
informa:

- nombre
- servicio
- fecha
- hora
- precio
- confirmación

Si existe Google Meet puedes mencionarlo.

No digas que eres una IA salvo que el cliente
pregunte directamente.
"""


def preguntar_gpt(
    telefono,
    mensaje,
    contexto_extra=""
):

    if not client:

        return (
            "Hola 😊 Estoy teniendo un problema "
            "con el asistente en este momento. "
            "Intenta nuevamente en unos minutos."
        )

    historial = obtener_historial(
        telefono,
        20
    )

    mensajes = [

        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }

    ]

    if contexto_extra:

        mensajes.append({

            "role": "system",

            "content":
                contexto_extra

        })

    for item in historial:

        rol = item.get(
            "rol"
        )

        if rol not in (
            "user",
            "assistant"
        ):

            continue

        mensajes.append({

            "role": rol,

            "content":
                item.get(
                    "mensaje",
                    ""
                )

        })

    mensajes.append({

        "role": "user",

        "content": mensaje

    })

    try:

        respuesta = client.chat.completions.create(

            model=OPENAI_MODEL,

            messages=mensajes,

            temperature=0.7

        )

        return (
            respuesta
            .choices[0]
            .message
            .content
            .strip()
        )

    except Exception as e:

        print(
            "ERROR OPENAI:"
        )

        print(
            traceback.format_exc()
        )

        raise


# ============================================================
# DETECCIÓN DE INTENCIÓN
# ============================================================

def parece_agendamiento(
    texto
):

    texto = texto.lower()

    palabras = [

        "reservar",
        "reserva",
        "agendar",
        "agenda",
        "cita",
        "turno",
        "hora",
        "disponibilidad",
        "disponible",
        "quiero ir",
        "quiero una hora"

    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


# ============================================================
# DETECTAR SERVICIO
# ============================================================

def detectar_servicio(
    texto
):

    texto = texto.lower()

    # primero las frases largas

    if (
        "corte y barba"
        in texto
        or "corte más barba"
        in texto
        or "corte mas barba"
        in texto
    ):

        return "Corte y barba"

    if (
        "perfilado de barba"
        in texto
        or "perfilado"
        in texto
    ):

        return "Perfilado de barba"

    if "barba" in texto:

        return "Barba"

    if "corte de cabello" in texto:

        return "Corte de cabello"

    if (
        re.search(
            r"\bcorte\b",
            texto
        )
    ):

        return "Corte de cabello"

    return None


# ============================================================
# DETECTAR HORA
# ============================================================

def detectar_hora(
    texto
):

    texto = texto.lower()

    patrones = [

        r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b",

        r"\b([01]?\d|2[0-3])\s*(?:hrs?|horas?)\b",

    ]

    for patron in patrones:

        match = re.search(
            patron,
            texto
        )

        if match:

            hora = int(
                match.group(1)
            )

            if match.lastindex >= 2:

                minuto = int(
                    match.group(2)
                )

            else:

                minuto = 0

            # Casos como 7 pm

            if (
                "pm" in texto
                and hora < 12
            ):

                hora += 12

            if (
                10 <= hora <= 18
            ):

                return hora, minuto

    # formatos "7 pm", "7:30 pm"

    match = re.search(
        r"\b(1[0-2]|[1-9])"
        r"(?:[:.]([0-5]\d))?"
        r"\s*(am|pm)\b",
        texto
    )

    if match:

        hora = int(
            match.group(1)
        )

        minuto = (
            int(match.group(2))
            if match.group(2)
            else 0
        )

        periodo = match.group(3)

        if periodo == "pm" and hora < 12:

            hora += 12

        if periodo == "am" and hora == 12:

            hora = 0

        if 10 <= hora <= 18:

            return hora, minuto

    return None


# ============================================================
# DETECTAR FECHA
# ============================================================

def detectar_fecha(
    texto
):

    ahora = datetime.now(
        TZ
    )

    try:

        fecha = dateparser.parse(

            texto,

            languages=["es"],

            settings={

                "TIMEZONE":
                    "America/Santiago",

                "RETURN_AS_TIMEZONE_AWARE":
                    True,

                "PREFER_DATES_FROM":
                    "future",

                "RELATIVE_BASE":
                    ahora
            }
        )

        if not fecha:

            return None

        return fecha.astimezone(
            TZ
        )

    except Exception:

        print(
            "ERROR detectando fecha:"
        )

        print(
            traceback.format_exc()
        )

        return None


# ============================================================
# OBTENER DATOS DE CONVERSACIÓN
# ============================================================

def extraer_datos_conversacion(
    telefono,
    mensaje
):

    historial = obtener_historial(
        telefono,
        30
    )

    texto_total = ""

    for item in historial:

        texto_total += (
            " "
            + item.get(
                "mensaje",
                ""
            )
        )

    texto_total += (
        " "
        + mensaje
    )

    cliente = obtener_cliente(
        telefono
    )

    nombre = None

    if cliente:

        nombre = cliente.get(
            "nombre"
        )

    servicio = detectar_servicio(
        texto_total
    )

    fecha = detectar_fecha(
        texto_total
    )

    hora_data = detectar_hora(
        texto_total
    )

    return {
        "nombre": nombre,
        "servicio": servicio,
        "fecha": fecha,
        "hora": hora_data
    }


# ============================================================
# DETECTAR NOMBRE
# ============================================================

def detectar_nombre(
    texto
):

    patrones = [

        r"(?:me llamo|mi nombre es)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40})",

        r"(?:soy)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40})",

        r"(?:nombre)\s+(?:es\s+)?([A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40})"

    ]

    for patron in patrones:

        match = re.search(
            patron,
            texto,
            re.IGNORECASE
        )

        if match:

            nombre = match.group(
                1
            ).strip()

            nombre = re.sub(
                r"\s+",
                " ",
                nombre
            )

            # evitar que capture demasiado

            palabras = nombre.split()

            if len(palabras) > 4:

                nombre = " ".join(
                    palabras[:4]
                )

            return nombre.title()

    return None


# ============================================================
# CONSTRUIR DATETIME
# ============================================================

def construir_inicio(
    fecha,
    hora_data
):

    if not fecha or not hora_data:

        return None

    hora, minuto = hora_data

    naive = datetime.combine(

        fecha.date(),

        time(
            hora,
            minuto
        )
    )

    return TZ.localize(
        naive
    )


# ============================================================
# FORMATEAR FECHA
# ============================================================

def nombre_dia(
    fecha
):

    dias = [

        "lunes",
        "martes",
        "miércoles",
        "jueves",
        "viernes",
        "sábado",
        "domingo"

    ]

    return dias[
        fecha.weekday()
    ]


def formatear_fecha(
    fecha
):

    return (
        f"{nombre_dia(fecha)} "
        f"{fecha.day:02d}/"
        f"{fecha.month:02d}/"
        f"{fecha.year}"
    )


# ============================================================
# FORMATEAR HORARIOS
# ============================================================

def formatear_horarios(
    horarios
):

    if not horarios:

        return (
            "No encontré horas disponibles "
            "para ese día 😕"
        )

    textos = []

    for hora in horarios:

        textos.append(
            hora.strftime(
                "%H:%M"
            )
        )

    return ", ".join(
        textos
    )


# ============================================================
# PROCESAR MENSAJE
# ============================================================

def procesar_mensaje(
    telefono,
    mensaje
):

    print(
        "------------------------------------------"
    )

    print(
        f"WHATSAPP DE: {telefono}"
    )

    print(
        f"MENSAJE: {mensaje}"
    )

    print(
        "------------------------------------------"
    )

    cliente = obtener_cliente(
        telefono
    )

    # --------------------------------------------------------
    # DETECTAR NOMBRE DIRECTAMENTE
    # --------------------------------------------------------

    nombre_detectado = detectar_nombre(
        mensaje
    )

    if nombre_detectado:

        guardar_cliente(
            telefono,
            nombre_detectado
        )

    cliente = obtener_cliente(
        telefono
    )

    nombre_actual = None

    if cliente:

        nombre_actual = cliente.get(
            "nombre"
        )

    # --------------------------------------------------------
    # SI NO ES AGENDAMIENTO
    # --------------------------------------------------------

    if not parece_agendamiento(
        mensaje
    ):

        respuesta = preguntar_gpt(

            telefono,

            mensaje

        )

        guardar_mensaje(

            telefono,

            "user",

            mensaje

        )

        guardar_mensaje(

            telefono,

            "assistant",

            respuesta

        )

        return respuesta

    # --------------------------------------------------------
    # EXTRAER DATOS
    # --------------------------------------------------------

    datos = extraer_datos_conversacion(

        telefono,

        mensaje

    )

    nombre = (
        nombre_actual
        or datos["nombre"]
    )

    servicio = datos["servicio"]

    fecha = datos["fecha"]

    hora_data = datos["hora"]

    # --------------------------------------------------------
    # GUARDAR NOMBRE SI EXISTE
    # --------------------------------------------------------

    if nombre:

        guardar_cliente(
            telefono,
            nombre
        )

    # --------------------------------------------------------
    # SI HAY FECHA
    # --------------------------------------------------------

    if fecha:

        if fecha.date() < datetime.now(
            TZ
        ).date():

            respuesta = (
                "Esa fecha ya pasó 😅 "
                "Dime qué día te gustaría "
                "venir y revisamos disponibilidad."
            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        if fecha.weekday() == 6:

            respuesta = (
                "Los domingos no atiendo 😊 "
                "Podemos buscar una hora de "
                "lunes a sábado."
            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

    # --------------------------------------------------------
    # SI HAY FECHA PERO NO HORA
    # --------------------------------------------------------

    if fecha and not hora_data:

        try:

            horarios = buscar_horarios_disponibles(

                fecha.date(),

                cantidad=5

            )

            horarios_texto = (
                formatear_horarios(
                    horarios
                )
            )

            contexto = f"""
El cliente quiere agendar.

Fecha:
{formatear_fecha(fecha)}

Horarios reales disponibles:
{horarios_texto}

Servicio detectado:
{servicio or "todavía no indicado"}

Nombre:
{nombre or "todavía no indicado"}

Debes continuar la conversación naturalmente.

No inventes horarios.

Si faltan datos, pregunta solo lo necesario.
"""

            respuesta = preguntar_gpt(

                telefono,

                mensaje,

                contexto

            )

        except Exception:

            print(
                "ERROR CONSULTANDO CALENDARIO:"
            )

            print(
                traceback.format_exc()
            )

            respuesta = (
                "Estoy revisando la agenda "
                "en este momento 🙏 "
                "Intenta nuevamente en unos segundos."
            )

        guardar_mensaje(
            telefono,
            "user",
            mensaje
        )

        guardar_mensaje(
            telefono,
            "assistant",
            respuesta
        )

        return respuesta

    # --------------------------------------------------------
    # SI HAY FECHA + HORA
    # --------------------------------------------------------

    if fecha and hora_data:

        inicio = construir_inicio(

            fecha,

            hora_data

        )

        if not inicio:

            return (
                "No pude interpretar bien "
                "la hora 😅"
            )

        # --------------------------------------------
        # VALIDAR HORARIO
        # --------------------------------------------

        if not es_horario_valido(
            inicio
        ):

            respuesta = (
                "Ese horario está fuera de atención 😊\n\n"
                "Trabajo de lunes a sábado "
                "entre las 10:00 y las 18:00.\n\n"
                "Dime otra hora y la revisamos."
            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        # --------------------------------------------
        # SI FALTA SERVICIO
        # --------------------------------------------

        if not servicio:

            respuesta = preguntar_gpt(

                telefono,

                mensaje,

                f"""
El cliente ya indicó la fecha
{formatear_fecha(fecha)}
y la hora
{inicio.strftime("%H:%M")}.

Todavía falta conocer el servicio.

Servicios disponibles:
- Corte de cabello
- Barba
- Corte y barba
- Perfilado de barba

Pregunta naturalmente cuál desea.
"""

            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        # --------------------------------------------
        # SI FALTA NOMBRE
        # --------------------------------------------

        if not nombre:

            respuesta = preguntar_gpt(

                telefono,

                mensaje,

                f"""
El cliente quiere reservar:

Servicio:
{servicio}

Fecha:
{formatear_fecha(fecha)}

Hora:
{inicio.strftime("%H:%M")}

Falta solamente su nombre.

Pregunta de manera natural cómo se llama.
"""

            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        # --------------------------------------------
        # COMPROBAR DISPONIBILIDAD
        # --------------------------------------------

        try:

            disponible = esta_disponible(
                inicio
            )

        except Exception:

            print(
                "ERROR COMPROBANDO DISPONIBILIDAD:"
            )

            print(
                traceback.format_exc()
            )

            raise

        if not disponible:

            horarios = buscar_horarios_disponibles(

                fecha.date(),

                cantidad=5

            )

            horarios_texto = (
                formatear_horarios(
                    horarios
                )
            )

            respuesta = (
                f"La hora {inicio.strftime('%H:%M')} "
                f"ya está ocupada 😕\n\n"
                f"Para ese día tengo disponibles: "
                f"{horarios_texto}\n\n"
                f"¿Te sirve alguna?"
            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        # --------------------------------------------
        # CREAR RESERVA
        # --------------------------------------------

        try:

            print(
                "CREANDO RESERVA GOOGLE..."
            )

            evento, meet_url = (
                crear_reserva_google(

                    nombre,

                    telefono,

                    servicio,

                    inicio

                )
            )

            guardar_reserva(

                telefono,

                nombre,

                servicio,

                inicio,

                evento,

                meet_url

            )

            print(
                "RESERVA CREADA CORRECTAMENTE"
            )

        except Exception:

            print(
                "ERROR CREANDO RESERVA:"
            )

            print(
                traceback.format_exc()
            )

            respuesta = (
                "Intenté reservar esa hora, "
                "pero tuve un problema al "
                "guardar la cita en la agenda 😕.\n\n"
                "No te preocupes, no voy a decir "
                "que quedó agendada hasta confirmar "
                "correctamente."
            )

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        # --------------------------------------------
        # CONFIRMACIÓN
        # --------------------------------------------

        precio_texto = (
            f"${PRECIO_SERVICIO:,}"
            .replace(
                ",",
                "."
            )
        )

        respuesta = (
            "¡Listo! 🎉 Tu cita quedó agendada.\n\n"
            f"👤 Nombre: {nombre}\n"
            f"💈 Servicio: {servicio}\n"
            f"📅 Fecha: {formatear_fecha(fecha)}\n"
            f"🕐 Hora: {inicio.strftime('%H:%M')}\n"
            f"💰 Precio: {precio_texto}\n\n"
            "Te espero 😊"
        )

        if meet_url:

            respuesta += (
                f"\n\n🔗 Google Meet:\n"
                f"{meet_url}"
            )

        guardar_mensaje(
            telefono,
            "user",
            mensaje
        )

        guardar_mensaje(
            telefono,
            "assistant",
            respuesta
        )

        return respuesta

    # --------------------------------------------------------
    # CONVERSACIÓN DE AGENDAMIENTO
    # --------------------------------------------------------

    contexto = f"""
El cliente está intentando agendar una cita.

Nombre conocido:
{nombre or "No indicado"}

Servicio conocido:
{servicio or "No indicado"}

Fecha conocida:
{
    formatear_fecha(fecha)
    if fecha
    else "No indicada"
}

Hora conocida:
{
    hora_data
    if hora_data
    else "No indicada"
}

Precio:
$20.000

Duración:
1 hora

Horario:
Lunes a sábado de 10:00 a 18:00.

Si falta información,
pregunta de manera natural.

No inventes disponibilidad.
"""

    respuesta = preguntar_gpt(

        telefono,

        mensaje,

        contexto

    )

    guardar_mensaje(
        telefono,
        "user",
        mensaje
    )

    guardar_mensaje(
        telefono,
        "assistant",
        respuesta
    )

    return respuesta


# ============================================================
# WEBHOOK WHATSAPP
# ============================================================

@app.route(
    "/webhook/whatsapp",
    methods=["POST"]
)
def whatsapp_webhook():

    print(
        "=========================================="
    )

    print(
        "WHATSAPP WEBHOOK RECIBIDO"
    )

    print(
        "=========================================="
    )

    try:

        telefono = request.form.get(
            "From",
            ""
        ).strip()

        mensaje = request.form.get(
            "Body",
            ""
        ).strip()

        message_sid = request.form.get(
            "MessageSid"
        )

        print(
            f"From: {telefono}"
        )

        print(
            f"Body: {mensaje}"
        )

        print(
            f"MessageSid: {message_sid}"
        )

        if not telefono:

            print(
                "ERROR: falta From"
            )

            return (
                "Missing From",
                400
            )

        if not mensaje:

            print(
                "ERROR: falta Body"
            )

            return (
                "Missing Body",
                400
            )

        # ----------------------------------------------------
        # GUARDAR CLIENTE
        # ----------------------------------------------------

        guardar_cliente(
            telefono
        )

        # ----------------------------------------------------
        # PROCESAR
        # ----------------------------------------------------

        respuesta = procesar_mensaje(

            telefono,

            mensaje

        )

        print(
            "RESPUESTA:"
        )

        print(
            respuesta
        )

        # ----------------------------------------------------
        # TWIML
        # ----------------------------------------------------

        twiml = MessagingResponse()

        twiml.message(
            respuesta
        )

        xml = str(
            twiml
        )

        print(
            "TWIML GENERADO CORRECTAMENTE"
        )

        return (
            xml,
            200,
            {
                "Content-Type":
                    "application/xml; charset=utf-8"
            }
        )

    except Exception as e:

        print(
            "=========================================="
        )

        print(
            "ERROR WEBHOOK WHATSAPP"
        )

        print(
            "=========================================="
        )

        print(
            traceback.format_exc()
        )

        twiml = MessagingResponse()

        twiml.message(
            "Disculpa 🙏 Estoy teniendo un "
            "pequeño problema técnico. "
            "Por favor intenta nuevamente."
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
# HEALTH CHECK
# ============================================================

@app.route("/")
def home():

    return """
    <!DOCTYPE html>

    <html>

    <head>

        <meta charset="UTF-8">

        <title>
            Asistente Virtual Estilista Diego
        </title>

    </head>

    <body style="
        font-family: Arial;
        max-width: 800px;
        margin: 60px auto;
        padding: 20px;
    ">

        <h1>
            💈 Asistente Virtual de Estilista Diego
        </h1>

        <p>
            Sistema funcionando correctamente.
        </p>

        <hr>

        <p>
            💬 WhatsApp: Twilio
        </p>

        <p>
            🤖 IA: OpenAI
        </p>

        <p>
            📅 Agenda: Google Calendar
        </p>

        <p>
            🧠 Datos temporales: memoria del servidor
        </p>

        <p>
            <a href="/health">
                Ver estado del sistema
            </a>
        </p>

    </body>

    </html>
    """


@app.route(
    "/health"
)
def health():

    estado = {

        "status":
            "ok",

        "service":
            "estilista-diego",

        "openai":
            bool(OPENAI_API_KEY),

        "twilio_sid":
            bool(TWILIO_ACCOUNT_SID),

        "twilio_auth_token":
            bool(TWILIO_AUTH_TOKEN),

        "twilio_whatsapp_from":
            TWILIO_WHATSAPP_FROM,

        "google_refresh_token":
            bool(GOOGLE_REFRESH_TOKEN),

        "google_client_id":
            bool(GOOGLE_CLIENT_ID),

        "google_client_secret":
            bool(GOOGLE_CLIENT_SECRET)

    }

    return jsonify(
        estado
    )


# ============================================================
# LOGIN ADMIN
# ============================================================

@app.route(
    "/admin/login",
    methods=[
        "GET",
        "POST"
    ]
)
def admin_login():

    if request.method == "POST":

        usuario = request.form.get(
            "usuario",
            ""
        )

        password = request.form.get(
            "password",
            ""
        )

        if (
            usuario == ADMIN_USER
            and password == ADMIN_PASSWORD
        ):

            session["admin"] = True

            return redirect(
                url_for(
                    "admin"
                )
            )

        return render_template_string(

            ADMIN_LOGIN_HTML,

            error=
                "Usuario o contraseña incorrectos"

        )

    return render_template_string(
        ADMIN_LOGIN_HTML
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route(
    "/admin/logout"
)
def admin_logout():

    session.clear()

    return redirect(
        url_for(
            "admin_login"
        )
    )


# ============================================================
# PANEL ADMIN
# ============================================================

@app.route(
    "/admin"
)
def admin():

    if not session.get(
        "admin"
    ):

        return redirect(
            url_for(
                "admin_login"
            )
        )

    reservas = sorted(
        RESERVAS,
        key=lambda r: r["inicio"],
        reverse=True
    )[:100]

    return render_template_string(

        ADMIN_HTML,

        reservas=reservas

    )


# ============================================================
# HTML LOGIN
# ============================================================

ADMIN_LOGIN_HTML = """

<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<title>
Administración
</title>

<style>

body {

    font-family: Arial;

    background: #f4f4f4;

}

.box {

    width: 350px;

    margin: 100px auto;

    background: white;

    padding: 30px;

    border-radius: 12px;

    box-shadow:
        0 4px 20px
        rgba(0,0,0,.1);

}

input {

    width: 100%;

    padding: 12px;

    margin: 8px 0;

    box-sizing: border-box;

}

button {

    width: 100%;

    padding: 12px;

    cursor: pointer;

}

.error {

    color: red;

}

</style>

</head>

<body>

<div class="box">

<h2>
💈 Administración
</h2>

{% if error %}

<p class="error">
{{ error }}
</p>

{% endif %}

<form method="POST">

<input
    name="usuario"
    placeholder="Usuario"
    required
>

<input
    type="password"
    name="password"
    placeholder="Contraseña"
    required
>

<button type="submit">
Ingresar
</button>

</form>

</div>

</body>

</html>

"""


# ============================================================
# HTML ADMIN
# ============================================================

ADMIN_HTML = """

<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<title>
Reservas - Estilista Diego
</title>

<style>

body {

    font-family: Arial;

    margin: 30px;

}

table {

    border-collapse: collapse;

    width: 100%;

}

th,
td {

    border: 1px solid #ddd;

    padding: 10px;

    text-align: left;

}

th {

    background: #eee;

}

a {

    text-decoration: none;

}

</style>

</head>

<body>

<h1>
💈 Reservas - Estilista Diego
</h1>

<p>

<a href="/admin/logout">
Cerrar sesión
</a>

</p>

<table>

<tr>

<th>
Fecha
</th>

<th>
Cliente
</th>

<th>
WhatsApp
</th>

<th>
Servicio
</th>

<th>
Precio
</th>

<th>
Google Meet
</th>

</tr>

{% for reserva in reservas %}

<tr>

<td>
{{ reserva.inicio }}
</td>

<td>
{{ reserva.nombre }}
</td>

<td>
{{ reserva.telefono }}
</td>

<td>
{{ reserva.servicio }}
</td>

<td>

${{
    "{:,}".format(
        reserva.precio
    ).replace(
        ",",
        "."
    )
}}

</td>

<td>

{% if reserva.meet_url %}

<a
    href="{{ reserva.meet_url }}"
    target="_blank"
>
Abrir Meet
</a>

{% else %}

-

{% endif %}

</td>

</tr>

{% endfor %}

</table>

</body>

</html>

"""


# ============================================================
# INICIALIZACIÓN
# ============================================================

print(
    "=========================================="
)

print(
    "INICIANDO ASISTENTE ESTILISTA DIEGO"
)

print(
    "=========================================="
)

print(
    "CONFIGURACIÓN:"
)

print(
    "OPENAI_API_KEY:",
    "OK"
    if OPENAI_API_KEY
    else "FALTA"
)


print(
    "TWILIO_ACCOUNT_SID:",
    "OK"
    if TWILIO_ACCOUNT_SID
    else "FALTA"
)

print(
    "TWILIO_AUTH_TOKEN:",
    "OK"
    if TWILIO_AUTH_TOKEN
    else "FALTA"
)

print(
    "TWILIO_WHATSAPP_FROM:",
    TWILIO_WHATSAPP_FROM
)

print(
    "GOOGLE_REFRESH_TOKEN:",
    "OK"
    if GOOGLE_REFRESH_TOKEN
    else "FALTA"
)

print(
    "GOOGLE_CLIENT_ID:",
    "OK"
    if GOOGLE_CLIENT_ID
    else "FALTA"
)

print(
    "GOOGLE_CLIENT_SECRET:",
    "OK"
    if GOOGLE_CLIENT_SECRET
    else "FALTA"
)

print(
    "OPENAI_MODEL:",
    OPENAI_MODEL
)

print(
    "TIMEZONE:",
    TZ
)

print(
    "=========================================="
)




# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    print(
        "=========================================="
    )

    print(
        f"INICIANDO FLASK EN PUERTO {port}"
    )

    print(
        "=========================================="
    )

    app.run(

        host="0.0.0.0",

        port=port,

        debug=False

    )
