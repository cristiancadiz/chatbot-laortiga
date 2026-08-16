import os
import re
import uuid
import traceback
from datetime import datetime, timedelta, time

import pytz
import dateparser

from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

app = Flask(__name__)

TZ = pytz.timezone(os.environ.get("TIMEZONE", "America/Santiago"))

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5-mini")

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_FROM = os.environ.get(
    "TWILIO_WHATSAPP_FROM",
    "whatsapp:+14155238886"
)

GOOGLE_REFRESH_TOKEN = os.environ.get("GOOGLE_REFRESH_TOKEN")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID", "primary")

PRECIO_SERVICIO = 20000
DURACION_CITA = 60

if TWILIO_WHATSAPP_FROM and not TWILIO_WHATSAPP_FROM.startswith("whatsapp:"):
    TWILIO_WHATSAPP_FROM = "whatsapp:" + TWILIO_WHATSAPP_FROM


# ============================================================
# OPENAI
# ============================================================

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ============================================================
# MEMORIA TEMPORAL
# ============================================================
# Para probar en Render + Sandbox.
# Si Render reinicia el proceso, esta memoria se reinicia.
# Las reservas reales quedan en Google Calendar.

CLIENTES = {}
HISTORIALES = {}


def guardar_cliente(telefono, nombre=None):
    cliente = CLIENTES.get(
        telefono,
        {
            "telefono": telefono,
            "nombre": None,
            "servicio": None,
            "fecha": None,
            "hora": None,
        }
    )

    if nombre:
        cliente["nombre"] = nombre

    CLIENTES[telefono] = cliente
    return cliente


def obtener_cliente(telefono):
    return guardar_cliente(telefono)


def guardar_historial(telefono, rol, contenido):
    HISTORIALES.setdefault(telefono, [])
    HISTORIALES[telefono].append({
        "role": rol,
        "content": contenido
    })
    HISTORIALES[telefono] = HISTORIALES[telefono][-20:]


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte": "Corte de cabello",
    "corte de cabello": "Corte de cabello",
    "barba": "Barba",
    "corte y barba": "Corte y barba",
    "corte barba": "Corte y barba",
    "corte más barba": "Corte y barba",
    "corte mas barba": "Corte y barba",
    "perfilado": "Perfilado de barba",
    "perfilado de barba": "Perfilado de barba",
}


def detectar_servicio(texto):
    texto = texto.lower().strip()

    for clave in sorted(SERVICIOS.keys(), key=len, reverse=True):
        if clave in texto:
            return SERVICIOS[clave]

    return None


# ============================================================
# NOMBRE
# ============================================================

def detectar_nombre(texto):
    patrones = [
        r"(?:me llamo|mi nombre es)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40})",
        r"(?:soy)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40})",
    ]

    for patron in patrones:
        match = re.search(patron, texto, re.IGNORECASE)

        if match:
            nombre = re.sub(r"\s+", " ", match.group(1)).strip()
            palabras = nombre.split()

            if len(palabras) > 4:
                nombre = " ".join(palabras[:4])

            return nombre.title()

    return None


# ============================================================
# FECHA
# ============================================================

def detectar_fecha(texto):
    ahora = datetime.now(TZ)
    texto_lower = texto.lower()

    # Atajos para evitar que dateparser interprete mensajes genéricos
    indicadores = [
        "hoy", "mañana", "pasado mañana",
        "lunes", "martes", "miércoles", "miercoles",
        "jueves", "viernes", "sábado", "sabado", "domingo",
    ]

    tiene_fecha_explicita = any(x in texto_lower for x in indicadores)
    tiene_fecha_numerica = bool(
        re.search(r"\b\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?\b", texto_lower)
    )

    if not tiene_fecha_explicita and not tiene_fecha_numerica:
        return None

    try:
        fecha = dateparser.parse(
            texto,
            languages=["es"],
            settings={
                "TIMEZONE": "America/Santiago",
                "RETURN_AS_TIMEZONE_AWARE": True,
                "PREFER_DATES_FROM": "future",
                "RELATIVE_BASE": ahora,
            },
        )

        if not fecha:
            return None

        return fecha.astimezone(TZ)

    except Exception:
        print("ERROR detectar_fecha")
        print(traceback.format_exc())
        return None


