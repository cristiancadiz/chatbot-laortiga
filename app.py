import os
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

else:
    print("OPENAI_API_KEY: FALTA")


# ============================================================
# POSTGRESQL
# ============================================================

DATABASE_URL = os.environ.get(
    "DATABASE_URL"
)


def get_db():

    if not DATABASE_URL:
        raise Exception(
            "DATABASE_URL no está configurada"
        )

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
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

def init_db():

    print("INICIANDO BASE DE DATOS...")

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
    # ESTA TABLA USA "role", NO "rol".
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
        (%s, %s)

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
    """, (
        telefono,
    ))

    cliente = cur.fetchone()

    cur.close()
    conn.close()

    return cliente


# ============================================================
# MEMORIA DE CONVERSACIÓN
# ============================================================

def guardar_mensaje(
    telefono,
    role,
    mensaje
):

    conn = get_db()
    cur = conn.cursor()

    # IMPORTANTE:
    # La columna correcta es "role".

    cur.execute("""
        INSERT INTO mensajes
        (
            telefono,
            role,
            mensaje
        )
        VALUES
        (%s, %s, %s)
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

    # IMPORTANTE:
    # La columna correcta es "role".

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
# REDONDEAR HORA
# ============================================================

def redondear_hora(
    inicio
):

    inicio = inicio.astimezone(TZ)

    minuto = inicio.minute

    if minuto < 30:

        minuto = 30

    else:

        inicio = inicio + timedelta(
            hours=1
        )

        minuto = 0

    return inicio.replace(
        minute=minuto,
        second=0,
        microsecond=0
    )


# ============================================================
# EVENTOS DE GOOGLE
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

        fin = hora + timedelta(
            minutes=DURACION_CITA
        )

        if fin <= cierre:

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
                    "ERROR CONSULTANDO CALENDARIO"
                )

                print(
                    traceback.format_exc()
                )

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
        }
    }

    creado = service.events().insert(
        calendarId=GOOGLE_CALENDAR_ID,
        body=evento,
        sendUpdates="all"
    ).execute()

    return creado, None


# ============================================================
# GUARDAR RESERVA
# ============================================================

