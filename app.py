import os
import re
import time
import requests
import pytz
import dateparser
import openai

from datetime import datetime, timedelta
from flask import (
    Flask,
    redirect,
    url_for,
    session,
    request,
    render_template_string,
)
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

if not OPENAI_API_KEY:
    raise Exception("Falta OPENAI_API_KEY.")

client = openai.OpenAI(
    api_key=OPENAI_API_KEY
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

PRECIO_SERVICIO = 20000


# ============================================================
# HORARIO
# ============================================================

DIAS_ATENCION = {
    0: "lunes",
    1: "martes",
    2: "miércoles",
    3: "jueves",
    4: "viernes",
    5: "sábado",
}

HORA_APERTURA = 10
HORA_CIERRE = 18
DURACION_RESERVA = 60

HORAS_DISPONIBLES = list(
    range(HORA_APERTURA, HORA_CIERRE)
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


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte": {
        "nombre": "Corte de cabello",
        "duracion": 60,
        "precio": 20000,
    },
    "corte_barba": {
        "nombre": "Corte + barba",
        "duracion": 60,
        "precio": 20000,
    },
    "barba": {
        "nombre": "Arreglo de barba",
        "duracion": 60,
        "precio": 20000,
    },
    "corte_nino": {
        "nombre": "Corte de niño",
        "duracion": 60,
        "precio": 20000,
    },
    "perfilado": {
        "nombre": "Perfilado",
        "duracion": 60,
        "precio": 20000,
    },
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

if not GOOGLE_CLIENT_ID:
    raise Exception("Falta GOOGLE_CLIENT_ID.")

if not GOOGLE_CLIENT_SECRET:
    raise Exception("Falta GOOGLE_CLIENT_SECRET.")

SCOPES = [
    "https://www.googleapis.com/auth/calendar"
]


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

WA_SESSIONS = {}
PROCESSED_MSG_IDS = {}

DEDUP_TTL_SECONDS = 120


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
        redirect_uri=GOOGLE_REDIRECT_URI,
    )


# ============================================================
# GOOGLE CREDENTIALS
# ============================================================

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

    texto = (texto or "").strip().lower()

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


# ============================================================
# SERVICIOS
# ============================================================

def detectar_servicio(texto):

    t = normalizar_texto(texto)

    if (
        ("corte" in t or "cortar" in t)
        and "barba" in t
    ):
        return "corte_barba"

    if (
        "corte de nino" in t
        or "corte nino" in t
        or "cortar al nino" in t
        or "corte para nino" in t
    ):
        return "corte_nino"

    if "perfilado" in t or "perfil" in t:
        return "perfilado"

    if "barba" in t:
        return "barba"

    if "corte" in t or "cortar" in t:
        return "corte"

    return None


def servicios_texto():

    return (
        "Claro ✂️ Estos son nuestros servicios:\n\n"
        "1. Corte de cabello — $20.000\n"
        "2. Corte + barba — $20.000\n"
        "3. Arreglo de barba — $20.000\n"
        "4. Corte de niño — $20.000\n"
        "5. Perfilado — $20.000\n\n"
        "Todos los servicios duran 1 hora.\n\n"
        "Si quieres, también puedo ayudarte a "
        "buscar una hora disponible 😊"
    )


def obtener_servicio(codigo):

    return SERVICIOS.get(
        codigo,
        SERVICIOS["corte"]
    )


# ============================================================
# DETECCIÓN DE INTENCIONES
# ============================================================

def es_saludo(texto):

    t = normalizar_texto(texto)

    patrones = [
        "hola",
        "holaa",
        "holaaa",
        "buenas",
        "buenos dias",
        "buenas tardes",
        "buenas noches",
        "hey",
        "hello",
    ]

    return any(
        re.search(
            r"\b" + re.escape(p) + r"\b",
            t
        )
        for p in patrones
    )


def es_respuesta_social(texto):

    t = normalizar_texto(texto)

    patrones = [
        "bien",
        "muy bien",
        "super bien",
        "suuuper bien",
        "excelente",
        "genial",
        "todo bien",
        "bien gracias",
        "y tu",
        "y usted",
        "estoy bien",
        "aca bien",
        "aqui bien",
    ]

    return any(
        p in t
        for p in patrones
    )


def quiere_servicios(texto):

    t = normalizar_texto(texto)

    patrones = [
        "servicios",
        "que hacen",
        "que ofrecen",
        "que cortes hacen",
        "que cortes tienen",
        "precios",
        "precio",
        "cuanto sale",
        "cuanto cuesta",
        "cuanto valen",
        "valor",
        "valores",
        "tarifas",
    ]

    return any(
        p in t
        for p in patrones
    )


def es_intencion_agendar(texto):

    t = normalizar_texto(texto)

    patrones = [
        "quiero agendar",
        "quiero reservar",
        "quiero una hora",
        "quiero sacar hora",
        "sacar hora",
        "reservar una hora",
        "agendar una hora",
        "reservame",
        "reserva para",
        "agenda para",
        "puedo agendar",
        "puedo reservar",
        "necesito una hora",
        "dame una hora",
        "quiero mi hora",
        "me gustaria agendar",
        "me gustaría agendar",
    ]

    if any(p in t for p in patrones):
        return True

    # Si menciona un servicio junto a una fecha/hora,
    # claramente está intentando reservar.
    servicio = detectar_servicio(t)

    tiene_fecha = (
        construir_fecha_desde_texto(t)
        is not None
    )

    tiene_hora = (
        parse_hora_texto(t)
        is not None
    )

    if servicio and (tiene_fecha or tiene_hora):
        return True

    return False