# ============================================================
# HORA
# ============================================================

def detectar_hora(texto):
    texto = texto.lower()

    # 10:00 / 10.30
    match = re.search(
        r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b",
        texto
    )

    if match:
        hora = int(match.group(1))
        minuto = int(match.group(2))

        if 0 <= hora <= 23:
            return hora, minuto

    # 10 hrs / 10 horas
    match = re.search(
        r"\b([01]?\d|2[0-3])\s*(?:hrs?|horas?)\b",
        texto
    )

    if match:
        return int(match.group(1)), 0

    # 3 pm / 3:30 pm
    match = re.search(
        r"\b(1[0-2]|[1-9])(?:[:.]([0-5]\d))?\s*(am|pm)\b",
        texto
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

    return None


# ============================================================
# GOOGLE CALENDAR
# ============================================================

def google_configurado():
    return all([
        GOOGLE_REFRESH_TOKEN,
        GOOGLE_CLIENT_ID,
        GOOGLE_CLIENT_SECRET,
    ])


def get_calendar_service():
    if not google_configurado():
        raise RuntimeError(
            "Faltan GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID o GOOGLE_CLIENT_SECRET"
        )

    credentials = Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )

    return build(
        "calendar",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )


def es_horario_valido(inicio):
    inicio = inicio.astimezone(TZ)

    # domingo cerrado
    if inicio.weekday() == 6:
        return False

    apertura = time(10, 0)
    cierre = time(18, 0)

    fin = inicio + timedelta(minutes=DURACION_CITA)

    return (
        apertura <= inicio.time() < cierre
        and fin.time() <= cierre
    )


