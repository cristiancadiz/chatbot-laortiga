import os
import json
import uuid
import traceback
from datetime import datetime, timedelta, time

import pytz
import psycopg2
from psycopg2.extras import RealDictCursor

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
from twilio.request_validator import RequestValidator

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
    os.environ.get("TIMEZONE", "America/Santiago")
)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-5-mini"
)

DATABASE_URL = os.environ.get("DATABASE_URL")

# ------------------------------------------------------------
# TWILIO
# ------------------------------------------------------------

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

TWILIO_WHATSAPP_FROM = os.environ.get(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886"
)

# ------------------------------------------------------------
# GOOGLE CALENDAR
# ------------------------------------------------------------

GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

GOOGLE_CALENDAR_ID = os.environ.get(
    "GOOGLE_CALENDAR_ID",
    "primary"
)

GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI"
)

# ------------------------------------------------------------
# ADMIN
# ------------------------------------------------------------

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
    client = OpenAI(
        api_key=OPENAI_API_KEY
    )


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte de cabello": 20000,
    "corte": 20000,
    "barba": 20000,
    "corte y barba": 20000,
    "perfilado de barba": 20000,
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

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clientes (
            id SERIAL PRIMARY KEY,
            telefono VARCHAR(100) UNIQUE NOT NULL,
            nombre VARCHAR(255),
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            actualizado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mensajes (
            id SERIAL PRIMARY KEY,
            telefono VARCHAR(100) NOT NULL,
            rol VARCHAR(30) NOT NULL,
            mensaje TEXT NOT NULL,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

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


# ============================================================
# CLIENTES
# ============================================================

def guardar_cliente(telefono, nombre=None):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO clientes
        (telefono, nombre)
        VALUES (%s, %s)
        ON CONFLICT (telefono)
        DO UPDATE SET
            nombre = COALESCE(EXCLUDED.nombre, clientes.nombre),
            actualizado_en = CURRENT_TIMESTAMP
    """, (
        telefono,
        nombre
    ))

    conn.commit()

    cur.close()
    conn.close()


def obtener_cliente(telefono):

    conn = get_db()
    cur = conn.cursor(
        cursor_factory=RealDictCursor
    )

    cur.execute("""
        SELECT *
        FROM clientes
        WHERE telefono = %s
    """, (telefono,))

    cliente = cur.fetchone()

    cur.close()
    conn.close()

    return cliente


# ============================================================
# MEMORIA DE CONVERSACIÓN
# ============================================================

def guardar_mensaje(
    telefono,
    rol,
    mensaje
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO mensajes
        (telefono, rol, mensaje)
        VALUES (%s, %s, %s)
    """, (
        telefono,
        rol,
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
        SELECT rol, mensaje
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
        token_uri="https://oauth2.googleapis.com/token",
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

def es_horario_valido(inicio):

    inicio = inicio.astimezone(TZ)

    # Lunes = 0
    # Domingo = 6

    if inicio.weekday() == 6:
        return False

    hora = inicio.time()

    apertura = time(10, 0)
    cierre = time(18, 0)

    if hora < apertura:
        return False

    if hora >= cierre:
        return False

    return True


def redondear_hora(inicio):

    inicio = inicio.astimezone(TZ)

    minuto = inicio.minute

    if minuto < 30:
        minuto = 30
    else:
        inicio = inicio + timedelta(hours=1)
        minuto = 0

    return inicio.replace(
        minute=minuto,
        second=0,
        microsecond=0
    )


# ============================================================
# DISPONIBILIDAD GOOGLE CALENDAR
# ============================================================

def obtener_eventos_dia(fecha):

    service = get_calendar_service()

    inicio = TZ.localize(
        datetime.combine(
            fecha,
            time(0, 0)
        )
    )

    fin = inicio + timedelta(days=1)

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


def esta_disponible(inicio):

    inicio = inicio.astimezone(TZ)

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    if not es_horario_valido(inicio):
        return False

    if fin.time() > time(18, 0):
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

        start_str = (
            start_data.get("dateTime")
        )

        end_str = (
            end_data.get("dateTime")
        )

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

        if inicio < end and fin > start:
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
            time(10, 0)
        )
    )

    cierre = TZ.localize(
        datetime.combine(
            fecha,
            time(18, 0)
        )
    )

    while hora < cierre:

        if (
            hora + timedelta(
                minutes=DURACION_CITA
            )
            <= cierre
        ):

            if esta_disponible(hora):

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
# CREAR EVENTO GOOGLE CALENDAR
# ============================================================

def crear_reserva_google(
    nombre,
    telefono,
    servicio,
    inicio
):

    service = get_calendar_service()

    fin = inicio + timedelta(
        minutes=DURACION_CITA
    )

    titulo = (
        f"Cita - {nombre} - "
        f"{servicio}"
    )

    descripcion = (
        "Reserva realizada mediante "
        "Asistente Virtual de Estilista Diego.\n\n"
        f"Cliente: {nombre}\n"
        f"WhatsApp: {telefono}\n"
        f"Servicio: {servicio}\n"
        f"Precio: ${PRECIO_SERVICIO:,}".replace(",", ".")
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
                "requestId": str(uuid.uuid4()),
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

        if entry.get(
            "entryPointType"
        ) == "video":

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
        (%s,%s,%s,%s,%s,%s,%s,%s)
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

Tu objetivo principal es atender naturalmente a los clientes
por WhatsApp y ayudarlos a agendar una hora con Diego.

Hablas español natural de Chile, de manera cercana, amable,
simple y humana.

NO debes sonar como un robot.

Puedes conversar libremente sobre cualquier tema relacionado
con la atención del cliente.

Cuando el cliente quiera reservar una hora, debes obtener:

1. Nombre del cliente.
2. Servicio.
3. Día.
4. Hora.

Las reservas duran 1 hora.

Horario de atención:

Lunes a sábado:
10:00 a 18:00.

Domingo:
cerrado.

Todos los servicios disponibles tienen un valor de $20.000.

No inventes disponibilidad.

La disponibilidad real será entregada por el sistema.

Si el cliente pregunta por disponibilidad, utiliza la
información proporcionada por el sistema.

La reserva se realiza SOLAMENTE en el calendario de Diego.

No necesitas crear ni modificar un calendario del cliente.

Cuando una reserva sea creada correctamente, informa:

- nombre
- servicio
- fecha
- hora
- precio
- confirmación de que quedó agendada

Si existe un enlace de Google Meet, también puedes entregarlo.

Mantén el contexto de la conversación.

Nunca pidas nuevamente información que el cliente ya entregó.

Si el cliente simplemente saluda o conversa, conversa
normalmente.

No fuerces el agendamiento.

No digas que eres una IA salvo que el cliente lo pregunte.

IMPORTANTE:

Cuando necesites ejecutar una reserva o consultar
disponibilidad, el sistema externo proporcionará los datos.
No inventes acciones realizadas.
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

        rol = item["rol"]

        if rol not in (
            "user",
            "assistant"
        ):
            continue

        mensajes.append({
            "role": rol,
            "content": item["mensaje"]
        })

    mensajes.append({
        "role": "user",
        "content": mensaje
    })

    respuesta = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=mensajes,
        temperature=0.7
    )

    return respuesta.choices[0].message.content.strip()


# ============================================================
# DETECCIÓN SIMPLE DE INTENCIÓN
# ============================================================

def parece_agendamiento(texto):

    texto = texto.lower()

    palabras = [
        "reservar",
        "reserva",
        "agendar",
        "agenda",
        "hora",
        "cita",
        "turno",
        "disponibilidad",
        "disponible"
    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


# ============================================================
# DETECCIÓN DE FECHA
# ============================================================

import dateparser


def detectar_fecha(texto):

    ahora = datetime.now(TZ)

    fecha = dateparser.parse(
        texto,
        languages=["es"],
        settings={
            "TIMEZONE": "America/Santiago",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": ahora
        }
    )

    if not fecha:
        return None

    return fecha.astimezone(TZ)


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
            hora.strftime("%H:%M")
        )

    return ", ".join(textos)


# ============================================================
# PROCESAR MENSAJE
# ============================================================

def procesar_mensaje(
    telefono,
    mensaje
):

    cliente = obtener_cliente(
        telefono
    )

    nombre_actual = None

    if cliente:
        nombre_actual = cliente.get(
            "nombre"
        )

    # --------------------------------------------------------
    # SALUDOS / CONVERSACIÓN NORMAL
    # --------------------------------------------------------

    # Primero dejamos que GPT converse.
    # No forzamos reserva.
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
    # INTENTO DE DETECTAR FECHA
    # --------------------------------------------------------

    fecha = detectar_fecha(
        mensaje
    )

    if fecha:

        if fecha.weekday() == 6:

            respuesta = (
                "Los domingos no atiendo 😊 "
                "Si quieres, podemos buscar una "
                "hora de lunes a sábado."
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
El cliente está interesado en agendar.

Fecha detectada:
{fecha.strftime("%A %d/%m/%Y")}

Horarios reales disponibles:
{horarios_texto}

No inventes otros horarios.
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

    # --------------------------------------------------------
    # CONVERSACIÓN DE RESERVA
    # --------------------------------------------------------

    contexto = """
El cliente está intentando agendar una cita.

Recuerda:
- Horario lunes a sábado 10:00-18:00.
- Duración: 1 hora.
- Precio: $20.000.
- La reserva se realiza solamente en el calendario
  de Estilista Diego.

Si todavía falta información, pregunta de forma natural
por el dato que falta.
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
# WEBHOOK WHATSAPP TWILIO
# ============================================================

@app.route(
    "/webhook/whatsapp",
    methods=["POST"]
)
def whatsapp_webhook():

    try:

        telefono = request.form.get(
            "From",
            ""
        )

        mensaje = request.form.get(
            "Body",
            ""
        ).strip()

        message_sid = request.form.get(
            "MessageSid"
        )

        if not telefono:
            return (
                "Missing From",
                400
            )

        if not mensaje:
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

        # ----------------------------------------------------
        # RESPUESTA TWILIO
        # ----------------------------------------------------

        twiml = MessagingResponse()

        twiml.message(
            respuesta
        )

        return (
            str(twiml),
            200,
            {
                "Content-Type":
                "application/xml"
            }
        )

    except Exception as e:

        print(
            "ERROR WEBHOOK WHATSAPP:"
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
                "application/xml"
            }
        )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/")
def home():

    return """
    <html>
    <head>
        <title>Asistente Virtual Estilista Diego</title>
        <meta charset="UTF-8">
    </head>

    <body style="
        font-family: Arial;
        max-width: 800px;
        margin: 50px auto;
    ">

        <h1>
            💈 Asistente Virtual de Estilista Diego
        </h1>

        <p>
            Sistema funcionando correctamente.
        </p>

        <p>
            WhatsApp: Twilio
        </p>

        <p>
            Agenda: Google Calendar
        </p>

        <p>
            IA: OpenAI
        </p>

    </body>
    </html>
    """


@app.route("/health")
def health():

    return jsonify({
        "status": "ok",
        "service": "estilista-diego"
    })


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
            "usuario"
        )

        password = request.form.get(
            "password"
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
            error="Usuario o contraseña incorrectos"
        )

    return render_template_string(
        ADMIN_LOGIN_HTML
    )


@app.route("/admin/logout")
def admin_logout():

    session.clear()

    return redirect(
        url_for("admin_login")
    )


# ============================================================
# PANEL ADMIN
# ============================================================

@app.route("/admin")
def admin():

    if not session.get("admin"):
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
# HTML ADMIN
# ============================================================

ADMIN_LOGIN_HTML = """

<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<title>Login</title>

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
}

.error {
    color: red;
}

</style>

</head>

<body>

<div class="box">

<h2>💈 Administración</h2>

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


ADMIN_HTML = """

<!DOCTYPE html>

<html>

<head>

<meta charset="UTF-8">

<title>Reservas</title>

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

<th>Fecha</th>
<th>Cliente</th>
<th>WhatsApp</th>
<th>Servicio</th>
<th>Precio</th>
<th>Meet</th>

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
${{ "{:,}".format(reserva.precio).replace(",", ".") }}
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

try:

    init_db()

    print(
        "BASE DE DATOS INICIALIZADA"
    )

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
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
