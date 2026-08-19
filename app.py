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
from twilio.rest import Client as TwilioClient

from datetime import timedelta, datetime
from dotenv import load_dotenv

from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from werkzeug.middleware.proxy_fix import ProxyFix

APP_VERSION = "2026-08-19-FINAL-DIEGO-V17-SERVICIOS-AGENDA-TRANSFERENCIA"


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

    "corte_hombre": {
        "numero": 1,
        "nombre": "Corte de cabello hombre",
        "duracion": 60,
        "precio": 17000,
        "precio_texto": "$17.000",
        "detalle": "Incluye perfilado de cejas, lavado de cabello y aplicación de producto.",
    },

    "perfilado_barba": {
        "numero": 2,
        "nombre": "Perfilado de barba",
        "duracion": 60,
        "precio": 10000,
        "precio_texto": "$10.000",
    },

    "base_rizos": {
        "numero": 3,
        "nombre": "Base de rizos permanente",
        "duracion": 60,
        "precio": 65000,
        "precio_texto": "$65.000",
    },

    "mechas_hombre": {
        "numero": 4,
        "nombre": "Mechas",
        "duracion": 60,
        "precio": 70000,
        "precio_texto": "desde $70.000",
    },

    "decoloracion_global": {
        "numero": 5,
        "nombre": "Decoloración global",
        "duracion": 60,
        "precio": 120000,
        "precio_texto": "$120.000",
    },

    "corte_mujer": {
        "numero": 6,
        "nombre": "Corte de cabello mujer",
        "duracion": 60,
        "precio": 30000,
        "precio_texto": "$30.000",
        "detalle": "Incluye lavado de cabello, hidratación y brushing.",
    },

    "masaje_hidratacion": {
        "numero": 7,
        "nombre": "Masaje de hidratación",
        "duracion": 60,
        "precio": 45000,
        "precio_texto": "$45.000",
    },

    "botox_capilar": {
        "numero": 8,
        "nombre": "Botox capilar",
        "duracion": 60,
        "precio": 65000,
        "precio_texto": "desde $65.000",
    },

    "alisado_permanente": {
        "numero": 9,
        "nombre": "Alisado permanente",
        "duracion": 60,
        "precio": 70000,
        "precio_texto": "desde $70.000",
    },

    "retoque_raiz": {
        "numero": 10,
        "nombre": "Retoque de color de raíz",
        "duracion": 60,
        "precio": 50000,
        "precio_texto": "$50.000",
    },

    "bano_color": {
        "numero": 11,
        "nombre": "Baño de color",
        "duracion": 60,
        "precio": 30000,
        "precio_texto": "$30.000",
    },

    "diagnostico_balayage": {
        "numero": 12,
        "nombre": "Diagnóstico capilar gratuito para Balayage",
        "duracion": 60,
        "precio": 0,
        "precio_texto": "Diagnóstico gratuito · Balayage estimado desde $150.000",
        "detalle": "El valor final del Balayage se define después del diagnóstico capilar.",
    },
}

SERVICIO_POR_NUMERO = {
    servicio["numero"]: codigo
    for codigo, servicio in SERVICIOS.items()
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
            "precio": 0,
            "precio_texto": "Valor a confirmar",
        }
    )


def precio_texto_servicio(servicio):

    if servicio.get("precio_texto"):
        return servicio["precio_texto"]

    precio = servicio.get("precio", 0)

    return (
        f"${precio:,}"
        .replace(",", ".")
    )


def mensaje_menu_principal():

    return (
        f"¡Hola! 👋 Soy el asistente virtual de {ESTILISTA_NOMBRE}.\n\n"
        "¿Qué te gustaría hacer?\n\n"
        "1. Conocer servicios y precios 💇‍♂️💇‍♀️\n"
        "2. Agendar una hora 📅\n\n"
        "Respóndeme con 1 o 2."
    )