def pregunta_disponibilidad(texto):

    t = normalizar_texto(texto)

    patrones = [
        "disponible",
        "disponibilidad",
        "que horas",
        "hay hora",
        "tienes hora",
        "tiene hora",
        "queda hora",
        "horas libres",
        "horas disponibles",
        "cuando tienes hora",
        "cuando hay hora",
        "cuando queda hora",
    ]

    return any(
        p in t
        for p in patrones
    )


def quiere_cancelar(texto):

    t = normalizar_texto(texto)

    patrones = [
        "no",
        "no gracias",
        "no gracias",
        "no quiero",
        "no quiero agendar",
        "dejalo",
        "dejalo",
        "cancelar",
        "cancela",
        "mejor no",
        "no por ahora",
        "despues",
        "después",
    ]

    return any(
        p == t or p in t
        for p in patrones
    )


# ============================================================
# FECHAS
# ============================================================

def detectar_dia_semana(texto):

    t = normalizar_texto(texto)

    dias = {
        "lunes": 0,
        "martes": 1,
        "miercoles": 2,
        "jueves": 3,
        "viernes": 4,
        "sabado": 5,
        "domingo": 6,
    }

    for nombre, numero in dias.items():

        if re.search(
            r"\b" + nombre + r"\b",
            t
        ):
            return numero

    return None


def detectar_dia_mes(texto):

    t = normalizar_texto(texto)

    meses = {
        "enero": 1,
        "febrero": 2,
        "marzo": 3,
        "abril": 4,
        "mayo": 5,
        "junio": 6,
        "julio": 7,
        "agosto": 8,
        "septiembre": 9,
        "setiembre": 9,
        "octubre": 10,
        "noviembre": 11,
        "diciembre": 12,
    }

    match = re.search(
        r"\b(\d{1,2})\s+de\s+"
        r"(enero|febrero|marzo|abril|mayo|junio|"
        r"julio|agosto|septiembre|setiembre|"
        r"octubre|noviembre|diciembre)\b",
        t
    )

    if match:
        return (
            int(match.group(1)),
            meses[match.group(2)]
        )

    match = re.search(
        r"\b(\d{1,2})[/-](\d{1,2})\b",
        t
    )

    if match:
        return (
            int(match.group(1)),
            int(match.group(2))
        )

    return None


def detectar_solo_dia(texto):

    t = normalizar_texto(texto)

    match = re.search(
        r"\b(?:el\s+|dia\s+)?(\d{1,2})\b",
        t
    )

    if not match:
        return None

    dia = int(match.group(1))

    if dia < 1 or dia > 31:
        return None

    # No confundir "a las 17" con día 17.
    if re.search(
        r"\ba\s+las?\s+" + str(dia) + r"\b",
        t
    ):
        return None

    return dia


def proximo_dia_semana(
    fecha_base,
    weekday_objetivo,
    incluir_hoy=True
):

    diferencia = (
        weekday_objetivo
        - fecha_base.weekday()
    ) % 7

    if diferencia == 0 and not incluir_hoy:
        diferencia = 7

    return fecha_base + timedelta(
        days=diferencia
    )


