import os
import uuid
import traceback
from datetime import datetime, timedelta, time

import pytz
import psycopg2
import dateparser

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
# OPENAI
# ============================================================

OPENAI_API_KEY = os.environ.get(
    "OPENAI_API_KEY"
)

OPENAI_MODEL = os.environ.get(
    "OPENAI_MODEL",
    "gpt-5-mini"
)

client = None

if OPENAI_API_KEY:
    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY
        )

        print("OPENAI: cliente inicializado")

    except Exception:
        print("ERROR INICIALIZANDO OPENAI")
        print(traceback.format_exc())


# ============================================================
# DATABASE
# ============================================================

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

GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI"
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

    print("INICIANDO BASE DE DATOS...")

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

    print("BASE DE DATOS: OK")


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
            actualizado_en = CURRENT_TIMESTAMP
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
    """, (
        telefono,
    ))

    cliente = cur.fetchone()

    cur.close()
    conn.close()

    return cliente


# ============================================================
# MEMORIA
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
        (
            telefono,
            rol,
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
        SELECT
            rol,
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

    print("GOOGLE CALENDAR: conectando...")

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

    print("GOOGLE CALENDAR: OK")

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

    inicio = inicio.astimezone(TZ)

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
            and
            fin > start
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

        fin_cita = (
            hora +
            timedelta(
                minutes=DURACION_CITA
            )
        )

        if fin_cita <= cierre:

            try:

                if esta_disponible(
                    hora
                ):

                    resultados.append(
                        hora
                    )

                    if len(resultados) >= cantidad:

                        break

            except Exception:

                print(
                    "ERROR CONSULTANDO DISPONIBILIDAD"
                )

                print(
                    traceback.format_exc()
                )

                raise

        hora += timedelta(
            minutes=30
        )

    return resultados


# ============================================================
# CREAR EVENTO
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
# OPENAI
# ============================================================

SYSTEM_PROMPT = """
Eres el Asistente Virtual de Estilista Diego.

Atiendes clientes por WhatsApp.

Hablas español natural de Chile.
Eres cercano, amable y simple.

No suenas como robot.

Tu objetivo principal es ayudar a los clientes
a agendar una hora con Diego.

HORARIO:

Lunes a sábado:
10:00 a 18:00.

Domingo:
cerrado.

DURACIÓN:

Cada cita dura 1 hora.

PRECIO:

Todos los servicios disponibles cuestan $20.000.

SERVICIOS:

- Corte de cabello
- Corte
- Barba
- Corte y barba
- Perfilado de barba

Puedes conversar normalmente.

Si el cliente solamente dice:

"Hola"

debes responder naturalmente.

NO fuerces al cliente a agendar.

Cuando el cliente quiera agendar necesitas:

1. Nombre
2. Servicio
3. Día
4. Hora

Si falta información, pregunta solamente lo necesario.

Nunca pidas nuevamente un dato que ya esté en la conversación.

Si el sistema entrega horarios disponibles,
utiliza solamente esos horarios.

NUNCA inventes disponibilidad.

La agenda utilizada es solamente la agenda de Diego.

No debes decir que modificaste el calendario del cliente.

Si una reserva fue creada correctamente,
debes informar:

- nombre
- servicio
- fecha
- hora
- precio
- confirmación

Si existe Google Meet, puedes entregarlo.

No digas que eres una IA salvo que el cliente lo pregunte.

IMPORTANTE:

No afirmes que una reserva fue creada
si el sistema no confirma que fue creada.
"""


def preguntar_gpt(
    telefono,
    mensaje,
    contexto_extra=""
):

    print("OPENAI: iniciando solicitud")

    if not client:

        print(
            "OPENAI ERROR: cliente no inicializado"
        )

        return (
            "Hola 😊 En este momento estoy teniendo "
            "un problema técnico con el asistente. "
            "Por favor intenta nuevamente."
        )

    historial = obtener_historial(
        telefono,
        limite=20
    )

    contenido = []

    contenido.append(
        SYSTEM_PROMPT
    )

    if contexto_extra:

        contenido.append(
            "\nINFORMACIÓN DEL SISTEMA:\n"
            + contexto_extra
        )

    contenido.append(
        "\nCONVERSACIÓN:\n"
    )

    for item in historial:

        rol = item["rol"]

        mensaje_historial = item[
            "mensaje"
        ]

        if rol == "user":

            contenido.append(
                f"Cliente: {mensaje_historial}"
            )

        elif rol == "assistant":

            contenido.append(
                f"Asistente: {mensaje_historial}"
            )

    contenido.append(
        f"\nCliente: {mensaje}"
    )

    prompt_final = "\n".join(
        contenido
    )

    try:

        response = client.responses.create(
            model=OPENAI_MODEL,
            input=prompt_final
        )

        respuesta = response.output_text.strip()

        print(
            "OPENAI: respuesta recibida"
        )

        print(
            respuesta
        )

        return respuesta

    except Exception:

        print(
            "ERROR OPENAI"
        )

        print(
            traceback.format_exc()
        )

        raise


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

        "agendar",
        "agenda",

        "hora",

        "cita",

        "turno",

        "disponibilidad",

        "disponible",

        "quiero cortarme",

        "quiero cortar",

        "cortarme el pelo",

        "cortarme el cabello"

    ]

    return any(
        palabra in texto
        for palabra in palabras
    )


# ============================================================
# DETECTAR FECHA
# ============================================================

def detectar_fecha(
    texto
):

    ahora = datetime.now(TZ)

    fecha = dateparser.parse(
        texto,
        languages=[
            "es"
        ],
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

    print("")
    print(
        "================================"
    )
    print(
        "PROCESANDO MENSAJE"
    )
    print(
        "================================"
    )

    print(
        "TELÉFONO:",
        telefono
    )

    print(
        "MENSAJE:",
        mensaje
    )

    cliente = obtener_cliente(
        telefono
    )

    if cliente:

        print(
            "CLIENTE EXISTENTE:",
            cliente.get("nombre")
        )

    else:

        print(
            "CLIENTE NUEVO"
        )

    # ========================================================
    # CONVERSACIÓN NORMAL
    # ========================================================

    if not parece_agendamiento(
        mensaje
    ):

        print(
            "INTENCIÓN: conversación normal"
        )

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

    # ========================================================
    # AGENDAMIENTO
    # ========================================================

    print(
        "INTENCIÓN: AGENDAMIENTO"
    )

    fecha = detectar_fecha(
        mensaje
    )

    print(
        "FECHA DETECTADA:",
        fecha
    )

    # ========================================================
    # SI HAY FECHA
    # ========================================================

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

        print(
            "CONSULTANDO DISPONIBILIDAD..."
        )

        horarios = buscar_horarios_disponibles(
            fecha.date(),
            cantidad=5
        )

        horarios_texto = formatear_horarios(
            horarios
        )

        print(
            "HORARIOS:",
            horarios_texto
        )

        contexto = f"""