def mostrar_servicios():

    return (
        "Estos son nuestros servicios y precios 👇\n\n"
        "👨 HOMBRE\n"
        "1. Corte de cabello hombre — $17.000\n"
        "   Incluye perfilado de cejas, lavado de cabello y aplicación de producto.\n\n"
        "2. Perfilado de barba — $10.000\n"
        "3. Base de rizos permanente — $65.000\n"
        "4. Mechas — desde $70.000\n"
        "5. Decoloración global — $120.000\n\n"
        "👩 MUJER\n"
        "6. Corte de cabello mujer — $30.000\n"
        "   Incluye lavado de cabello, hidratación y brushing.\n\n"
        "7. Masaje de hidratación — $45.000\n"
        "8. Botox capilar — desde $65.000\n"
        "9. Alisado permanente — desde $70.000\n"
        "10. Retoque de color de raíz — $50.000\n"
        "11. Baño de color — $30.000\n"
        "12. Balayage — valor estimado desde $150.000\n"
        "    Requiere agendar un diagnóstico capilar gratuito para definir el valor final.\n\n"
        "Para agendar, respóndeme con el número del servicio (1 al 12).\n"
        "Si solo querías revisar precios, puedes escribir MENÚ para volver."
    )


def detectar_servicio_por_numero(texto):

    match = re.fullmatch(
        r"\s*(\d{1,2})\s*",
        texto or ""
    )

    if not match:
        return None

    numero = int(match.group(1))

    return SERVICIO_POR_NUMERO.get(numero)


def detectar_servicio(texto):

    texto_n = normalizar_texto(texto)

    servicio_numero = detectar_servicio_por_numero(texto)

    if servicio_numero:
        return servicio_numero

    # Servicios con nombres suficientemente específicos.
    if "corte" in texto_n and (
        "mujer" in texto_n
        or "dama" in texto_n
        or "femenino" in texto_n
    ):
        return "corte_mujer"

    if "corte" in texto_n and (
        "hombre" in texto_n
        or "varon" in texto_n
        or "masculino" in texto_n
    ):
        return "corte_hombre"

    if "perfilado" in texto_n and "barba" in texto_n:
        return "perfilado_barba"

    if "barba" in texto_n:
        return "perfilado_barba"

    if "rizo" in texto_n or "permanente de rizo" in texto_n:
        return "base_rizos"

    if "mecha" in texto_n:
        return "mechas_hombre"

    if "decoloracion" in texto_n:
        return "decoloracion_global"

    if "masaje" in texto_n and "hidrat" in texto_n:
        return "masaje_hidratacion"

    if "botox" in texto_n:
        return "botox_capilar"

    if "alisado" in texto_n:
        return "alisado_permanente"

    if "retoque" in texto_n and "raiz" in texto_n:
        return "retoque_raiz"

    if "bano de color" in texto_n or ("bano" in texto_n and "color" in texto_n):
        return "bano_color"

    if "balayage" in texto_n:
        return "diagnostico_balayage"

    # "corte" a secas es ambiguo: no asumimos hombre o mujer.
    return None


# ============================================================
# DETECTAR FECHA / HORA SOLICITADA EN TEXTO LIBRE
# ============================================================

def detectar_hora_solicitada(texto):
    """
    Interpreta expresiones como:
    - a las 3
    - a las 3:30
    - 15:00
    - 3 pm

    Como el negocio atiende entre 10:00 y 18:00,
    una hora simple como "3" se interpreta como 15:00.
    """

    texto_n = normalizar_texto(texto)

    # 15:00 / 15.30 / a las 15:00
    match = re.search(
        r"(?:a\s+las?\s+)?\b([01]?\d|2[0-3])[:.]([0-5]\d)\b",
        texto_n
    )

    if match:
        hora = int(match.group(1))
        minuto = int(match.group(2))
        return hora, minuto

    # 3 pm / 3:30 pm
    match = re.search(
        r"(?:a\s+las?\s+)?\b(1[0-2]|[1-9])"
        r"(?:[:.]([0-5]\d))?\s*(am|pm)\b",
        texto_n
    )

    if match:
        hora = int(match.group(1))
        minuto = int(match.group(2) or 0)
        periodo = match.group(3)

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

        return hora, minuto

    # "a las 3" / "a la 1"
    match = re.search(
        r"\ba\s+las?\s+(\d{1,2})\b",
        texto_n
    )

    if match:
        hora = int(match.group(1))

        # Dentro del horario del negocio, 1..6 normalmente
        # significa 13:00..18:00.
        if 1 <= hora <= 6:
            hora += 12

        return hora, 0

    return None