def construir_fecha_desde_texto(texto):

    zona = obtener_zona()
    ahora = ahora_local()
    t = normalizar_texto(texto)

    if re.search(r"\bpasado manana\b", t):
        return (
            ahora + timedelta(days=2)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    if re.search(r"\bmanana\b", t):
        return (
            ahora + timedelta(days=1)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    weekday = detectar_dia_semana(t)

    if weekday is not None:

        fecha = proximo_dia_semana(
            ahora,
            weekday,
            incluir_hoy=not (
                "proximo" in t
                or "siguiente" in t
            )
        )

        return fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    dia_mes = detectar_dia_mes(t)

    if dia_mes:

        dia, mes = dia_mes
        anio = ahora.year

        try:
            fecha = zona.localize(
                datetime(
                    anio,
                    mes,
                    dia
                )
            )
        except ValueError:
            return None

        if fecha.date() < ahora.date():

            try:
                fecha = zona.localize(
                    datetime(
                        anio + 1,
                        mes,
                        dia
                    )
                )
            except ValueError:
                return None

        return fecha

    solo_dia = detectar_solo_dia(t)

    if solo_dia:

        anio = ahora.year
        mes = ahora.month

        try:
            fecha = zona.localize(
                datetime(
                    anio,
                    mes,
                    solo_dia
                )
            )
        except ValueError:
            return None

        if fecha.date() < ahora.date():

            if mes == 12:
                anio += 1
                mes = 1
            else:
                mes += 1

            try:
                fecha = zona.localize(
                    datetime(
                        anio,
                        mes,
                        solo_dia
                    )
                )
            except ValueError:
                return None

        return fecha

    return None


def parse_hora_texto(texto):

    t = normalizar_texto(texto)

    match = re.search(
        r"\b(\d{1,2})\s*:\s*(\d{2})\b",
        t
    )

    if match:

        hora = int(match.group(1))
        minuto = int(match.group(2))

        if 0 <= hora <= 23 and 0 <= minuto <= 59:
            return hora, minuto

    match = re.search(
        r"\b(\d{1,2})\s*(am|pm)\b",
        t
    )

    if match:

        hora = int(match.group(1))
        periodo = match.group(2)

        if periodo == "pm" and hora < 12:
            hora += 12

        if periodo == "am" and hora == 12:
            hora = 0

        if 0 <= hora <= 23:
            return hora, 0

    match = re.search(
        r"\ba\s+las?\s+(\d{1,2})\b",
        t
    )

    if match:

        hora = int(match.group(1))

        if 1 <= hora <= 6:
            hora += 12

        return hora, 0

    return None


def parse_fecha_hora(texto):

    zona = obtener_zona()
    ahora = ahora_local()

    fecha = construir_fecha_desde_texto(
        texto
    )

    if fecha is None:

        try:

            resultado = dateparser.parse(
                texto,
                languages=["es"],
                settings={
                    "PREFER_DATES_FROM": "future",
                    "RETURN_AS_TIMEZONE_AWARE": True,
                    "TIMEZONE": TIMEZONE,
                    "TO_TIMEZONE": TIMEZONE,
                    "RELATIVE_BASE": ahora,
                }
            )

            if resultado:

                if resultado.tzinfo is None:
                    resultado = zona.localize(
                        resultado
                    )
                else:
                    resultado = resultado.astimezone(
                        zona
                    )

                fecha = resultado

        except Exception as e:

            print(
                "DATEPARSER:",
                repr(e)
            )

    if fecha is None:
        return None

    hora = parse_hora_texto(texto)

    if hora:

        h, m = hora

        fecha = fecha.replace(
            hour=h,
            minute=m,
            second=0,
            microsecond=0
        )
    else:

        fecha = fecha.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    return fecha.astimezone(zona)


# ============================================================
# FORMATO
# ============================================================

def formato_fecha_corta(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day:02d}/{fecha.month:02d} "
        f"a las {fecha.strftime('%H:%M')}"
    )


def formato_fecha_completa(fecha):

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

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        f"{DIAS_NOMBRES[fecha.weekday()]} "
        f"{fecha.day} de "
        f"{meses[fecha.month - 1]} "
        f"a las {fecha.strftime('%H:%M')}"
    )


# ============================================================
# CALENDAR
# ============================================================