def obtener_eventos_dia(fecha):
    service = get_calendar_service()

    inicio = TZ.localize(datetime.combine(fecha, time(0, 0)))
    fin = inicio + timedelta(days=1)

    resultado = service.events().list(
        calendarId=GOOGLE_CALENDAR_ID,
        timeMin=inicio.isoformat(),
        timeMax=fin.isoformat(),
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    return resultado.get("items", [])


def esta_disponible(inicio):
    if not es_horario_valido(inicio):
        return False

    fin = inicio + timedelta(minutes=DURACION_CITA)

    for evento in obtener_eventos_dia(inicio.date()):
        start_str = evento.get("start", {}).get("dateTime")
        end_str = evento.get("end", {}).get("dateTime")

        # Ignorar eventos de día completo
        if not start_str or not end_str:
            continue

        inicio_evento = datetime.fromisoformat(
            start_str.replace("Z", "+00:00")
        ).astimezone(TZ)

        fin_evento = datetime.fromisoformat(
            end_str.replace("Z", "+00:00")
        ).astimezone(TZ)

        if inicio < fin_evento and fin > inicio_evento:
            return False

    return True


def buscar_horarios_disponibles(fecha, cantidad=5):
    horarios = []

    cursor = TZ.localize(datetime.combine(fecha, time(10, 0)))
    cierre = TZ.localize(datetime.combine(fecha, time(18, 0)))

    while cursor + timedelta(minutes=DURACION_CITA) <= cierre:
        if esta_disponible(cursor):
            horarios.append(cursor)

            if len(horarios) >= cantidad:
                break

        cursor += timedelta(minutes=30)

    return horarios


def crear_reserva_google(nombre, telefono, servicio, inicio):
    service = get_calendar_service()

    fin = inicio + timedelta(minutes=DURACION_CITA)

    precio = f"${PRECIO_SERVICIO:,}".replace(",", ".")

    evento = {
        "summary": f"Cita - {nombre} - {servicio}",
        "description": (
            "Reserva realizada por WhatsApp con el asistente virtual.\n\n"
            f"Cliente: {nombre}\n"
            f"WhatsApp: {telefono}\n"
            f"Servicio: {servicio}\n"
            f"Precio: {precio}"
        ),
        "start": {
            "dateTime": inicio.isoformat(),
            "timeZone": str(TZ),
        },
        "end": {
            "dateTime": fin.isoformat(),
            "timeZone": str(TZ),
        },
    }

    creado = service.events().insert(
        calendarId=GOOGLE_CALENDAR_ID,
        body=evento,
    ).execute()

    return creado


# ============================================================
# FORMATO
# ============================================================

DIAS = [
    "lunes", "martes", "miércoles", "jueves",
    "viernes", "sábado", "domingo"
]


def formatear_fecha(fecha):
    return (
        f"{DIAS[fecha.weekday()]} "
        f"{fecha.day:02d}/{fecha.month:02d}/{fecha.year}"
    )


def construir_inicio(fecha, hora_data):
    if not fecha or not hora_data:
        return None

    hora, minuto = hora_data

    naive = datetime.combine(
        fecha.date(),
        time(hora, minuto)
    )

    return TZ.localize(naive)


# ============================================================
# OPENAI
# ============================================================

SYSTEM_PROMPT = """
Eres el asistente virtual por WhatsApp de Estilista Diego en Chile.

Tu tono es cercano, breve, natural y amable.
No debes sonar como robot.

Servicios:
- Corte de cabello
- Barba
- Corte y barba
- Perfilado de barba

Precio:
Todos los servicios cuestan $20.000.

Horario:
Lunes a sábado de 10:00 a 18:00.
Domingo cerrado.

Cada cita dura 1 hora.

Tu objetivo principal es ayudar a resolver preguntas y, cuando el cliente
quiera, ayudarlo a reservar una hora.

Para reservar necesitas:
- nombre
- servicio
- fecha
- hora

Nunca inventes disponibilidad.
Nunca confirmes una cita si el sistema no indicó expresamente que fue creada.
Si el usuario solo saluda, saluda normalmente.
No digas que eres una IA a menos que te lo pregunten directamente.
"""


def preguntar_gpt(telefono, mensaje, contexto_extra=""):
    if not client:
        return (
            "Hola 😊 El asistente está activo, pero falta configurar "
            "OPENAI_API_KEY en Render."
        )

    historial = HISTORIALES.get(telefono, [])[-12:]

    input_messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]

    if contexto_extra:
        input_messages.append({
            "role": "system",
            "content": contexto_extra
        })

    input_messages.extend(historial)

    input_messages.append({
        "role": "user",
        "content": mensaje
    })

    try:
        # Responses API
        response = client.responses.create(
            model=OPENAI_MODEL,
            input=input_messages,
        )

        texto = response.output_text.strip()

        if not texto:
            return "¿En qué te puedo ayudar? 😊"

        return texto

    except Exception:
        print("ERROR OPENAI")
        print(traceback.format_exc())

        return (
            "Disculpa 🙏 Tuve un problema procesando el mensaje. "
            "Intenta nuevamente."
        )


# ============================================================
# INTENCIÓN
# ============================================================

def parece_agendamiento(texto):
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
        "pedir hora",
        "tomar hora",
    ]

    return any(p in texto for p in palabras)


# ============================================================
# PROCESAR MENSAJE
# ============================================================