def detectar_fecha_solicitada(texto, hora_data=None):
    """
    Detecta hoy, mañana, pasado mañana o un día de la semana.

    Si el cliente solo escribe una hora (ej. "a las 3"),
    usa el próximo día de atención donde esa hora todavía
    tenga sentido.
    """

    texto_n = normalizar_texto(texto)
    ahora = ahora_local()

    fecha_base = ahora.replace(
        second=0,
        microsecond=0
    )

    if "pasado manana" in texto_n:
        return (
            fecha_base
            + timedelta(days=2)
        )

    if "manana" in texto_n:
        return (
            fecha_base
            + timedelta(days=1)
        )

    if "hoy" in texto_n:
        return fecha_base

    dias_map = {
        "lunes": 0,
        "martes": 1,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sabado": 5,
        "domingo": 6,
    }

    for nombre_dia, weekday in dias_map.items():

        if nombre_dia in texto_n:

            diferencia = (
                weekday
                - ahora.weekday()
            ) % 7

            # Si menciona el mismo día pero la hora ya pasó,
            # tomar la semana siguiente.
            if (
                diferencia == 0
                and hora_data
            ):

                hora, minuto = hora_data

                candidato_hoy = ahora.replace(
                    hour=hora,
                    minute=minuto,
                    second=0,
                    microsecond=0
                )

                if candidato_hoy <= ahora:
                    diferencia = 7

            return (
                fecha_base
                + timedelta(days=diferencia)
            )

    # Sin fecha explícita: usar hoy si todavía sirve,
    # si no, avanzar al siguiente día de atención.
    if hora_data:

        hora, minuto = hora_data

        for offset in range(8):

            candidato_fecha = (
                ahora
                + timedelta(days=offset)
            ).replace(
                hour=hora,
                minute=minuto,
                second=0,
                microsecond=0
            )

            if not es_dia_atencion(
                candidato_fecha
            ):
                continue

            if candidato_fecha <= ahora:
                continue

            return candidato_fecha

    return None


def construir_fecha_hora_solicitada(texto):
    """
    Devuelve un datetime timezone-aware si el mensaje contiene
    una hora interpretable.
    """

    hora_data = detectar_hora_solicitada(
        texto
    )

    if not hora_data:
        return None

    fecha = detectar_fecha_solicitada(
        texto,
        hora_data
    )

    if not fecha:
        return None

    hora, minuto = hora_data

    return fecha.replace(
        hour=hora,
        minute=minuto,
        second=0,
        microsecond=0
    )


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