def es_dia_atencion(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return fecha.weekday() in DIAS_ATENCION


def es_hora_valida(fecha):

    fecha = fecha.astimezone(
        obtener_zona()
    )

    return (
        es_dia_atencion(fecha)
        and fecha.minute == 0
        and fecha.second == 0
        and HORA_APERTURA
        <= fecha.hour
        < HORA_CIERRE
    )


def verificar_disponibilidad(
    inicio,
    duracion=DURACION_RESERVA
):

    try:

        inicio = inicio.astimezone(
            obtener_zona()
        )

        if not es_hora_valida(inicio):
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
# PRÓXIMAS 10 HORAS
# ============================================================

def buscar_proximas_horas(
    fecha_inicio=None,
    cantidad=10
):

    zona = obtener_zona()
    ahora = ahora_local()

    if fecha_inicio is None:
        fecha_inicio = ahora

    fecha_inicio = fecha_inicio.astimezone(
        zona
    )

    resultados = []

    # Revisamos hasta 30 días.
    # Esto evita que una agenda muy ocupada
    # deje al cliente sin opciones.
    for offset in range(0, 31):

        fecha = (
            fecha_inicio
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

            # MUY IMPORTANTE:
            # si Calendar dice que está ocupado,
            # simplemente saltamos esa hora.
            if disponible is True:

                resultados.append(
                    inicio
                )

                if len(resultados) >= cantidad:
                    return resultados

            elif disponible is None:

                # Si falla Calendar, no inventamos
                # disponibilidad.
                continue

    return resultados


def formatear_opciones_horas(horas):

    if not horas:
        return (
            "No encontré horas disponibles "
            "en este momento."
        )

    lineas = []

    for i, hora in enumerate(horas, 1):

        lineas.append(
            f"{i}. {formato_fecha_corta(hora)}"
        )

    return "\n".join(lineas)


def obtener_opcion_numerica(
    texto,
    cantidad
):

    t = normalizar_texto(texto).strip()

    # Permitir "3", "la 3", "opción 3"
    match = re.fullmatch(
        r"(?:la\s+|opcion\s+|opción\s+)?(\d{1,2})",
        t
    )

    if not match:
        return None

    numero = int(
        match.group(1)
    )

    if 1 <= numero <= cantidad:
        return numero

    return None


# ============================================================
# RESERVA
# ============================================================

def estado_reserva_nuevo():

    return {
        "activo": False,
        "servicio": None,
        "fecha": None,
        "hora": None,
        "nombre": None,
        "telefono": None,
        "opciones": [],
    }


def reiniciar_reserva(estado):

    telefono = estado.get(
        "telefono"
    )

    estado.clear()

    estado.update(
        estado_reserva_nuevo()
    )

    estado["telefono"] = telefono


def iniciar_agenda(estado, texto):

    estado["activo"] = True

    servicio = detectar_servicio(
        texto
    )

    if servicio:
        estado["servicio"] = servicio

    return procesar_agenda(
        estado,
        texto
    )


def procesar_agenda(
    estado,
    texto
):

    texto = (texto or "").strip()

    datos = estado

    # ========================================================
    # CANCELAR
    # ========================================================

    if quiere_cancelar(texto):

        reiniciar_reserva(datos)

        return (
            "No hay problema 😊 "
            "Lo dejamos para otra ocasión.\n\n"
            "¡Que tengas un excelente día! 👋"
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

        else:

            return (
                "Claro 😊 Antes de buscar tu hora, "
                "¿qué servicio quieres?\n\n"
                "1. Corte de cabello — $20.000\n"
                "2. Corte + barba — $20.000\n"
                "3. Arreglo de barba — $20.000\n"
                "4. Corte de niño — $20.000\n"
                "5. Perfilado — $20.000"
            )


    # ========================================================
    # SI YA TENEMOS OPCIONES,
    # ESPERAMOS NÚMERO
    # ========================================================

    if datos.get("opciones"):

        numero = obtener_opcion_numerica(
            texto,
            len(datos["opciones"])
        )

        if numero is not None:

            seleccion = datos["opciones"][
                numero - 1
            ]

            # Segunda comprobación.
            # Esto evita que dos personas
            # puedan reservar la misma hora.
            disponible = verificar_disponibilidad(
                seleccion,
                DURACION_RESERVA
            )

            if disponible is None:

                return (
                    "No pude comprobar la agenda "
                    "en este momento 😕.\n\n"
                    "Intenta nuevamente."
                )

            if not disponible:

                datos["opciones"] = []

                nuevas = buscar_proximas_horas(
                    seleccion,
                    10
                )

                datos["opciones"] = nuevas

                if nuevas:

                    return (
                        "Justo esa hora acaba de "
                        "ser ocupada 😕.\n\n"
                        "Te muestro nuevamente las "
                        "próximas horas disponibles:\n\n"
                        + formatear_opciones_horas(
                            nuevas
                        )
                        + "\n\n"
                        "Respóndeme con el número "
                        "de la hora que prefieres."
                    )

                return (
                    "Justo esa hora se ocupó 😕.\n\n"
                    "No encontré otra disponibilidad "
                    "cercana."
                )

            datos["fecha"] = seleccion.isoformat()
            datos["hora"] = seleccion.strftime(
                "%H:%M"
            )
            datos["opciones"] = []

        else:

            # Si escribió una hora o fecha nueva,
            # descartamos las opciones anteriores.
            if (
                parse_fecha_hora(texto)
                is None
                and detectar_servicio(texto)
                is None
            ):
                return (
                    "Puedes elegir una de las horas "
                    "respondiendo con un número del "
                    "1 al "
                    f"{len(datos['opciones'])}. 😊"
                )


    # ========================================================
    # FECHA / HORA
    # ========================================================

    if not datos["fecha"]:

        fecha = parse_fecha_hora(
            texto
        )

        hora = parse_hora_texto(
            texto
        )

        if fecha is not None and hora is not None:

            # Si entregó día y hora directamente,
            # primero verificamos.
            if not es_dia_atencion(fecha):

                nuevas = buscar_proximas_horas(
                    fecha,
                    10
                )

                datos["opciones"] = nuevas

                if nuevas:

                    return (
                        "Ese día no atendemos 😕.\n\n"
                        "Estas son las próximas "
                        "horas disponibles:\n\n"
                        + formatear_opciones_horas(
                            nuevas
                        )
                        + "\n\n"
                        "Elige una respondiendo "
                        "con el número."
                    )

                return (
                    "Ese día no atendemos 😕.\n\n"
                    "Atendemos de lunes a sábado, "
                    "de 10:00 a 18:00."
                )

            if (
                fecha.minute != 0
                or fecha.hour < HORA_APERTURA
                or fecha.hour >= HORA_CIERRE
            ):

                nuevas = buscar_proximas_horas(
                    fecha,
                    10
                )

                datos["opciones"] = nuevas

                return (
                    "Las reservas son por horas "
                    "completas 🕐.\n\n"
                    "Te muestro las próximas "
                    "horas disponibles:\n\n"
                    + formatear_opciones_horas(
                        nuevas
                    )
                    + "\n\n"
                    "Elige una respondiendo con "
                    "el número."
                )

            if fecha <= ahora_local():

                nuevas = buscar_proximas_horas(
                    ahora_local(),
                    10
                )

                datos["opciones"] = nuevas

                return (
                    "Esa hora ya pasó 😕.\n\n"
                    "Estas son las próximas "
                    "horas disponibles:\n\n"
                    + formatear_opciones_horas(
                        nuevas
                    )
                    + "\n\n"
                    "Elige una respondiendo con "
                    "el número."
                )

            disponible = verificar_disponibilidad(
                fecha,
                DURACION_RESERVA
            )

            if disponible is True:

                datos["fecha"] = fecha.isoformat()
                datos["hora"] = fecha.strftime(
                    "%H:%M"
                )

            elif disponible is False:

                nuevas = buscar_proximas_horas(
                    fecha,
                    10
                )

                datos["opciones"] = nuevas

                if nuevas:

                    return (
                        "Esa hora ya está ocupada 😕.\n\n"
                        "Estas son las próximas horas "
                        "disponibles:\n\n"
                        + formatear_opciones_horas(
                            nuevas
                        )
                        + "\n\n"
                        "Elige una respondiendo "
                        "con el número."
                    )

                return (
                    "Esa hora está ocupada y no "
                    "encontré disponibilidad cercana."
                )

            else:

                return (
                    "No pude consultar la agenda "
                    "en este momento 😕."
                )

        else:

            # Si solamente dio día,
            # mostramos 10 horas disponibles.
            fecha_sola = construir_fecha_desde_texto(
                texto
            )

            if fecha_sola is not None:

                nuevas = buscar_proximas_horas(
                    fecha_sola,
                    10
                )

            else:

                nuevas = buscar_proximas_horas(
                    ahora_local(),
                    10
                )

            datos["opciones"] = nuevas

            if nuevas:

                return (
                    "Perfecto 😊 Estas son las "
                    "próximas horas disponibles:\n\n"
                    + formatear_opciones_horas(
                        nuevas
                    )
                    + "\n\n"
                    "Elige una respondiendo con "
                    "el número del 1 al "
                    f"{len(nuevas)}."
                )

            return (
                "No encontré horas disponibles "
                "en este momento 😕.\n\n"
                "¿Quieres probar con otro día?"
            )


    # ========================================================
    # NOMBRE
    # ========================================================

    if not datos["nombre"]:

        # Evitar tomar un número como nombre.
        if texto.isdigit():

            return "¿Me indicas tu nombre, por favor? 😊"

        if len(texto) < 2:

            return "¿Me indicas tu nombre, por favor? 😊"

        datos["nombre"] = texto

        if datos.get("telefono"):

            return confirmar_y_reservar(
                datos
            )

        return (
            f"Perfecto, {datos['nombre']} 👍\n\n"
            "¿Me das tu número de teléfono?"
        )


    # ========================================================
    # TELÉFONO
    # ========================================================

    if not datos["telefono"]:

        datos["telefono"] = texto

        return confirmar_y_reservar(
            datos
        )


    # ========================================================
    # COMPLETAR
    # ========================================================

    return confirmar_y_reservar(
        datos
    )


# ============================================================
# CREAR EVENTO
# ============================================================

def crear_evento_diego(
    inicio,
    servicio_codigo,
    nombre_cliente,
    telefono_cliente
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
            "summary": (
                f"{servicio['nombre']} - "
                f"{nombre_cliente}"
            ),

            "description": (
                "Reserva creada por Asistente "
                "Virtual de Estilista Diego.\n\n"
                f"Cliente: {nombre_cliente}\n"
                f"Teléfono: {telefono_cliente}\n"
                f"Servicio: {servicio['nombre']}\n"
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

            "extendedProperties": {
                "private": {
                    "cliente": nombre_cliente,
                    "telefono": telefono_cliente,
                    "servicio": servicio["nombre"],
                    "duracion": str(DURACION_RESERVA),
                    "origen": "Asistente Virtual Diego",
                }
            },
        }

        resultado = (
            service
            .events()
            .insert(
                calendarId=CALENDAR_ID,
                body=evento
            )
            .execute()
        )

        print(
            "EVENTO CREADO:",
            resultado.get("id")
        )

        return {
            "ok": True,
            "evento_id": resultado.get("id"),
            "link": resultado.get("htmlLink"),
        }

    except Exception as e:

        print(
            "ERROR CREANDO EVENTO:",
            repr(e)
        )

        return {
            "ok": False,
            "error": str(e),
        }


# ============================================================
# CONFIRMAR / CREAR RESERVA
# ============================================================

def confirmar_y_reservar(datos):

    if not datos.get("fecha"):
        return "Me falta seleccionar la hora 😊."

    if not datos.get("servicio"):
        return "Me falta seleccionar el servicio 😊."

    if not datos.get("nombre"):
        return "Me falta tu nombre 😊."

    if not datos.get("telefono"):
        return "Me falta tu teléfono 📞."

    try:

        inicio = datetime.fromisoformat(
            datos["fecha"]
        )

        if inicio.tzinfo is None:

            inicio = obtener_zona().localize(
                inicio
            )

        inicio = inicio.astimezone(
            obtener_zona()
        )

    except Exception:

        datos["fecha"] = None

        return (
            "Necesito volver a confirmar "
            "la hora 😊."
        )

    # ========================================================
    # ÚLTIMA COMPROBACIÓN
    # ========================================================

    disponible = verificar_disponibilidad(
        inicio,
        DURACION_RESERVA
    )

    if disponible is None:

        return (
            "No pude comprobar nuevamente "
            "la agenda 😕.\n\n"
            "Intenta nuevamente."
        )

    if disponible is False:

        datos["fecha"] = None
        datos["hora"] = None

        nuevas = buscar_proximas_horas(
            inicio,
            10
        )

        datos["opciones"] = nuevas

        if nuevas:

            return (
                "Justo esa hora acaba de ser "
                "ocupada 😕.\n\n"
                "Te muestro nuevamente las "
                "próximas horas disponibles:\n\n"
                + formatear_opciones_horas(
                    nuevas
                )
                + "\n\n"
                "Elige una respondiendo con "
                "el número."
            )

        return (
            "Justo esa hora acaba de ser ocupada "
            "y no encontré otra disponibilidad "
            "cercana 😕."
        )

    # ========================================================
    # CREAR EVENTO
    # ========================================================

    resultado = crear_evento_diego(
        inicio=inicio,
        servicio_codigo=datos["servicio"],
        nombre_cliente=datos["nombre"],
        telefono_cliente=datos["telefono"],
    )

    if not resultado["ok"]:

        print(
            "ERROR RESERVA:",
            resultado.get("error")
        )

        return (
            "No pude completar la reserva "
            "en este momento 😕.\n\n"
            "Intenta nuevamente."
        )

    servicio = obtener_servicio(
        datos["servicio"]
    )

    nombre = datos["nombre"]
    telefono = datos["telefono"]

    texto = (
        "✅ ¡Reserva confirmada!\n\n"
        f"✂️ Servicio: {servicio['nombre']}\n"
        f"💰 Valor: $20.000\n"
        f"👤 Cliente: {nombre}\n"
        f"📞 Teléfono: {telefono}\n"
        f"📅 {formato_fecha_completa(inicio)}\n\n"
        f"La hora quedó agendada directamente "
        f"en la agenda de {ESTILISTA_NOMBRE}.\n\n"
        "¡Te esperamos! 🙌"
    )

    telefono_guardar = telefono

    reiniciar_reserva(datos)

    datos["telefono"] = telefono_guardar

    return texto


# ============================================================
# OPENAI CONVERSACIONAL
# ============================================================

def responder_openai(
    historial,
    pregunta
):

    system_prompt = f"""
Eres el asistente virtual de un estilista/barbero.

Tu nombre es:
"Asistente Virtual de Estilista {ESTILISTA_NOMBRE}".

Hablas español natural de Chile.

Tu prioridad es conversar de manera humana y fluida.

NO debes responder siempre:
"¡Claro! Cuéntame qué necesitas."

Eso está prohibido como respuesta automática
cuando la persona simplemente está conversando.

EJEMPLO CORRECTO:

Cliente:
Hola

Asistente:
¡Hola! 👋 ¿Cómo estás?

Cliente:
Súper bien y tú?

Asistente:
¡Muy bien también! 😄 Me alegra que estés bien.
¿Te gustaría conocer los servicios de Diego
o prefieres que te ayude a buscar una hora?

Otro ejemplo:

Cliente:
Hola, cómo estás?

Asistente:
¡Hola! 👋 Muy bien, gracias 😄
¿Y tú cómo estás?

Después de una pequeña conversación puedes
invitar naturalmente a conocer los servicios
o agendar.

NO fuerces una reserva si la persona solamente
está saludando o conversando.

SERVICIOS:

- Corte de cabello: $20.000
- Corte + barba: $20.000
- Arreglo de barba: $20.000
- Corte de niño: $20.000
- Perfilado: $20.000

Todos duran 1 hora.

HORARIO:

Lunes a sábado,
10:00 a 18:00.

Domingo cerrado.

Las reservas se realizan directamente
en la agenda de {ESTILISTA_NOMBRE}.

El cliente no necesita Google Calendar.

Si la persona pregunta por servicios o precios,
muéstrale los servicios y valores.

Si la persona manifiesta claramente que quiere
agendar, el sistema externo iniciará el flujo
de agenda.

No inventes disponibilidad.

La disponibilidad real siempre la consulta
Google Calendar.

Mantén respuestas cortas, naturales y humanas.

No hables de código, APIs, Google Calendar,
OpenAI ni sistemas internos.

Si el cliente dice que no quiere agendar,
no insistas.

Puedes despedirte cordialmente.
"""

    try:

        mensajes = [
            {
                "role": "system",
                "content": system_prompt,
            }
        ]

        mensajes.extend(
            historial[-14:]
        )

        mensajes.append(
            {
                "role": "user",
                "content": pregunta,
            }
        )

        completion = (
            client
            .chat
            .completions
            .create(
                model="gpt-4o-mini",
                messages=mensajes,
                max_tokens=250,
                temperature=0.8,
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

    return (
        "¡Hola! 👋 Qué bueno verte por acá. "
        "¿Cómo estás?"
    )


# ============================================================
# MOTOR PRINCIPAL
# ============================================================

def procesar_mensaje(
    estado,
    historial,
    texto
):

    texto = (texto or "").strip()

    if not texto:
        return (
            "¡Hola! 👋 ¿Cómo estás?"
        )

    # ========================================================
    # SI ESTÁ EN AGENDA
    # ========================================================

    if estado.get("activo"):

        # Si ya está seleccionando hora,
        # solamente procesa la selección.
        return procesar_agenda(
            estado,
            texto
        )

    # ========================================================
    # SALUDO
    # ========================================================

    if es_saludo(texto):

        # Si solamente dijo hola.
        if len(
            normalizar_texto(texto).split()
        ) <= 3:

            return (
                "¡Hola! 👋 ¿Cómo estás?"
            )

        return responder_openai(
            historial,
            texto
        )

    # ========================================================
    # RESPUESTA SOCIAL
    # ========================================================

    if es_respuesta_social(texto):

        return (
            "¡Qué bueno! 😄 Yo también estoy "
            "muy bien, gracias por preguntar.\n\n"
            "Si quieres, puedo contarte los "
            "servicios de Diego ✂️ o ayudarte "
            "a buscar una hora."
        )

    # ========================================================
    # SERVICIOS
    # ========================================================

    if quiere_servicios(texto):

        return servicios_texto()

    # ========================================================
    # AGENDA
    # ========================================================

    if es_intencion_agendar(texto):

        estado["activo"] = True

        return iniciar_agenda(
            estado,
            texto
        )

    # ========================================================
    # DISPONIBILIDAD
    # ========================================================

    if pregunta_disponibilidad(texto):

        estado["activo"] = True

        return iniciar_agenda(
            estado,
            texto
        )

    # ========================================================
    # SERVICIO MENCIONADO SOLO
    # ========================================================

    servicio = detectar_servicio(
        texto
    )

    if servicio:

        # "corte" solo no necesariamente
        # significa reservar.
        # Mostramos información y damos opción.
        nombre = SERVICIOS[
            servicio
        ]["nombre"]

        return (
            f"Sí 😊 Tenemos {nombre} por "
            "$20.000.\n\n"
            "La atención dura 1 hora.\n\n"
            "Si quieres reservarlo, dime algo "
            "como \"quiero agendar\" y te muestro "
            "las próximas horas disponibles."
        )

    # ========================================================
    # CONVERSACIÓN NORMAL
    # ========================================================

    return responder_openai(
        historial,
        texto
    )


# ============================================================
# SESIÓN WHATSAPP
# ============================================================

def get_wa_session(wa_id):

    if wa_id not in WA_SESSIONS:

        WA_SESSIONS[wa_id] = {
            "historial": [],
            "activo": False,
            "servicio": None,
            "fecha": None,
            "hora": None,
            "nombre": None,
            "telefono": wa_id,
            "opciones": [],
        }

    return WA_SESSIONS[wa_id]


# ============================================================
# WHATSAPP ENVÍO
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
            "WhatsApp no configurado."
        )
        return None

    url = (
        "https://graph.facebook.com/v20.0/"
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
            timeout=20
        )

        print(
            "WhatsApp:",
            response.status_code,
            response.text[:300]
        )

        return response

    except Exception as e:

        print(
            "ERROR WHATSAPP:",
            repr(e)
        )

        return None


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

    if "historial" not in session:

        session["historial"] = [
            {
                "role": "assistant",
                "content":
                    "¡Hola! 👋 ¿Cómo estás?"
            }
        ]

    if "estado_reserva" not in session:

        session[
            "estado_reserva"
        ] = estado_reserva_nuevo()

    if request.method == "POST":

        pregunta = (
            request.form
            .get("pregunta", "")
            .strip()
        )

        if pregunta:

            historial = session[
                "historial"
            ]

            estado = session[
                "estado_reserva"
            ]

            historial.append(
                {
                    "role": "user",
                    "content": pregunta,
                }
            )

            respuesta = procesar_mensaje(
                estado,
                historial,
                pregunta
            )

            historial.append(
                {
                    "role": "assistant",
                    "content": respuesta,
                }
            )

            session[
                "historial"
            ] = historial[-30:]

            session[
                "estado_reserva"
            ] = estado

            session.modified = True

    return render_template_string(
        TEMPLATE,
        historial=session[
            "historial"
        ]
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
        and token == WHATSAPP_VERIFY_TOKEN
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

    try:

        entry = (
            data.get("entry")
            or []
        )

        if not entry:
            return "ok", 200

        changes = (
            entry[0].get("changes")
            or []
        )

        if not changes:
            return "ok", 200

        value = (
            changes[0].get("value")
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

        if not wa_id:
            return "ok", 200

        text = (
            msg.get("text")
            or {}
        ).get(
            "body",
            ""
        ).strip()

        if not text:

            wa_send_text(
                wa_id,
                "Por ahora puedo ayudarte por texto 😊."
            )

            return "ok", 200

        # ====================================================
        # DEDUPLICACIÓN
        # ====================================================

        ahora_ts = time.time()

        for old_id in list(
            PROCESSED_MSG_IDS.keys()
        ):

            if (
                ahora_ts
                - PROCESSED_MSG_IDS[old_id]
                > DEDUP_TTL_SECONDS
            ):

                del PROCESSED_MSG_IDS[
                    old_id
                ]

        if msg_id:

            if msg_id in PROCESSED_MSG_IDS:
                return "ok", 200

            PROCESSED_MSG_IDS[
                msg_id
            ] = ahora_ts

        # ====================================================
        # SESIÓN
        # ====================================================

        estado = get_wa_session(
            wa_id
        )

        estado["telefono"] = wa_id

        historial = estado[
            "historial"
        ]

        historial.append(
            {
                "role": "user",
                "content": text,
            }
        )

        respuesta = procesar_mensaje(
            estado,
            historial,
            text
        )

        historial.append(
            {
                "role": "assistant",
                "content": respuesta,
            }
        )

        estado[
            "historial"
        ] = historial[-30:]

        wa_send_text(
            wa_id,
            respuesta
        )

    except Exception as e:

        print(
            "WHATSAPP WEBHOOK ERROR:",
            repr(e)
        )

    return "ok", 200


# ============================================================
# GOOGLE LOGIN
# ============================================================

@app.route("/admin/login")
def admin_login():

    try:

        flow = crear_google_flow()

        authorization_url, state = (
            flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
        )

        session.permanent = True

        session[
            "google_oauth_state"
        ] = state

        session[
            "google_code_verifier"
        ] = flow.code_verifier

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
            mensaje=str(e),
        )


# ============================================================
# GOOGLE CALLBACK
# ============================================================

@app.route("/callback")
def callback():

    error = request.args.get(
        "error"
    )

    if error:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Google rechazó la autorización",
            mensaje=f"Google respondió: {error}",
        )

    code = request.args.get(
        "code"
    )

    if not code:

        return render_template_string(
            ERROR_TEMPLATE,
            titulo="Falta código OAuth",
            mensaje="Google no entregó el parámetro code.",
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

        refresh_token = credentials.refresh_token

        if not refresh_token:

            return render_template_string(
                ERROR_TEMPLATE,
                titulo=
                    "Google no entregó refresh token",
                mensaje=(
                    "Google autorizó la aplicación, "
                    "pero no entregó refresh_token."
                ),
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
            mensaje=str(e),
        )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("home")
    )


# ============================================================
# HTML TOKEN
# ============================================================

TOKEN_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Google Calendar autorizado</title>

<style>
body {
    font-family: Arial, sans-serif;
    max-width: 850px;
    margin: 50px auto;
    padding: 20px;
    background: #f5f5f5;
}

.box {
    background: white;
    padding: 30px;
    border-radius: 15px;
    box-shadow: 0 5px 25px rgba(0,0,0,.10);
}

textarea {
    width: 100%;
    height: 120px;
    margin-top: 15px;
}

.success {
    color: #087f23;
}
</style>
</head>

<body>

<div class="box">

<h1 class="success">
✅ Google Calendar autorizado
</h1>

<p>
La autorización fue completada correctamente.
</p>

<p>
Este es tu <b>GOOGLE_REFRESH_TOKEN</b>:
</p>

<textarea readonly>{{ token }}</textarea>

<h3>Ahora:</h3>

<ol>
<li>Ve a Render.</li>
<li>Environment.</li>
<li>Busca GOOGLE_REFRESH_TOKEN.</li>
<li>Pega el token.</li>
<li>Guarda los cambios.</li>
<li>Espera el deploy.</li>
</ol>

<p>
⚠️ No compartas este token.
</p>

</div>

</body>
</html>
"""


# ============================================================
# ERROR
# ============================================================

ERROR_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">

<head>
<meta charset="UTF-8">
<title>Error</title>

<style>

body {
    font-family: Arial, sans-serif;
    max-width: 800px;
    margin: 50px auto;
    padding: 20px;
}

.box {
    padding: 30px;
    border-radius: 15px;
    background: #fff3f3;
    border: 1px solid #ffcccc;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;
}

</style>
</head>

<body>

<div class="box">

<h1>
❌ {{ titulo }}
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
# CHAT
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
    font-family: Arial, sans-serif;
    background: #f3f4f6;
}

#chat-container {

    position: fixed;

    bottom: 20px;
    right: 20px;

    width: 380px;
    height: 600px;

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
    opacity: .75;
    margin-top: 5px;
}

#chat-messages {

    flex: 1;

    overflow-y: auto;

    padding: 15px;

    background: #f9fafb;
}

.msg {

    max-width: 85%;

    margin-bottom: 10px;

    padding: 11px 14px;

    border-radius: 16px;

    white-space: pre-wrap;

    line-height: 1.45;

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

    border-top:
        1px solid #ddd;

    background: white;
}

#chat-input {

    flex: 1;

    border: none;

    outline: none;

    padding: 12px;

    font-size: 14px;
}

button {

    border: none;

    background: #111827;

    color: white;

    padding: 0 18px;

    border-radius: 10px;

    cursor: pointer;
}

@media (max-width: 600px) {

    #chat-container {

        position: fixed;

        inset: 0;

        width: 100%;

        height: 100%;

        border-radius: 0;
    }
}

</style>

</head>

<body>

<div id="chat-container">

<div id="chat-header">

<div class="name">
✂️ Asistente Virtual de Estilista Diego
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

    const input =
        document.getElementById(
            "chat-input"
        );

    if (input) {
        input.focus();
    }
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

    app.run(
        host="0.0.0.0",
        port=port,
        debug=(
            os.getenv("FLASK_ENV")
            == "development"
        )
    )
