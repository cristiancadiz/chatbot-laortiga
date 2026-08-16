import os
import re
import uuid
import traceback
from datetime import datetime, timedelta, time

import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
import dateparser

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


# ============================================================
# VARIABLES DE ENTORNO
# ============================================================

OPENAI_API_KEY = os.environ.get(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-5-mini"
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL"
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

# Normalizamos automáticamente
if TWILIO_WHATSAPP_FROM:
    if not TWILIO_WHATSAPP_FROM.startswith("whatsapp:"):
        TWILIO_WHATSAPP_FROM = (
            "whatsapp:" +
            TWILIO_WHATSAPP_FROM
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

        print("OPENAI: cliente inicializado")

    except Exception:

        print(
            "ERROR INICIALIZANDO OPENAI"
        )

        print(
            traceback.format_exc()
        )


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte": "Corte de cabello",
    "corte de cabello": "Corte de cabello",
    "barba": "Barba",
    "perfilado de barba": "Perfilado de barba",
    "corte y barba": "Corte y barba",
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
            telefono VARCHAR(100) UNIQUE NOT NULL,
            nombre VARCHAR(255),
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # --------------------------------------------------------
    # MENSAJES
    #
    # IMPORTANTE:
    # Tu tabla existente usa "role".
    # Por eso usamos role y NO rol.
    # --------------------------------------------------------

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mensajes (
            id SERIAL PRIMARY KEY,
            telefono VARCHAR(100) NOT NULL,
            role VARCHAR(30) NOT NULL,
            mensaje TEXT NOT NULL,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            nombre = COALESCE(
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
    role,
    mensaje
):

    conn = get_db()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO mensajes
        (
            telefono,
            role,
            mensaje
        )
        VALUES
        (
            %s,
            %s,
            %s
        )
    """, (
        telefono,
        role,
        mensaje
    ))

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

    cur.execute("""
        SELECT
            role,
            mensaje
        FROM mensajes
        WHERE telefono = %s
        ORDER BY creado_en DESC
        LIMIT %s
    """, (
        telefono,
        limite
    ))

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

    service = build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    return service


# ============================================================
# HORARIOS
# ============================================================

def es_horario_valido(
    inicio
):

    inicio = inicio.astimezone(TZ)

    # Domingo cerrado

    if inicio.weekday() == 6:

        return False

    apertura = time(
        10,
        0
    )

    cierre = time(
        18,
        0
    )

    hora = inicio.time()

    if hora < apertura:

        return False

    if hora >= cierre:

        return False

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    if fin.time() > cierre:

        return False

    return True


# ============================================================
# EVENTOS DEL DÍA
# ============================================================

def obtener_eventos_dia(
    fecha
):

    service = get_calendar_service()

    inicio = TZ.localize(
        datetime.combine(
            fecha,
            time(0, 0)
        )
    )

    fin = inicio + timedelta(
        days=1
    )

    resultado = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=inicio.isoformat(),
        timeMax=fin.isoformat(),
        singleEvents=True,
        orderBy="startTime"
    ).execute()

    return resultado.get(
        "items",
        []
    )


# ============================================================
# DISPONIBILIDAD
# ============================================================

def esta_disponible(
    inicio
):

    inicio = inicio.astimezone(TZ)

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    if not es_horario_valido(
        inicio
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

        try:

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

        except Exception:

            continue

        if (
            inicio < end
            and fin > start
        ):

            return False

    return True


# ============================================================
# BUSCAR HORARIOS
# ============================================================

def buscar_horarios_disponibles(
    fecha,
    cantidad=5
):

    resultados = []

    inicio = TZ.localize(
        datetime.combine(
            fecha,
            time(10, 0)
        )
    )

    cierre = TZ.localize(
        datetime.combine(
            fecha,
            time(18, 0)
        )
    )

    hora = inicio

    while hora < cierre:

        if (
            hora +
            timedelta(
                minutes=DURACION_CITA
            )
            <= cierre
        ):

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

    inicio = inicio.astimezone(TZ)

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    titulo = (
        f"Cita - {nombre} - {servicio}"
    )

    descripcion = (
        "Reserva realizada mediante "
        "Asistente Virtual de Estilista Diego.\n\n"
        f"Cliente: {nombre}\n"
        f"WhatsApp: {telefono}\n"
        f"Servicio: {servicio}\n"
        f"Precio: ${PRECIO_SERVICIO:,}".replace(
            ",",
            "."
        )
    )

    evento = {

        "summary": titulo,

        "description": descripcion,

        "start": {
            "dateTime": inicio.isoformat(),
            "timeZone": str(TZ)
        },

        "end": {
            "dateTime": fin.isoformat(),
            "timeZone": str(TZ)
        },

        "conferenceData": {

            "createRequest": {

                "requestId": str(
                    uuid.uuid4()
                ),

                "conferenceSolutionKey": {

                    "type": "hangoutsMeet"

                }
            }
        }
    }

    creado = service.events().insert(
        calendarId=GOOGLE_CALENDAR_ID,
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
# GUARDAR RESERVA DB
# ============================================================

def guardar_reserva(
    telefono,
    nombre,
    servicio,
    inicio,
    evento,
    meet_url
):

    conn = get_db()

    cur = conn.cursor()

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    cur.execute("""
        INSERT INTO reservas
        (
            telefono,
            nombre,
            servicio,
            precio,
            inicio,
            fin,
            google_event_id,
            meet_url
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """, (
        telefono,
        nombre,
        servicio,
        PRECIO_SERVICIO,
        inicio,
        fin,
        evento.get("id"),
        meet_url
    ))

    conn.commit()

    cur.close()
    conn.close()


# ============================================================
# GPT
# ============================================================

SYSTEM_PROMPT = """
Eres el Asistente Virtual de Estilista Diego.

Atiendes clientes por WhatsApp.

Hablas español natural de Chile.

Tu estilo es:
- cercano
- amable
- breve
- humano
- natural

NO parezcas un robot.

El objetivo principal es ayudar a los clientes a reservar
una hora con Estilista Diego.

Puedes conversar normalmente.

NO fuerces una reserva.

Si el cliente solamente dice:
"hola"

responde naturalmente.

Ejemplo:

"¡Hola! 😊 ¿Cómo estás? Soy el asistente de Diego.
¿En qué te puedo ayudar?"

SERVICIOS:

- Corte de cabello
- Barba
- Perfilado de barba
- Corte y barba

PRECIO:

$20.000

DURACIÓN:

60 minutos.

HORARIO:

Lunes a sábado:
10:00 a 18:00

Domingo:
cerrado.

Las reservas se realizan solamente en el calendario
de Estilista Diego.

Nunca inventes disponibilidad.

Si el sistema te entrega horarios disponibles,
solo puedes ofrecer esos horarios.

Cuando falte información para reservar,
pregunta solamente por lo que falta.

Datos necesarios:

1. Nombre
2. Servicio
3. Día
4. Hora

No vuelvas a preguntar información que el cliente
ya entregó.

Si el cliente pregunta por una fecha u horario,
el sistema puede entregar disponibilidad real.

Si una reserva se confirma correctamente,
informa claramente:

✅ Reserva confirmada

Nombre
Servicio
Fecha
Hora
Precio

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
            "Hola 😊 En este momento estoy teniendo "
            "un problema técnico. Escríbeme nuevamente "
            "en unos minutos."
        )

    historial = obtener_historial(
        telefono,
        limite=20
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

            "content": contexto_extra

        })

    for item in historial:

        role = item.get(
            "role"
        )

        if role not in (
            "user",
            "assistant"
        ):

            continue

        mensajes.append({

            "role": role,

            "content": item.get(
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

            messages=mensajes

        )

        texto = (
            respuesta
            .choices[0]
            .message
            .content
        )

        if not texto:

            return (
                "Cuéntame 😊 ¿En qué te puedo ayudar?"
            )

        return texto.strip()

    except Exception:

        print(
            "ERROR OPENAI:"
        )

        print(
            traceback.format_exc()
        )

        return (
            "Disculpa 🙏 Estoy teniendo un "
            "pequeño problema técnico. "
            "Por favor intenta nuevamente."
        )


# ============================================================
# DETECCIÓN DE AGENDAMIENTO
# ============================================================

def parece_agendamiento(
    texto
):

    texto = texto.lower()

    palabras = [

        "reservar",
        "reserva",
        "reservame",
        "agendar",
        "agenda",
        "agéndame",
        "agendarme",
        "cita",
        "turno",
        "hora",
        "disponibilidad",
        "disponible",
        "quiero cortarme",
        "quiero corte",
        "cortar el pelo",
        "cortarme el pelo"

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

    texto_lower = texto.lower()

    # Orden importante
    if (
        "corte y barba" in texto_lower
        or "corte barba" in texto_lower
    ):

        return "Corte y barba"

    if (
        "perfilado" in texto_lower
        and "barba" in texto_lower
    ):

        return "Perfilado de barba"

    if "barba" in texto_lower:

        return "Barba"

    if (
        "corte" in texto_lower
        or "pelo" in texto_lower
        or "cabello" in texto_lower
    ):

        return "Corte de cabello"

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

    texto_lower = texto.lower().strip()

    # --------------------------------------------------------
    # HOY
    # --------------------------------------------------------

    if texto_lower in (
        "hoy",
        "hoy mismo"
    ):

        return ahora

    # --------------------------------------------------------
    # MAÑANA
    # --------------------------------------------------------

    if (
        "mañana" in texto_lower
        or "manana" in texto_lower
    ):

        return ahora + timedelta(
            days=1
        )

    # --------------------------------------------------------
    # DATEPARSER
    # --------------------------------------------------------

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

        return None


# ============================================================
# DETECTAR HORA
# ============================================================

def detectar_hora(
    texto
):

    texto_lower = texto.lower()

    # 14:30
    match = re.search(
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
        texto_lower
    )

    if match:

        hora = int(
            match.group(1)
        )

        minuto = int(
            match.group(2)
        )

        return hora, minuto

    # 14.30
    match = re.search(
        r"\b([01]?\d|2[0-3])\.([0-5]\d)\b",
        texto_lower
    )

    if match:

        hora = int(
            match.group(1)
        )

        minuto = int(
            match.group(2)
        )

        return hora, minuto

    # 2 pm / 2:30 pm
    match = re.search(
        r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
        texto_lower
    )

    if match:

        hora = int(
            match.group(1)
        )

        minuto = int(
            match.group(2)
            or 0
        )

        periodo = match.group(3)

        if periodo == "pm" and hora < 12:

            hora += 12

        if periodo == "am" and hora == 12:

            hora = 0

        return hora, minuto

    # 2 de la tarde
    match = re.search(
        r"\b(\d{1,2})\s*(?:de la )?(mañana|tarde|noche)\b",
        texto_lower
    )

    if match:

        hora = int(
            match.group(1)
        )

        periodo = match.group(2)

        if periodo in (
            "tarde",
            "noche"
        ) and hora < 12:

            hora += 12

        if periodo == "mañana" and hora == 12:

            hora = 0

        return hora, 0

    return None


# ============================================================
# DETECTAR NOMBRE
# ============================================================

def detectar_nombre(
    texto
):

    texto_lower = texto.lower().strip()

    patrones = [

        r"me llamo\s+([a-záéíóúñ ]+)",

        r"mi nombre es\s+([a-záéíóúñ ]+)",

        r"soy\s+([a-záéíóúñ ]+)",

        r"nombre\s*[:\-]?\s*([a-záéíóúñ ]+)"

    ]

    for patron in patrones:

        match = re.search(
            patron,
            texto_lower
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

            if 1 < len(nombre) < 50:

                return nombre.title()

    return None


# ============================================================
# FORMATEAR FECHA
# ============================================================

DIAS = [

    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo"

]


def fecha_texto(
    fecha
):

    return (
        f"{DIAS[fecha.weekday()]} "
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
# PROCESAR RESERVA
# ============================================================

def procesar_mensaje(
    telefono,
    mensaje
):

    mensaje = mensaje.strip()

    cliente = obtener_cliente(
        telefono
    )

    nombre_actual = None

    if cliente:

        nombre_actual = cliente.get(
            "nombre"
        )

    # ========================================================
    # EXTRAER DATOS DEL MENSAJE
    # ========================================================

    nombre_detectado = detectar_nombre(
        mensaje
    )

    servicio = detectar_servicio(
        mensaje
    )

    fecha = detectar_fecha(
        mensaje
    )

    hora = detectar_hora(
        mensaje
    )

    # ========================================================
    # GUARDAR NOMBRE
    # ========================================================

    if nombre_detectado:

        nombre_actual = nombre_detectado

        guardar_cliente(
            telefono,
            nombre_detectado
        )

    # ========================================================
    # CONVERSACIÓN NORMAL
    # ========================================================

    if not parece_agendamiento(
        mensaje
    ):

        guardar_mensaje(
            telefono,
            "user",
            mensaje
        )

        respuesta = preguntar_gpt(
            telefono,
            mensaje
        )

        guardar_mensaje(
            telefono,
            "assistant",
            respuesta
        )

        return respuesta

    # ========================================================
    # AGENDAMIENTO
    # ========================================================

    # --------------------------------------------------------
    # FECHA
    # --------------------------------------------------------

    if fecha:

        if fecha.date() < datetime.now(
            TZ
        ).date():

            respuesta = (
                "Esa fecha ya pasó 😕 "
                "Dime otra fecha y revisamos."
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
                "Los domingos estoy cerrado 😊 "
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
    # SI HAY FECHA, MOSTRAR DISPONIBILIDAD
    # --------------------------------------------------------

    if fecha and not hora:

        try:

            horarios = buscar_horarios_disponibles(
                fecha.date(),
                cantidad=5
            )

            horarios_texto = formatear_horarios(
                horarios
            )

            contexto = f"""
El cliente quiere agendar.

Fecha:
{fecha_texto(fecha)}

Horarios REALES disponibles:
{horarios_texto}

Servicio detectado:
{servicio or "todavía no indicado"}

Nombre:
{nombre_actual or "todavía no indicado"}

Si faltan datos, pregunta naturalmente.

NO inventes horarios.
"""

            guardar_mensaje(
                telefono,
                "user",
                mensaje
            )

            respuesta = preguntar_gpt(
                telefono,
                mensaje,
                contexto
            )

            guardar_mensaje(
                telefono,
                "assistant",
                respuesta
            )

            return respuesta

        except Exception:

            print(
                "ERROR CONSULTANDO DISPONIBILIDAD:"
            )

            print(
                traceback.format_exc()
            )

            respuesta = (
                "Disculpa 🙏 No pude consultar "
                "la agenda en este momento. "
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
    # FALTAN DATOS
    # --------------------------------------------------------

    faltantes = []

    if not nombre_actual:

        faltantes.append(
            "nombre"
        )

    if not servicio:

        faltantes.append(
            "servicio"
        )

    if not fecha:

        faltantes.append(
            "día"
        )

    if not hora:

        faltantes.append(
            "hora"
        )

    # --------------------------------------------------------
    # PEDIR DATOS FALTANTES
    # --------------------------------------------------------

    if faltantes:

        contexto = f"""
El cliente está intentando reservar una cita.

Datos que ya conocemos:

Nombre:
{nombre_actual or "NO"}

Servicio:
{servicio or "NO"}

Fecha:
{fecha_texto(fecha) if fecha else "NO"}

Hora:
{f"{hora[0]:02d}:{hora[1]:02d}" if hora else "NO"}

Datos que faltan:
{", ".join(faltantes)}

Pregunta de forma natural solamente por
la información que falta.

Precio:
$20.000

Duración:
60 minutos.

Horario:
lunes a sábado de 10:00 a 18:00.
"""

        guardar_mensaje(
            telefono,
            "user",
            mensaje
        )

        respuesta = preguntar_gpt(
            telefono,
            mensaje,
            contexto
        )

        guardar_mensaje(
            telefono,
            "assistant",
            respuesta
        )

        return respuesta

    # ========================================================
    # TENEMOS TODOS LOS DATOS
    # ========================================================

    hora_numero = hora[0]

    minuto_numero = hora[1]

    try:

        inicio = TZ.localize(

            datetime.combine(

                fecha.date(),

                time(
                    hora_numero,
                    minuto_numero
                )

            )

        )

    except Exception:

        respuesta = (
            "No pude interpretar esa hora 😕 "
            "Por favor dime una hora como "
            "14:00 o 14:30."
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
    # VALIDAR HORA
    # --------------------------------------------------------

    if not es_horario_valido(
        inicio
    ):

        respuesta = (
            "Esa hora está fuera de mi horario 😊\n\n"
            "Atiendo de lunes a sábado entre "
            "10:00 y 18:00.\n\n"
            "Dime otra hora y revisamos."
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
    # VALIDAR DISPONIBILIDAD
    # --------------------------------------------------------

    try:

        disponible = esta_disponible(
            inicio
        )

    except Exception:

        print(
            "ERROR CONSULTANDO GOOGLE:"
        )

        print(
            traceback.format_exc()
        )

        respuesta = (
            "Disculpa 🙏 No pude revisar "
            "la agenda en este momento. "
            "Intenta nuevamente."
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

    if not disponible:

        try:

            horarios = buscar_horarios_disponibles(
                fecha.date(),
                cantidad=5
            )

            horarios_texto = formatear_horarios(
                horarios
            )

        except Exception:

            horarios_texto = (
                "No pude consultar otras horas."
            )

        respuesta = (
            f"Justo las {inicio.strftime('%H:%M')} "
            f"no están disponibles 😕\n\n"
            f"Para {fecha_texto(fecha)} tengo estas "
            f"horas disponibles:\n"
            f"{horarios_texto}\n\n"
            f"¿Cuál te acomoda?"
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

    # ========================================================
    # CREAR RESERVA
    # ========================================================

    try:

        evento, meet_url = crear_reserva_google(

            nombre_actual,

            telefono,

            servicio,

            inicio

        )

        guardar_reserva(

            telefono,

            nombre_actual,

            servicio,

            inicio,

            evento,

            meet_url

        )

        guardar_cliente(
            telefono,
            nombre_actual
        )

        # ----------------------------------------------------
        # CONFIRMACIÓN
        # ----------------------------------------------------

        respuesta = (
            "✅ ¡Reserva confirmada!\n\n"
            f"👤 Nombre: {nombre_actual}\n"
            f"✂️ Servicio: {servicio}\n"
            f"📅 Fecha: {fecha_texto(inicio)}\n"
            f"🕐 Hora: {inicio.strftime('%H:%M')}\n"
            f"💰 Precio: ${PRECIO_SERVICIO:,}".replace(
                ",",
                "."
            )
            + "\n\n"
            "¡Te espero! 😊"
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

    except Exception:

        print(
            "ERROR CREANDO RESERVA:"
        )

        print(
            traceback.format_exc()
        )

        respuesta = (
            "Disculpa 🙏 Ocurrió un problema "
            "al intentar confirmar la reserva. "
            "No quiero darte una confirmación falsa. "
            "Intenta nuevamente."
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

    try:

        print("")
        print(
            "=========================================="
        )

        print(
            "MENSAJE WHATSAPP RECIBIDO"
        )

        print(
            "=========================================="
        )

        # ----------------------------------------------------
        # DATOS TWILIO
        # ----------------------------------------------------

        telefono = request.form.get(
            "From",
            ""
        ).strip()

        mensaje = request.form.get(
            "Body",
            ""
        ).strip()

        message_sid = request.form.get(
            "MessageSid",
            ""
        )

        print(
            "FROM:",
            telefono
        )

        print(
            "BODY:",
            mensaje
        )

        print(
            "MESSAGE SID:",
            message_sid
        )

        # ----------------------------------------------------
        # VALIDACIÓN BÁSICA
        # ----------------------------------------------------

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
            "RESPUESTA:",
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
            "TWIML:",
            xml
        )

        return (
            xml,
            200,
            {
                "Content-Type":
                "application/xml; charset=utf-8"
            }
        )

    except Exception:

        print("")
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

        # ----------------------------------------------------
        # AUN ASÍ RESPONDEMOS A TWILIO
        # ----------------------------------------------------

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

@app.route(
    "/health",
    methods=["GET"]
)
def health():

    return jsonify({

        "status": "ok",

        "service":
            "estilista-diego",

        "whatsapp":
            "connected",

        "openai":
            bool(client),

        "database":
            bool(DATABASE_URL),

        "google_calendar":
            bool(
                GOOGLE_REFRESH_TOKEN
                and GOOGLE_CLIENT_ID
                and GOOGLE_CLIENT_SECRET
            )

    })


# ============================================================
# HOME
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
            📱 WhatsApp: Twilio
        </p>

        <p>
            🤖 IA: OpenAI
        </p>

        <p>
            📅 Agenda: Google Calendar
        </p>

        <p>
            🗄️ Base de datos: PostgreSQL
        </p>

        <hr>

        <p>
            Webhook:
            <strong>
                /webhook/whatsapp
            </strong>
        </p>

        <p>
            Health:
            <a href="/health">
                /health
            </a>
        </p>

    </body>

    </html>
    """


# ============================================================
# LOGIN ADMIN
# ============================================================

@app.route(
    "/admin/login",
    methods=["GET", "POST"]
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
                url_for("admin")
            )

        return render_template_string(

            ADMIN_LOGIN_HTML,

            error=(
                "Usuario o contraseña incorrectos"
            )

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
        url_for("admin_login")
    )


# ============================================================
# ADMIN
# ============================================================

@app.route(
    "/admin"
)
def admin():

    if not session.get(
        "admin"
    ):

        return redirect(
            url_for("admin_login")
        )

    conn = get_db()

    cur = conn.cursor(
        cursor_factory=RealDictCursor
    )

    cur.execute("""
        SELECT *
        FROM reservas
        ORDER BY inicio DESC
        LIMIT 100
    """)

    reservas = cur.fetchall()

    cur.close()
    conn.close()

    return render_template_string(

        ADMIN_HTML,

        reservas=reservas

    )


# ============================================================
# ADMIN LOGIN HTML
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

    border-radius: 10px;

    box-shadow:
        0 2px 10px
        rgba(0,0,0,0.1);

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
# ADMIN HTML
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

th, td {

    border: 1px solid #ddd;

    padding: 10px;

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
Meet
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

print("")
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
    "OK" if OPENAI_API_KEY else "FALTA"
)

print(
    "DATABASE_URL:",
    "OK" if DATABASE_URL else "FALTA"
)

print(
    "TWILIO_ACCOUNT_SID:",
    "OK" if TWILIO_ACCOUNT_SID else "FALTA"
)

print(
    "TWILIO_AUTH_TOKEN:",
    "OK" if TWILIO_AUTH_TOKEN else "FALTA"
)

print(
    "TWILIO_WHATSAPP_FROM:",
    TWILIO_WHATSAPP_FROM
)

print(
    "GOOGLE_REFRESH_TOKEN:",
    "OK" if GOOGLE_REFRESH_TOKEN else "FALTA"
)

print(
    "GOOGLE_CLIENT_ID:",
    "OK" if GOOGLE_CLIENT_ID else "FALTA"
)

print(
    "GOOGLE_CLIENT_SECRET:",
    "OK" if GOOGLE_CLIENT_SECRET else "FALTA"
)

print(
    "OPENAI_MODEL:",
    OPENAI_MODEL
)

print(
    "TIMEZONE:",
    str(TZ)
)

print(
    "=========================================="
)


try:

    init_db()

except Exception:

    print(
        "ERROR INICIALIZANDO BASE DE DATOS"
    )

    print(
        traceback.format_exc()
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
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