def procesar_mensaje(telefono, mensaje):
    cliente = obtener_cliente(telefono)

    nombre = detectar_nombre(mensaje)
    servicio = detectar_servicio(mensaje)
    fecha = detectar_fecha(mensaje)
    hora_data = detectar_hora(mensaje)

    if nombre:
        cliente["nombre"] = nombre

    if servicio:
        cliente["servicio"] = servicio

    if fecha:
        cliente["fecha"] = fecha

    if hora_data:
        cliente["hora"] = hora_data

    # Si ya estábamos en proceso de reserva, seguimos aunque el nuevo
    # mensaje sea solo "15:00", "corte", "Antonio", etc.
    en_reserva = any([
        cliente.get("servicio"),
        cliente.get("fecha"),
        cliente.get("hora"),
    ])

    if not parece_agendamiento(mensaje) and not en_reserva:
        respuesta = preguntar_gpt(telefono, mensaje)
        guardar_historial(telefono, "user", mensaje)
        guardar_historial(telefono, "assistant", respuesta)
        return respuesta

    nombre = cliente.get("nombre")
    servicio = cliente.get("servicio")
    fecha = cliente.get("fecha")
    hora_data = cliente.get("hora")

    ahora = datetime.now(TZ)

    if fecha and fecha.date() < ahora.date():
        cliente["fecha"] = None
        cliente["hora"] = None
        return "Esa fecha ya pasó 😅. ¿Qué otro día te gustaría venir?"

    if fecha and fecha.weekday() == 6:
        cliente["fecha"] = None
        cliente["hora"] = None
        return (
            "Los domingos no atendemos 😊. "
            "Podemos buscar una hora de lunes a sábado."
        )

    if not fecha:
        return "Perfecto 😊 ¿Para qué día quieres reservar?"

    # Si fecha pero aún no hay hora, mostrar disponibilidad real.
    if fecha and not hora_data:
        if not google_configurado():
            return (
                f"Tengo registrado {formatear_fecha(fecha)}, pero falta "
                "configurar Google Calendar en Render para revisar disponibilidad."
            )

        try:
            horarios = buscar_horarios_disponibles(fecha.date(), cantidad=5)

            if not horarios:
                return (
                    f"No encontré horas disponibles para "
                    f"{formatear_fecha(fecha)} 😕. ¿Quieres probar otro día?"
                )

            opciones = ", ".join(h.strftime("%H:%M") for h in horarios)

            faltante = ""
            if not servicio:
                faltante = "\n\nTambién dime qué servicio necesitas."

            return (
                f"Para {formatear_fecha(fecha)} tengo estas horas disponibles:\n"
                f"🕐 {opciones}\n\n"
                f"¿Cuál te sirve?{faltante}"
            )

        except Exception:
            print("ERROR CALENDARIO")
            print(traceback.format_exc())
            return (
                "Tuve un problema consultando Google Calendar 🙏. "
                "Revisa las variables de Google en Render."
            )

    inicio = construir_inicio(fecha, hora_data)

    if not es_horario_valido(inicio):
        cliente["hora"] = None
        return (
            "Ese horario está fuera de atención 😊.\n"
            "Atendemos de lunes a sábado entre 10:00 y 18:00.\n"
            "Dime otra hora."
        )

    if not servicio:
        return (
            "Perfecto. ¿Qué servicio quieres?\n\n"
            "💈 Corte de cabello\n"
            "🧔 Barba\n"
            "✂️ Corte y barba\n"
            "✨ Perfilado de barba"
        )

    if not nombre:
        # Permite que un mensaje corto como "Antonio" sea tomado como nombre
        if re.fullmatch(r"[A-Za-zÁÉÍÓÚÑáéíóúñ ]{2,40}", mensaje.strip()):
            posible = mensaje.strip().title()

            palabras_reservadas = {
                "corte", "barba", "perfilado", "hola", "si", "sí",
                "no", "gracias", "mañana", "hoy"
            }

            if posible.lower() not in palabras_reservadas:
                cliente["nombre"] = posible
                nombre = posible

        if not nombre:
            return "Ya casi 😊. ¿A nombre de quién hago la reserva?"

    if not google_configurado():
        return (
            "Ya tengo todos los datos, pero falta configurar Google Calendar "
            "en Render. Revisa GOOGLE_REFRESH_TOKEN, GOOGLE_CLIENT_ID y "
            "GOOGLE_CLIENT_SECRET."
        )

    try:
        disponible = esta_disponible(inicio)

        if not disponible:
            cliente["hora"] = None

            horarios = buscar_horarios_disponibles(
                fecha.date(),
                cantidad=5
            )

            opciones = ", ".join(
                h.strftime("%H:%M")
                for h in horarios
            )

            if opciones:
                return (
                    f"La hora {inicio.strftime('%H:%M')} ya está ocupada 😕.\n\n"
                    f"Tengo disponibles: {opciones}\n"
                    "¿Cuál te sirve?"
                )

            return (
                "Esa hora ya está ocupada y no encontré otra disponible "
                "ese día 😕. ¿Probamos con otra fecha?"
            )

        evento = crear_reserva_google(
            nombre=nombre,
            telefono=telefono,
            servicio=servicio,
            inicio=inicio,
        )

        precio = f"${PRECIO_SERVICIO:,}".replace(",", ".")

        respuesta = (
            "¡Listo! 🎉 Tu cita quedó agendada.\n\n"
            f"👤 Nombre: {nombre}\n"
            f"💈 Servicio: {servicio}\n"
            f"📅 Fecha: {formatear_fecha(fecha)}\n"
            f"🕐 Hora: {inicio.strftime('%H:%M')}\n"
            f"💰 Precio: {precio}\n\n"
            "Te espero 😊"
        )

        # Limpiar datos de reserva, conservando nombre.
        CLIENTES[telefono] = {
            "telefono": telefono,
            "nombre": nombre,
            "servicio": None,
            "fecha": None,
            "hora": None,
        }

        print("RESERVA GOOGLE:", evento.get("id"))

        guardar_historial(telefono, "user", mensaje)
        guardar_historial(telefono, "assistant", respuesta)

        return respuesta

    except Exception:
        print("ERROR CREANDO RESERVA")
        print(traceback.format_exc())

        return (
            "No pude guardar la reserva en Google Calendar 😕. "
            "No la consideraré confirmada hasta que pueda crearla correctamente."
        )