def guardar_reserva(
    telefono,
    nombre,
    servicio,
    inicio,
    evento,
    meet_url=None
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

Hablas en español natural de Chile.

Tu personalidad es:

- cercana
- amable
- simple
- humana
- rápida
- cordial

NO debes sonar como robot.

Puedes conversar normalmente.

No debes forzar al cliente a reservar.

Si el cliente solamente dice:

"hola"

responde naturalmente.

Por ejemplo:

"¡Hola! 😊 ¿Cómo estás? ¿En qué te puedo ayudar?"

Si el cliente pregunta por los servicios,
puedes informar:

Corte de cabello: $20.000
Barba: $20.000
Corte y barba: $20.000
Perfilado de barba: $20.000

Horario:

Lunes a sábado:
10:00 a 18:00

Domingo:
cerrado.

Las citas duran 1 hora.

Cuando el cliente quiera reservar,
necesitamos obtener:

1. Nombre
2. Servicio
3. Día
4. Hora

IMPORTANTE:

Nunca inventes una disponibilidad.

La disponibilidad real del calendario será
proporcionada por el sistema.

Si el sistema entrega horarios disponibles,
solamente puedes ofrecer esos horarios.

Si falta información para agendar,
pregunta de manera natural por el dato que falta.

No vuelvas a preguntar un dato que el cliente
ya haya entregado.

Si el cliente dice que quiere agendar,
no le preguntes todo de golpe necesariamente.

Lleva la conversación naturalmente.

Ejemplo:

Cliente:
Quiero agendar

Asistente:
"¡Claro! 😊 ¿Qué servicio te gustaría realizarte?"

Cliente:
Corte

Asistente:
"Perfecto. ¿Qué día te acomoda?"

Cliente:
Mañana

Asistente:
"Déjame revisar las horas disponibles 😊"

Cuando el sistema proporcione disponibilidad,
utilízala.

No digas que una cita está confirmada hasta que
el sistema realmente la haya creado.

No digas que eres una IA salvo que el cliente
lo pregunte.

El calendario que se utiliza es el calendario
de Estilista Diego.
"""


# ============================================================
# PREGUNTAR GPT
# ============================================================

def preguntar_gpt(
    telefono,
    mensaje,
    contexto_extra=""
):

    if not client:

        return (
            "Hola 😊 En este momento estoy "
            "teniendo un problema técnico "
            "con el asistente. "
            "Por favor intenta nuevamente "
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

        contenido = item.get(
            "mensaje"
        )

        if role not in (
            "user",
            "assistant"
        ):
            continue

        mensajes.append({
            "role": role,
            "content": contenido
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

    except Exception:

        print(
            "ERROR OPENAI:"
        )

        print(
            traceback.format_exc()
        )

        return (
            "Disculpa 🙏 Estoy teniendo "
            "un problema temporal con "
            "el asistente. "
            "Intenta nuevamente."
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
        "hora",
        "cita",
        "turno",
        "disponibilidad",
        "disponible",
        "quiero cortarme",
        "quiero corte",
        "quiero barba",
        "cortar el pelo"
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

        return None


# ============================================================
# DETECTAR HORA
# ============================================================

def detectar_hora(
    texto
):

    texto = texto.lower().strip()

    # Intentamos encontrar formatos comunes:
    #
    # 10
    # 10:30
    # 10 am
    # 10:30 am
    # 5 pm
    # 17:00

    import re

    patrones = [
        r"\b([01]?\d|2[0-3]):([0-5]\d)\b",
        r"\b([1-9]|1[0-2])\s*(am|pm)\b",
        r"\b([1-9]|1[0-2])\b"
    ]

    for patron in patrones:

        match = re.search(
            patron,
            texto
        )

        if not match:
            continue

        try:

            if len(match.groups()) == 2:

                primer = match.group(1)
                segundo = match.group(2)

                # HH:MM

                if segundo.isdigit():

                    hora = int(primer)
                    minuto = int(segundo)

                    if 0 <= hora <= 23:

                        return time(
                            hora,
                            minuto
                        )

                # AM / PM

                periodo = segundo

                hora = int(primer)

                if periodo == "pm" and hora != 12:
                    hora += 12

                if periodo == "am" and hora == 12:
                    hora = 0

                return time(
                    hora,
                    0
                )

            else:

                hora = int(
                    match.group(1)
                )

                # Para una hora sola,
                # asumimos horario de atención.

                if 10 <= hora <= 18:

                    return time(
                        hora,
                        0
                    )

        except Exception:

            continue

    return None


# ============================================================
# DETECTAR SERVICIO
# ============================================================

def detectar_servicio(
    texto
):

    texto = texto.lower()

    if (
        "corte y barba" in texto
        or "corte barba" in texto
    ):

        return "Corte y barba"

    if (
        "perfilado" in texto
        and "barba" in texto
    ):

        return "Perfilado de barba"

    if "barba" in texto:

        return "Barba"

    if (
        "corte" in texto
        or "pelo" in texto
        or "cabello" in texto
    ):

        return "Corte de cabello"

    return None


# ============================================================
# DETECTAR NOMBRE
# ============================================================

def detectar_nombre(
    texto
):

    import re

    patrones = [
        r"(?:me llamo|soy|mi nombre es)\s+([A-Za-zÁÉÍÓÚáéíóúÑñ ]{2,50})",
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

            return nombre.title()

    return None


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

    return ", ".join(
        textos
    )


# ============================================================
# OBTENER PRÓXIMOS HORARIOS
# ============================================================

def buscar_proximas_disponibilidades(
    dias=7
):

    resultados = []

    hoy = datetime.now(
        TZ
    ).date()

    for i in range(
        dias
    ):

        fecha = hoy + timedelta(
            days=i
        )

        if fecha.weekday() == 6:
            continue

        horarios = buscar_horarios_disponibles(
            fecha,
            cantidad=3
        )

        if horarios:

            resultados.append(
                (
                    fecha,
                    horarios
                )
            )

        if len(resultados) >= 3:
            break

    return resultados


# ============================================================
# GUARDAR DATOS DE CLIENTE DETECTADOS
# ============================================================

def actualizar_nombre_si_corresponde(
    telefono,
    mensaje
):

    nombre = detectar_nombre(
        mensaje
    )

    if nombre:

        guardar_cliente(
            telefono,
            nombre
        )

        return nombre

    cliente = obtener_cliente(
        telefono
    )

    if cliente:

        return cliente.get(
            "nombre"
        )

    return None


# ============================================================
# PROCESAR MENSAJE
# ============================================================

def procesar_mensaje(
    telefono,
    mensaje
):

    # --------------------------------------------------------
    # Cliente
    # --------------------------------------------------------

    cliente = obtener_cliente(
        telefono
    )

    nombre_actual = None

    if cliente:

        nombre_actual = cliente.get(
            "nombre"
        )

    # --------------------------------------------------------
    # Detectar nombre
    # --------------------------------------------------------

    nombre_detectado = detectar_nombre(
        mensaje
    )

    if nombre_detectado:

        nombre_actual = nombre_detectado

        guardar_cliente(
            telefono,
            nombre_detectado
        )

    # --------------------------------------------------------
    # Detectar servicio
    # --------------------------------------------------------

    servicio_detectado = detectar_servicio(
        mensaje
    )

    # --------------------------------------------------------
    # Detectar fecha
    # --------------------------------------------------------

    fecha = detectar_fecha(
        mensaje
    )

    # --------------------------------------------------------
    # Detectar hora
    # --------------------------------------------------------

    hora = detectar_hora(
        mensaje
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
    # DOMINGO
    # --------------------------------------------------------

    if fecha:

        if fecha.weekday() == 6:

            respuesta = (
                "Los domingos no atiendo 😊 "
                "Si quieres, podemos buscar "
                "una hora de lunes a sábado."
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
    # FECHA PERO SIN HORA
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

Fecha detectada:
{fecha.strftime("%A %d/%m/%Y")}

Horarios REALES disponibles:
{horarios_texto}

No inventes otros horarios.

Si hay horarios disponibles,
muéstralos al cliente de forma clara.

El servicio detectado es:
{servicio_detectado or "todavía no indicado"}

El nombre del cliente es:
{nombre_actual or "todavía no indicado"}
"""

            respuesta = preguntar_gpt(
                telefono,
                mensaje,
                contexto
            )

        except Exception:

            print(
                "ERROR BUSCANDO DISPONIBILIDAD:"
            )

            print(
                traceback.format_exc()
            )

            respuesta = (
                "Estoy teniendo problemas "
                "para revisar el calendario "
                "en este momento 😕. "
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

    # --------------------------------------------------------
    # FECHA + HORA
    # --------------------------------------------------------

    if fecha and hora:

        inicio = TZ.localize(
            datetime.combine(
                fecha.date(),
                hora
            )
        )

        # ----------------------------------------------------
        # Validar horario
        # ----------------------------------------------------

        if not es_horario_valido(
            inicio
        ):

            respuesta = (
                "Esa hora está fuera de mi "
                "horario de atención 😊\n\n"
                "Atiendo de lunes a sábado, "
                "de 10:00 a 18:00."
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

        # ----------------------------------------------------
        # Validar disponibilidad
        # ----------------------------------------------------

        try:

            disponible = esta_disponible(
                inicio
            )

        except Exception:

            print(
                "ERROR CONSULTANDO CALENDARIO:"
            )

            print(
                traceback.format_exc()
            )

            respuesta = (
                "No pude revisar el calendario "
                "en este momento 😕. "
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

            horarios = buscar_horarios_disponibles(
                fecha.date(),
                cantidad=5
            )

            horarios_texto = formatear_horarios(
                horarios
            )

            respuesta = (
                f"La hora de las "
                f"{inicio.strftime('%H:%M')} "
                f"no está disponible 😕.\n\n"
                f"Para ese día tengo disponible: "
                f"{horarios_texto}"
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

        # ----------------------------------------------------
        # Falta nombre
        # ----------------------------------------------------

        if not nombre_actual:

            contexto = """
El cliente ya indicó un día y una hora
que están disponibles.

Antes de confirmar la reserva necesitamos
el nombre del cliente.

Pregúntale solamente su nombre,
de forma natural y breve.

No vuelvas a preguntar el día ni la hora.
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

        # ----------------------------------------------------
        # Falta servicio
        # ----------------------------------------------------

        if not servicio_detectado:

            contexto = """
El cliente ya indicó una fecha y hora
disponibles y conocemos su nombre.

Todavía falta saber qué servicio quiere.

Los servicios disponibles son:

- Corte de cabello
- Barba
- Corte y barba
- Perfilado de barba

Todos cuestan $20.000.

Pregunta solamente qué servicio desea.
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

        # ----------------------------------------------------
        # CREAR RESERVA
        # ----------------------------------------------------

        try:

            # Segunda comprobación antes de crear
            if not esta_disponible(
                inicio
            ):

                respuesta = (
                    "Parece que esa hora acaba "
                    "de ser ocupada 😕.\n\n"
                    "Déjame buscarte otras horas."
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

            evento, meet_url = crear_reserva_google(
                nombre_actual,
                telefono,
                servicio_detectado,
                inicio
            )

            guardar_reserva(
                telefono,
                nombre_actual,
                servicio_detectado,
                inicio,
                evento,
                meet_url
            )

            fecha_texto = inicio.strftime(
                "%d/%m/%Y"
            )

            hora_texto = inicio.strftime(
                "%H:%M"
            )

            respuesta = (
                "¡Listo! 🎉 Tu cita quedó "
                "agendada correctamente.\n\n"
                f"👤 Nombre: {nombre_actual}\n"
                f"✂️ Servicio: {servicio_detectado}\n"
                f"📅 Fecha: {fecha_texto}\n"
                f"🕐 Hora: {hora_texto}\n"
                f"💰 Valor: $20.000\n\n"
                "¡Te esperamos! 😊"
            )

            if meet_url:

                respuesta += (
                    f"\n\n🔗 Google Meet:\n"
                    f"{meet_url}"
                )

        except Exception:

            print(
                "ERROR CREANDO RESERVA:"
            )

            print(
                traceback.format_exc()
            )

            respuesta = (
                "No pude completar la reserva "
                "en este momento 😕.\n\n"
                "Por favor intenta nuevamente."
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
    # AGENDAMIENTO SIN FECHA
    # --------------------------------------------------------

    contexto = f"""
El cliente quiere agendar una cita.

Nombre conocido:
{nombre_actual or "no indicado"}

Servicio detectado:
{servicio_detectado or "no indicado"}

Fecha:
no indicada

Hora:
no indicada

Horario de atención:
lunes a sábado de 10:00 a 18:00.

Precio:
$20.000.

Si falta información,
continúa la conversación naturalmente.

No preguntes nuevamente información
que ya conozcamos.

Si el servicio ya está indicado,
pregunta por el día.

Si el día ya estuviera indicado,
pregunta por la hora.
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
# VALIDACIÓN OPCIONAL DE TWILIO
# ============================================================

def validar_twilio_request():

    # Durante las pruebas del Sandbox no bloqueamos
    # el webhook si no está configurado el Auth Token.

    if not TWILIO_AUTH_TOKEN:

        print(
            "TWILIO_AUTH_TOKEN: FALTA "
            "(validación omitida)"
        )

        return True

    try:

        validator = RequestValidator(
            TWILIO_AUTH_TOKEN
        )

        signature = request.headers.get(
            "X-Twilio-Signature",
            ""
        )

        url = request.url

        params = request.form.to_dict()

        return validator.validate(
            url,
            params,
            signature
        )

    except Exception:

        print(
            "ERROR VALIDANDO TWILIO:"
        )

        print(
            traceback.format_exc()
        )

        return False


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
            "WHATSAPP WEBHOOK RECIBIDO"
        )

        print(
            "=========================================="
        )

        print(
            "FORM:",
            request.form.to_dict()
        )

        # ----------------------------------------------------
        # Validación Twilio
        # ----------------------------------------------------

        if not validar_twilio_request():

            print(
                "TWILIO REQUEST INVALIDO"
            )

            return (
                "Unauthorized",
                403
            )

        # ----------------------------------------------------
        # Datos WhatsApp
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
            "TELEFONO:",
            telefono
        )

        print(
            "MENSAJE:",
            mensaje
        )

        print(
            "MESSAGE SID:",
            message_sid
        )

        # ----------------------------------------------------
        # Validar
        # ----------------------------------------------------

        if not telefono:

            print(
                "FALTA From"
            )

            return (
                "Missing From",
                400
            )

        if not mensaje:

            print(
                "FALTA Body"
            )

            return (
                "Missing Body",
                400
            )

        # ----------------------------------------------------
        # Guardar cliente
        # ----------------------------------------------------

        guardar_cliente(
            telefono
        )

        # ----------------------------------------------------
        # Procesar
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
        # TwiML
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

        print(
            "=========================================="
        )

        print(
            "FIN WEBHOOK"
        )

        print(
            "=========================================="
        )

        print("")

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
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "ERROR WEBHOOK WHATSAPP"
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            traceback.format_exc()
        )

        print("")

        twiml = MessagingResponse()

        twiml.message(
            "Disculpa 🙏 Estoy teniendo "
            "un pequeño problema técnico. "
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
    "/health"
)
def health():

    return jsonify({

        "status": "ok",

        "service":
            "estilista-diego",

        "openai":
            bool(OPENAI_API_KEY),

        "database":
            bool(DATABASE_URL),

        "twilio_account_sid":
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
            bool(GOOGLE_CLIENT_SECRET),

        "timezone":
            str(TZ)

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

        <style>

            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
            }

            .ok {
                color: green;
            }

            .box {
                padding: 20px;
                border: 1px solid #ddd;
                border-radius: 10px;
                margin-top: 20px;
            }

        </style>

    </head>

    <body>

        <h1>
            💈 Asistente Virtual de Estilista Diego
        </h1>

        <div class="box">

            <p class="ok">
                ✅ Sistema funcionando
            </p>

            <p>
                WhatsApp: Twilio
            </p>

            <p>
                IA: OpenAI
            </p>

            <p>
                Agenda: Google Calendar
            </p>

            <p>
                Base de datos: PostgreSQL
            </p>

        </div>

        <div class="box">

            <a href="/health">
                Ver estado del sistema
            </a>

        </div>

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
    box-shadow:
        0 2px 10px rgba(0,0,0,.1);
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
# MOSTRAR CONFIGURACIÓN
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
        "INICIANDO FLASK EN PUERTO",
        port
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