El cliente está interesado en agendar.

Fecha detectada:
{fecha.strftime("%A %d/%m/%Y")}

Horarios reales disponibles:
{horarios_texto}

IMPORTANTE:
Solamente puedes mencionar los horarios
que aparecen arriba.
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

    # ========================================================
    # AGENDAMIENTO SIN FECHA
    # ========================================================

    contexto = """
El cliente quiere agendar una cita.

Todavía no se ha detectado una fecha concreta.

Pregunta naturalmente qué día le acomoda.

Recuerda:

Lunes a sábado:
10:00 a 18:00.

Domingo:
cerrado.

Duración:
1 hora.

Precio:
$20.000.
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
    methods=[
        "POST"
    ]
)
def whatsapp_webhook():

    print("")
    print(
        "########################################"
    )

    print(
        "WHATSAPP WEBHOOK RECIBIDO"
    )

    print(
        "########################################"
    )

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

        # ====================================================
        # VALIDACIÓN
        # ====================================================

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

        # ====================================================
        # GUARDAR CLIENTE
        # ====================================================

        print(
            "PASO 1: guardar cliente"
        )

        guardar_cliente(
            telefono
        )

        print(
            "PASO 1 OK"
        )

        # ====================================================
        # PROCESAR
        # ====================================================

        print(
            "PASO 2: procesar mensaje"
        )

        respuesta = procesar_mensaje(
            telefono,
            mensaje
        )

        print(
            "PASO 2 OK"
        )

        print(
            "RESPUESTA:",
            respuesta
        )

        # ====================================================
        # TWILIO
        # ====================================================

        print(
            "PASO 3: crear TwiML"
        )

        twiml = MessagingResponse()

        twiml.message(
            respuesta
        )

        xml = str(
            twiml
        )

        print(
            "PASO 3 OK"
        )

        print(
            "ENVIANDO RESPUESTA A TWILIO"
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

        print("")
        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "ERROR REAL DEL WEBHOOK"
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "ERROR:",
            str(e)
        )

        print(
            traceback.format_exc()
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
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
        margin: 50px auto;
    ">

        <h1>
            💈 Asistente Virtual
            de Estilista Diego
        </h1>

        <p>
            Sistema funcionando correctamente.
        </p>

        <p>
            WhatsApp: Twilio Sandbox
        </p>

        <p>
            Agenda: Google Calendar
        </p>

        <p>
            IA: OpenAI
        </p>

        <hr>

        <p>
            Webhook:
        </p>

        <code>
            /webhook/whatsapp
        </code>

    </body>

    </html>
    """


# ============================================================
# HEALTH
# ============================================================

@app.route(
    "/health"
)
def health():

    return jsonify({
        "status": "ok",
        "service": "estilista-diego",
        "whatsapp_webhook": "/webhook/whatsapp"
    })


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
            "usuario"
        )

        password = request.form.get(
            "password"
        )

        if (
            usuario == ADMIN_USER
            and
            password == ADMIN_PASSWORD
        ):

            session["admin"] = True

            return redirect(
                url_for(
                    "admin"
                )
            )

        return render_template_string(
            ADMIN_LOGIN_HTML,
            error=(
                "Usuario o contraseña incorrectos"
            )
        )

    return render_template_string(
        ADMIN_LOGIN_HTML,
        error=None
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
            url_for(
                "admin_login"
            )
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
Reservas
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
    ).replace(",", ".")
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
# INFORMACIÓN DE CONFIGURACIÓN
# ============================================================

print("")
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
        f"INICIANDO FLASK EN PUERTO {port}"
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