# ============================================================
# WHATSAPP / TWILIO WEBHOOK
# ============================================================
#
# IMPORTANTE:
# Tu Sandbox está configurado actualmente con:
# https://chatbot-laortiga-9.onrender.com/whatsapp/webhook
#
# Dejamos además /webhook/whatsapp como alias por compatibilidad.
# ============================================================

@app.route("/whatsapp/webhook", methods=["POST"])
@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    try:
        telefono = request.form.get("From", "").strip()
        mensaje = request.form.get("Body", "").strip()
        message_sid = request.form.get("MessageSid", "").strip()

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono)
        print("Body:", mensaje)
        print("MessageSid:", message_sid)
        print("=" * 60)

        if not telefono:
            return "Missing From", 400

        twiml = MessagingResponse()

        # Algunos eventos pueden llegar sin texto.
        if not mensaje:
            twiml.message(
                "Por ahora puedo procesar mensajes de texto 😊."
            )
            return str(twiml), 200, {
                "Content-Type": "application/xml; charset=utf-8"
            }

        respuesta = procesar_mensaje(
            telefono=telefono,
            mensaje=mensaje
        )

        twiml.message(respuesta)

        return str(twiml), 200, {
            "Content-Type": "application/xml; charset=utf-8"
        }

    except Exception:
        print("ERROR WEBHOOK")
        print(traceback.format_exc())

        twiml = MessagingResponse()
        twiml.message(
            "Disculpa 🙏 Estoy teniendo un problema técnico. "
            "Intenta nuevamente."
        )

        return str(twiml), 200, {
            "Content-Type": "application/xml; charset=utf-8"
        }


# ============================================================
# HEALTH / HOME
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "service": "Asistente Virtual Estilista Diego",
        "whatsapp_webhook": "/whatsapp/webhook",
        "alternate_webhook": "/webhook/whatsapp",
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "openai": bool(OPENAI_API_KEY),
        "openai_model": OPENAI_MODEL,
        "twilio_account_sid": bool(TWILIO_ACCOUNT_SID),
        "twilio_auth_token": bool(TWILIO_AUTH_TOKEN),
        "twilio_whatsapp_from": TWILIO_WHATSAPP_FROM,
        "google_calendar": google_configurado(),
        "google_calendar_id": GOOGLE_CALENDAR_ID,
        "timezone": str(TZ),
    })


# ============================================================
# TEST LOCAL DEL WEBHOOK
# ============================================================

@app.route("/test", methods=["GET"])
def test():
    return jsonify({
        "ok": True,
        "message": "Servidor funcionando",
        "webhook": "POST /whatsapp/webhook"
    })


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    print("=" * 60)
    print("INICIANDO ASISTENTE")
    print("PORT:", port)
    print("OPENAI:", "OK" if OPENAI_API_KEY else "FALTA")
    print("TWILIO SID:", "OK" if TWILIO_ACCOUNT_SID else "FALTA")
    print("TWILIO TOKEN:", "OK" if TWILIO_AUTH_TOKEN else "FALTA")
    print("GOOGLE CALENDAR:", "OK" if google_configurado() else "FALTA")
    print("WEBHOOK PRINCIPAL: /whatsapp/webhook")
    print("=" * 60)

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