def buscar_proximas_10_horas(desde=None):

    """
    Busca las próximas 10 horas disponibles haciendo UNA sola
    consulta a Google Calendar para evitar timeouts de Twilio.

    Si recibe "desde", comienza a buscar desde esa fecha/hora.
    """

    ahora = ahora_local()
    zona = obtener_zona()

    if desde is None:
        desde = ahora
    else:
        desde = desde.astimezone(zona)

    if desde < ahora:
        desde = ahora

    # Revisamos hasta 31 días hacia adelante.
    inicio_rango = desde

    fin_rango = (
        desde
        + timedelta(days=31)
    ).replace(
        hour=23,
        minute=59,
        second=59,
        microsecond=0
    )

    try:

        service = obtener_calendar_service()

        eventos_resultado = (
            service
            .events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=inicio_rango.isoformat(),
                timeMax=fin_rango.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=500,
            )
            .execute()
        )

        eventos = eventos_resultado.get(
            "items",
            []
        )

        print(
            "CALENDAR OK - EVENTOS EN RANGO:",
            len(eventos),
            "DESDE:",
            inicio_rango.isoformat(),
            "HASTA:",
            fin_rango.isoformat(),
        )

    except Exception as e:

        print(
            "ERROR CONSULTANDO CALENDAR PARA DISPONIBILIDAD:",
            repr(e)
        )

        import traceback
        print(traceback.format_exc())

        # None permite distinguir un error técnico de "sin horas disponibles".
        return None


    # Convertimos eventos del calendario en intervalos ocupados.
    ocupados = []

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

        # Evento con hora específica.
        if start_str and end_str:

            try:

                inicio_evento = (
                    datetime
                    .fromisoformat(
                        start_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                fin_evento = (
                    datetime
                    .fromisoformat(
                        end_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                ocupados.append(
                    (
                        inicio_evento,
                        fin_evento
                    )
                )

            except Exception:

                continue

        # Evento de día completo.
        elif (
            start_data.get("date")
            and end_data.get("date")
        ):

            try:

                inicio_evento = zona.localize(
                    datetime.combine(
                        datetime.fromisoformat(
                            start_data["date"]
                        ).date(),
                        datetime.min.time()
                    )
                )

                fin_evento = zona.localize(
                    datetime.combine(
                        datetime.fromisoformat(
                            end_data["date"]
                        ).date(),
                        datetime.min.time()
                    )
                )

                ocupados.append(
                    (
                        inicio_evento,
                        fin_evento
                    )
                )

            except Exception:

                continue


    resultados = []

    # Generar horas enteras de lunes a sábado,
    # entre 10:00 y 17:00.
    for offset in range(32):

        fecha = (
            desde
            + timedelta(days=offset)
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

            if inicio < desde:

                continue

            fin = (
                inicio
                + timedelta(
                    minutes=DURACION_RESERVA
                )
            )

            # No permitir reservas que terminen después de las 18:00.
            limite = fecha.replace(
                hour=HORA_CIERRE,
                minute=0,
                second=0,
                microsecond=0
            )

            if fin > limite:

                continue

            disponible = True

            for (
                inicio_ocupado,
                fin_ocupado
            ) in ocupados:

                if (
                    inicio < fin_ocupado
                    and fin > inicio_ocupado
                ):

                    disponible = False
                    break

            if disponible:

                resultados.append(
                    inicio
                )

                if len(resultados) >= 10:

                    print(
                        "10 HORAS DISPONIBLES:",
                        [
                            h.isoformat()
                            for h in resultados
                        ]
                    )

                    return resultados

    return resultados


def buscar_horas_disponibles_dia(fecha_obj):
    """
    Devuelve todas las horas enteras disponibles del día solicitado
    dentro del horario 10:00 a 18:00, haciendo una sola consulta
    a Google Calendar.
    """

    zona = obtener_zona()
    ahora = ahora_local()

    fecha_obj = fecha_obj.astimezone(zona)

    inicio_dia = fecha_obj.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )

    fin_dia = (
        inicio_dia
        + timedelta(days=1)
    )

    if not es_dia_atencion(inicio_dia):
        return []

    try:

        service = obtener_calendar_service()

        eventos_resultado = (
            service
            .events()
            .list(
                calendarId=CALENDAR_ID,
                timeMin=inicio_dia.isoformat(),
                timeMax=fin_dia.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=100,
            )
            .execute()
        )

        eventos = eventos_resultado.get(
            "items",
            []
        )

    except Exception as e:

        print(
            "ERROR CONSULTANDO DISPONIBILIDAD DEL DIA:",
            repr(e)
        )

        return None


    ocupados = []

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

        if start_str and end_str:

            try:

                inicio_evento = (
                    datetime
                    .fromisoformat(
                        start_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                fin_evento = (
                    datetime
                    .fromisoformat(
                        end_str.replace(
                            "Z",
                            "+00:00"
                        )
                    )
                    .astimezone(zona)
                )

                ocupados.append(
                    (
                        inicio_evento,
                        fin_evento
                    )
                )

            except Exception:
                continue

        elif (
            start_data.get("date")
            and end_data.get("date")
        ):

            # Evento de día completo: bloquear todo el día.
            return []


    resultados = []

    for hora in HORAS_DISPONIBLES:

        inicio = inicio_dia.replace(
            hour=hora,
            minute=0,
            second=0,
            microsecond=0
        )

        if inicio <= ahora:
            continue

        fin = inicio + timedelta(
            minutes=DURACION_RESERVA
        )

        limite = inicio_dia.replace(
            hour=HORA_CIERRE,
            minute=0,
            second=0,
            microsecond=0
        )

        if fin > limite:
            continue

        disponible = True

        for (
            inicio_ocupado,
            fin_ocupado
        ) in ocupados:

            if (
                inicio < fin_ocupado
                and fin > inicio_ocupado
            ):

                disponible = False
                break

        if disponible:
            resultados.append(inicio)

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
        "lista de precios",
        "que hacen",
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
        "disponibilidad",
        "horas disponibles",
        "hora disponible",
        "que horas tienes",
        "que hora tienes",
        "tienes hora",
        "tienes horas",
        "hay hora",
        "hay horas",
        "disponible manana",
        "disponible hoy",
    ]

    return any(
        p in texto_n
        for p in patrones
    )


def es_saludo_o_menu(texto):

    texto_n = normalizar_texto(texto)

    return texto_n in {
        "hola",
        "holi",
        "holaa",
        "buenas",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "menu",
        "inicio",
        "volver",
    }


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
Eres el asistente virtual de {ESTILISTA_NOMBRE}.

Responde en español de Chile, de forma breve, clara y amable.

REGLAS ESTRICTAS:
- No inventes servicios, precios, promociones, horarios ni disponibilidad.
- No agregues recomendaciones largas ni información que el cliente no pidió.
- No divagues ni cambies de tema.
- Tu objetivo es llevar al cliente a una de dos opciones:
  1) conocer servicios y precios;
  2) agendar una hora.
- Si la consulta no corresponde a estas opciones, responde brevemente y vuelve a ofrecerlas.
- Nunca confirmes una hora por tu cuenta. La aplicación consulta la agenda real.
- Nunca hables de APIs, programación, Twilio, bases de datos ni sistemas internos.

SERVICIOS HOMBRE:
1. Corte de cabello hombre — $17.000. Incluye perfilado de cejas, lavado de cabello y aplicación de producto.
2. Perfilado de barba — $10.000.
3. Base de rizos permanente — $65.000.
4. Mechas — desde $70.000.
5. Decoloración global — $120.000.

SERVICIOS MUJER:
6. Corte de cabello mujer — $30.000. Incluye lavado de cabello, hidratación y brushing.
7. Masaje de hidratación — $45.000.
8. Botox capilar — desde $65.000.
9. Alisado permanente — desde $70.000.
10. Retoque de color de raíz — $50.000.
11. Baño de color — $30.000.
12. Balayage — estimado desde $150.000. Requiere diagnóstico capilar gratuito para definir el valor final.

HORARIO:
Lunes a sábado, de 10:00 a 18:00.
La última hora de inicio es a las 17:00.
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
                    f"Valor: {precio_texto_servicio(servicio)}\n"
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

            # ====================================================
            # EL CLIENTE YA INDICÓ UNA HORA
            # Ejemplo: "quiero un corte a las 3"
            # ====================================================

            hora_solicitada = (
                construir_fecha_hora_solicitada(
                    texto
                )
            )

            if hora_solicitada:

                if canal == "whatsapp":

                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        (
                            "🔎 Estoy revisando si esa hora "
                            "está disponible en mi agenda. "
                            "Dame un momento 😊"
                        )
                    )

                disponible = verificar_disponibilidad(
                    hora_solicitada,
                    DURACION_RESERVA
                )

                if disponible is None:

                    return (
                        "No pude comprobar la agenda "
                        "en este momento 😕.\n\n"
                        "Intenta nuevamente en unos segundos."
                    )

                if disponible is True:

                    datos["fecha_hora"] = (
                        hora_solicitada.isoformat()
                    )

                    estado["paso"] = "nombre"

                    precio = precio_texto_servicio(
                        servicio_info
                    )

                    return (
                        "¡Sí! Esa hora está disponible 😊\n\n"
                        f"💈 {servicio_info['nombre']}\n"
                        f"💰 {precio}\n"
                        f"📅 {formato_fecha_larga(hora_solicitada)}\n\n"
                        "¿Me indicas tu nombre para continuar "
                        "con la reserva?"
                    )

                # La hora solicitada está ocupada:
                # mostrar alternativas reales.
                if canal == "whatsapp":

                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        (
                            "Esa hora está ocupada. "
                            "Estoy buscando las alternativas "
                            "más próximas 😊"
                        )
                    )

                horas = buscar_proximas_10_horas()

                if horas is None:
                    return (
                        "No pude consultar la agenda en este momento 😕.\n\n"
                        "Revisa la conexión con Google Calendar e intenta nuevamente."
                    )

                if not horas:

                    return (
                        f"La hora solicitada para "
                        f"{servicio_info['nombre']} está ocupada "
                        "y por ahora no encontré otras horas "
                        "disponibles."
                    )

                estado["horas_ofrecidas"] = [
                    h.isoformat()
                    for h in horas
                ]

                estado["paso"] = "seleccionar_hora"

                return (
                    f"La hora que pediste "
                    f"({formato_fecha_larga(hora_solicitada)}) "
                    "no está disponible 😕.\n\n"
                    "Estas son las próximas opciones disponibles:\n\n"
                    f"{formatear_opciones_horas(horas)}\n\n"
                    "Respóndeme con el número de la opción "
                    "que prefieras."
                )

            # ====================================================
            # NO INDICÓ HORA: flujo normal de próximas 10 horas
            # ====================================================

            if canal == "whatsapp":

                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    (
                        "🔎 Estoy buscando las horas más próximas "
                        "disponibles en mi agenda. "
                        "Dame un momento 😊"
                    )
                )

            horas = buscar_proximas_10_horas()

            if horas is None:
                return (
                    "Elegiste el servicio, pero no pude consultar "
                    "Google Calendar en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

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

            precio = precio_texto_servicio(
                servicio_info
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

        # ====================================================
        # EL CLIENTE PUEDE CAMBIAR DE DÍA EN LENGUAJE NATURAL
        # Ejemplos:
        # "mañana no hay?"
        # "¿y el viernes?"
        # "tienes disponibilidad el miércoles?"
        # ====================================================

        fecha_consultada = detectar_fecha_solicitada(
            texto,
            None
        )

        texto_n = normalizar_texto(texto)

        menciona_dia = any(
            palabra in texto_n
            for palabra in [
                "hoy",
                "manana",
                "pasado manana",
                "lunes",
                "martes",
                "miercoles",
                "jueves",
                "viernes",
                "sabado",
                "domingo",
            ]
        )

        if (
            fecha_consultada
            and menciona_dia
        ):

            if canal == "whatsapp":

                enviar_mensaje_progreso_twilio(
                    cliente_id,
                    (
                        "🔎 Estoy revisando la disponibilidad "
                        "de ese día en mi agenda. "
                        "Dame un momento 😊"
                    )
                )

            horas_dia = buscar_horas_disponibles_dia(
                fecha_consultada
            )

            if horas_dia is None:

                return (
                    "No pude comprobar la agenda "
                    "en este momento 😕.\n\n"
                    "Intenta nuevamente en unos segundos."
                )

            if not es_dia_atencion(
                fecha_consultada
            ):

                return (
                    f"El {DIAS_NOMBRES[fecha_consultada.weekday()]} "
                    "no atendemos.\n\n"
                    "Atendemos de lunes a sábado "
                    "entre 10:00 y 18:00.\n\n"
                    "¿Qué otro día quieres revisar?"
                )

            if not horas_dia:

                siguiente_dia = (
                    fecha_consultada
                    + timedelta(days=1)
                ).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0
                )

                if canal == "whatsapp":

                    enviar_mensaje_progreso_twilio(
                        cliente_id,
                        (
                            "Ese día está completo. "
                            "Estoy buscando las próximas "
                            "horas disponibles 😊"
                        )
                    )

                horas_siguientes = buscar_proximas_10_horas(
                    desde=siguiente_dia
                )

                if horas_siguientes is None:
                    return (
                        "No pude consultar las horas siguientes en Google Calendar 😕.\n\n"
                        "Intenta nuevamente en unos segundos."
                    )

                if not horas_siguientes:

                    return (
                        f"Para el "
                        f"{DIAS_NOMBRES[fecha_consultada.weekday()]} "
                        f"{fecha_consultada.day}/{fecha_consultada.month} "
                        "no tengo horas disponibles 😕.\n\n"
                        "Tampoco encontré horas disponibles "
                        "en los días siguientes por ahora."
                    )

                estado["horas_ofrecidas"] = [
                    h.isoformat()
                    for h in horas_siguientes
                ]

                estado["paso"] = "seleccionar_hora"

                return (
                    f"Para el "
                    f"{DIAS_NOMBRES[fecha_consultada.weekday()]} "
                    f"{fecha_consultada.day}/{fecha_consultada.month} "
                    "no tengo horas disponibles 😕.\n\n"
                    "Estas son las próximas 10 horas disponibles "
                    "desde el día siguiente:\n\n"
                    f"{formatear_opciones_horas(horas_siguientes)}\n\n"
                    "Respóndeme con el número de la hora "
                    "que prefieras, del 1 al 10."
                )

            estado["horas_ofrecidas"] = [
                h.isoformat()
                for h in horas_dia
            ]

            return (
                f"Sí 😊 Para el "
                f"{DIAS_NOMBRES[fecha_consultada.weekday()]} "
                f"{fecha_consultada.day}/{fecha_consultada.month} "
                "tengo estas horas disponibles:\n\n"
                f"{formatear_opciones_horas(horas_dia)}\n\n"
                "Respóndeme con el número de la hora "
                "que prefieras."
            )

        match = re.fullmatch(
            r"\s*(\d{1,2})\s*",
            texto
        )

        if not match:

            return (
                "Puedes responder con el número de una hora "
                "o preguntarme por otro día 😊\n\n"
                "Por ejemplo:\n"
                "• 1\n"
                "• ¿Mañana tienes disponibilidad?\n"
                "• ¿Y el viernes?"
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
        # SEGUNDA COMPROBACIÓN EN GOOGLE CALENDAR
        # ====================================================

        if canal == "whatsapp":

            enviar_mensaje_progreso_twilio(
                cliente_id,
                (
                    "🔎 Perfecto. Estoy verificando nuevamente "
                    "esa hora en la agenda antes de continuar 😊"
                )
            )

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

            if horas is None:
                return (
                    "Esa hora ya no está disponible y no pude actualizar "
                    "la agenda en este momento 😕. Intenta nuevamente."
                )

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
            "¡Perfecto! 😊\n\n"
            "La hora sigue disponible:\n"
            f"📅 {formato_fecha_larga(fecha_hora)}\n\n"
            "Ahora necesito tus datos para cerrar la cita.\n\n"
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

        if canal == "whatsapp":

            enviar_mensaje_progreso_twilio(
                cliente_id,
                (
                    "📅 Gracias. Estoy haciendo la última validación "
                    "en la agenda y cerrando tu cita. Dame un momento 😊"
                )
            )

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

        if horas is None:
            return (
                "No pude consultar la agenda en este momento 😕. "
                "Intenta nuevamente en unos segundos."
            )

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Estas son las próximas 10 horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "Respóndeme con el número de la hora que prefieras."
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

        if horas is None:
            return (
                "No pude actualizar la agenda en este momento 😕. "
                "Intenta nuevamente en unos segundos."
            )

        estado["horas_ofrecidas"] = [
            h.isoformat()
            for h in horas
        ]

        return (
            "Justo esa hora acaba de ocuparse 😕.\n\n"
            "Volví a consultar la agenda y estas son las "
            "próximas 10 horas disponibles:\n\n"
            f"{formatear_opciones_horas(horas)}\n\n"
            "Respóndeme con el número de la nueva hora "
            "que prefieras."
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

    precio = precio_texto_servicio(
        servicio
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
        "📍 Dirección de atención:\n"
        "2 Norte 280\n\n"
        "💳 Datos de transferencia:\n"
        "Nombre: Diego\n"
        "RUT: 18.149.067-5\n"
        "Banco: BancoEstado\n"
        "Tipo de cuenta: Cuenta Vista\n"
        "N° de cuenta: 18149067\n\n"
        "¡Te esperamos! 😊"
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

            "paso": "menu_principal",

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


twilio_client = None

if (
    TWILIO_ACCOUNT_SID
    and TWILIO_AUTH_TOKEN
):
    try:
        twilio_client = TwilioClient(
            TWILIO_ACCOUNT_SID,
            TWILIO_AUTH_TOKEN
        )
    except Exception as e:
        print(
            "ERROR INICIALIZANDO TWILIO CLIENT:",
            repr(e)
        )


def enviar_mensaje_progreso_twilio(
    telefono_twilio,
    texto
):
    """
    Envía un mensaje inmediato mientras el webhook continúa
    procesando la búsqueda de disponibilidad.
    """

    if not twilio_client:
        return False

    if not telefono_twilio:
        return False

    try:

        destino = telefono_twilio

        if not destino.startswith("whatsapp:"):
            destino = "whatsapp:" + destino

        twilio_client.messages.create(
            from_=TWILIO_WHATSAPP_FROM,
            to=destino,
            body=texto
        )

        print(
            "MENSAJE PROGRESO TWILIO ENVIADO:",
            destino
        )

        return True

    except Exception as e:

        print(
            "ERROR MENSAJE PROGRESO TWILIO:",
            repr(e)
        )

        return False


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

        print(
            "ESTADO WHATSAPP ANTES DE PROCESAR:",
            {
                "modo_agendar": estado.get("modo_agendar"),
                "paso": estado.get("paso"),
                "servicio": estado.get("datos_reserva", {}).get("servicio"),
                "horas_guardadas": len(estado.get("horas_ofrecidas", [])),
            }
        )

        texto_n = normalizar_texto(text)

        # MENÚ siempre permite salir de cualquier flujo y comenzar de nuevo.
        if texto_n in {"menu", "inicio", "volver"}:

            resetear_reserva(estado)
            estado["paso"] = "menu_principal"
            respuesta = mensaje_menu_principal()

        # Si el cliente ya está dentro de una reserva, seguimos el flujo
        # sin enviar la conversación a OpenAI.
        elif estado["modo_agendar"]:

            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        # Respuesta al menú inicial.
        elif estado.get("paso") == "menu_principal":

            if texto_n in {"1", "servicios", "precios", "servicios y precios"}:
                estado["paso"] = "servicios_mostrados"
                respuesta = mostrar_servicios()

            elif texto_n in {"2", "agendar", "reservar", "agenda"}:
                estado["modo_agendar"] = True
                estado["paso"] = "inicio"
                respuesta = (
                    "Perfecto 📅 ¿Qué servicio quieres agendar?\n\n"
                    + mostrar_servicios()
                )

            else:
                respuesta = mensaje_menu_principal()

        # Después de mostrar los precios, un número del 1 al 12
        # se interpreta como selección del servicio y abre la agenda.
        elif estado.get("paso") == "servicios_mostrados" and detectar_servicio(text):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"
            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        elif pregunta_servicios(text):

            estado["paso"] = "servicios_mostrados"
            respuesta = mostrar_servicios()

        elif es_intencion_agendar(text):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"
            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        elif detectar_servicio(text):

            estado["modo_agendar"] = True
            estado["paso"] = "inicio"
            respuesta = procesar_agenda(
                estado,
                text,
                cliente_id,
                "whatsapp"
            )

        # Saludos y cualquier consulta fuera de flujo vuelven al menú.
        # Así evitamos que el bot divague.
        else:

            estado["paso"] = "menu_principal"
            respuesta = mensaje_menu_principal()


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
