import os
import re
import html
from datetime import datetime, timedelta
from threading import Lock

import pytz
from dotenv import load_dotenv
from flask import Flask, request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from twilio.twiml.messaging_response import MessagingResponse
from werkzeug.middleware.proxy_fix import ProxyFix


APP_VERSION = "2026-09-04-V33-DIEGO-AGENDA-DIRECCION-CONTACTO"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

ESTILISTA_NOMBRE = os.getenv("ESTILISTA_NOMBRE", "Diego")
NEGOCIO_NOMBRE = os.getenv("NEGOCIO_NOMBRE", "Estilista Diego")
TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DIRECCION_ATENCION = os.getenv("DIRECCION_ATENCION", "3 Poniente 382, Viña del Mar")
TELEFONO_EJECUTIVO = os.getenv("TELEFONO_EJECUTIVO", "+56966461436")

HORA_APERTURA = int(os.getenv("HORA_APERTURA", "10"))
HORA_CIERRE = int(os.getenv("HORA_CIERRE", "19"))
DURACION_RESERVA = int(os.getenv("DURACION_RESERVA", "60"))

# 0=lunes ... 5=sábado. Domingo cerrado.
DIAS_ATENCION = {0, 1, 2, 3, 4, 5}


def zona_local():
    return pytz.timezone(TIMEZONE)


def ahora_local():
    return datetime.now(zona_local())


def normalizar_texto(texto):
    texto = (texto or "").strip().lower()
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n",
    }
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    return texto


def normalizar_telefono(valor):
    valor = (valor or "").strip()
    return valor[len("whatsapp:"):] if valor.startswith("whatsapp:") else valor


# ============================================================
# SERVICIOS
# ============================================================

SERVICIOS = {
    "corte_hombre": {
        "numero": 1,
        "nombre": "Corte de cabello hombre",
        "precio": 17000,
        "precio_texto": "$17.000",
        "detalle": "Incluye perfilado de cejas, lavado de cabello y aplicación de producto.",
    },
    "perfilado_barba": {
        "numero": 2,
        "nombre": "Perfilado de barba",
        "precio": 10000,
        "precio_texto": "$10.000",
        "detalle": "",
    },
    "base_rizos": {
        "numero": 3,
        "nombre": "Base de rizos permanente",
        "precio": 65000,
        "precio_texto": "$65.000",
        "detalle": "",
    },
    "mechas_hombre": {
        "numero": 4,
        "nombre": "Mechas",
        "precio": 70000,
        "precio_texto": "desde $70.000",
        "detalle": "",
    },
    "decoloracion_global": {
        "numero": 5,
        "nombre": "Decoloración global",
        "precio": 120000,
        "precio_texto": "$120.000",
        "detalle": "",
    },
    "corte_mujer": {
        "numero": 6,
        "nombre": "Corte de cabello mujer",
        "precio": 30000,
        "precio_texto": "$30.000",
        "detalle": "Incluye lavado de cabello, hidratación y brushing.",
    },
    "masaje_hidratacion": {
        "numero": 7,
        "nombre": "Masaje de hidratación",
        "precio": 45000,
        "precio_texto": "$45.000",
        "detalle": "",
    },
    "botox_capilar": {
        "numero": 8,
        "nombre": "Botox capilar",
        "precio": 65000,
        "precio_texto": "desde $65.000",
        "detalle": "",
    },
    "alisado_permanente": {
        "numero": 9,
        "nombre": "Alisado permanente",
        "precio": 70000,
        "precio_texto": "desde $70.000",
        "detalle": "",
    },
    "retoque_raiz": {
        "numero": 10,
        "nombre": "Retoque de color de raíz",
        "precio": 50000,
        "precio_texto": "$50.000",
        "detalle": "",
    },
    "bano_color": {
        "numero": 11,
        "nombre": "Baño de color",
        "precio": 30000,
        "precio_texto": "$30.000",
        "detalle": "",
    },
    "diagnostico_balayage": {
        "numero": 12,
        "nombre": "Diagnóstico capilar gratuito para Balayage",
        "precio": 0,
        "precio_texto": "Diagnóstico gratuito · Balayage estimado desde $150.000",
        "detalle": "El valor final del Balayage se define después del diagnóstico capilar.",
    },
}

SERVICIO_POR_NUMERO = {v["numero"]: k for k, v in SERVICIOS.items()}


def mostrar_servicios():
    return (
        "Estos son los servicios de Diego 👇\n\n"
        "👨 HOMBRE\n"
        "1. Corte de cabello hombre — $17.000\n"
        "2. Perfilado de barba — $10.000\n"
        "3. Base de rizos permanente — $65.000\n"
        "4. Mechas — desde $70.000\n"
        "5. Decoloración global — $120.000\n\n"
        "👩 MUJER\n"
        "6. Corte de cabello mujer — $30.000\n"
        "7. Masaje de hidratación — $45.000\n"
        "8. Botox capilar — desde $65.000\n"
        "9. Alisado permanente — desde $70.000\n"
        "10. Retoque de color de raíz — $50.000\n"
        "11. Baño de color — $30.000\n"
        "12. Diagnóstico para Balayage — gratuito; Balayage desde $150.000\n\n"
        "Para agendar, responde con el número o nombre del servicio."
    )


def detectar_servicio(texto):
    t = normalizar_texto(texto)
    m = re.fullmatch(r"\s*(\d{1,2})\s*", t)
    if m:
        return SERVICIO_POR_NUMERO.get(int(m.group(1)))

    if "corte" in t and any(x in t for x in ("mujer", "dama", "femenino")):
        return "corte_mujer"
    if "corte" in t and any(x in t for x in ("hombre", "varon", "masculino")):
        return "corte_hombre"
    if "barba" in t:
        return "perfilado_barba"
    if "rizo" in t or "permanente" in t and "rizo" in t:
        return "base_rizos"
    if "mecha" in t:
        return "mechas_hombre"
    if "decolor" in t:
        return "decoloracion_global"
    if "masaje" in t and "hidrat" in t:
        return "masaje_hidratacion"
    if "botox" in t:
        return "botox_capilar"
    if "alisado" in t:
        return "alisado_permanente"
    if "retoque" in t and "raiz" in t:
        return "retoque_raiz"
    if "bano" in t and "color" in t:
        return "bano_color"
    if "balayage" in t:
        return "diagnostico_balayage"
    return None


def corte_ambiguo(texto):
    t = normalizar_texto(texto)
    return "corte" in t and detectar_servicio(texto) is None


# ============================================================
# GOOGLE CALENDAR
# ============================================================

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = os.getenv("GOOGLE_REFRESH_TOKEN")
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar"]


def google_credentials(scopes):
    if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN]):
        raise RuntimeError("Faltan credenciales de Google Calendar en Render")
    return Credentials(
        token=None,
        refresh_token=GOOGLE_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=scopes,
    )


def calendar_service():
    return build("calendar", "v3", credentials=google_credentials(CALENDAR_SCOPES), cache_discovery=False)


def es_dia_atencion(fecha):
    return fecha.astimezone(zona_local()).weekday() in DIAS_ATENCION


def eventos_ocupados(inicio_rango, fin_rango):
    service = calendar_service()
    data = service.events().list(
        calendarId=CALENDAR_ID,
        timeMin=inicio_rango.isoformat(),
        timeMax=fin_rango.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=500,
    ).execute()

    ocupados = []
    zona = zona_local()
    for ev in data.get("items", []):
        ini = (ev.get("start") or {}).get("dateTime")
        fin = (ev.get("end") or {}).get("dateTime")
        if ini and fin:
            try:
                di = datetime.fromisoformat(ini.replace("Z", "+00:00")).astimezone(zona)
                df = datetime.fromisoformat(fin.replace("Z", "+00:00")).astimezone(zona)
                ocupados.append((di, df))
            except Exception:
                pass
        elif (ev.get("start") or {}).get("date"):
            try:
                d = datetime.fromisoformat(ev["start"]["date"]).date()
                di = zona.localize(datetime.combine(d, datetime.min.time()))
                df = di + timedelta(days=1)
                ocupados.append((di, df))
            except Exception:
                pass
    return ocupados


def hora_libre(inicio, ocupados):
    fin = inicio + timedelta(minutes=DURACION_RESERVA)
    return all(not (inicio < ocupado_fin and fin > ocupado_ini) for ocupado_ini, ocupado_fin in ocupados)


def verificar_disponibilidad(inicio):
    inicio = inicio.astimezone(zona_local())
    if inicio <= ahora_local() or not es_dia_atencion(inicio):
        return False
    if inicio.minute != 0 or inicio.hour < HORA_APERTURA or inicio.hour >= HORA_CIERRE:
        return False
    fin = inicio + timedelta(minutes=DURACION_RESERVA)
    limite = inicio.replace(hour=HORA_CIERRE, minute=0, second=0, microsecond=0)
    if fin > limite:
        return False
    return hora_libre(inicio, eventos_ocupados(inicio, fin))


def buscar_proximas_horas(desde=None, limite=15):
    ahora = ahora_local()
    desde = (desde or ahora).astimezone(zona_local())
    if desde < ahora:
        desde = ahora

    inicio_rango = desde
    fin_rango = desde + timedelta(days=31)
    ocupados = eventos_ocupados(inicio_rango, fin_rango)
    resultados = []

    for offset in range(32):
        dia = (desde + timedelta(days=offset)).replace(hour=0, minute=0, second=0, microsecond=0)
        if not es_dia_atencion(dia):
            continue
        for h in range(HORA_APERTURA, HORA_CIERRE):
            slot = dia.replace(hour=h)
            if slot <= ahora or slot < desde:
                continue
            if hora_libre(slot, ocupados):
                resultados.append(slot)
                if len(resultados) >= limite:
                    return resultados
    return resultados


def buscar_horas_dia(fecha):
    zona = zona_local()
    fecha = fecha.astimezone(zona)
    inicio = fecha.replace(hour=0, minute=0, second=0, microsecond=0)
    if not es_dia_atencion(inicio):
        return []
    fin = inicio + timedelta(days=1)
    ocupados = eventos_ocupados(inicio, fin)
    ahora = ahora_local()
    out = []
    for h in range(HORA_APERTURA, HORA_CIERRE):
        slot = inicio.replace(hour=h)
        if slot > ahora and hora_libre(slot, ocupados):
            out.append(slot)
    return out


def crear_evento(inicio, servicio_codigo, nombre, telefono, correo):
    # Revalidación justo antes de reservar para evitar doble reserva.
    if not verificar_disponibilidad(inicio):
        return {"ok": False, "ocupada": True}

    servicio = SERVICIOS[servicio_codigo]
    fin = inicio + timedelta(minutes=DURACION_RESERVA)
    body = {
        "summary": f"{servicio['nombre']} - {nombre}",
        "description": (
            f"Reserva creada por el Asistente Virtual de {ESTILISTA_NOMBRE}.\n\n"
            f"Cliente: {nombre}\nTeléfono: {telefono}\nCorreo: {correo}\n"
            f"Servicio: {servicio['nombre']}\nValor referencial: {servicio['precio_texto']}\n"
            f"Duración: {DURACION_RESERVA} minutos\nOrigen: WhatsApp"
        ),
        "start": {"dateTime": inicio.isoformat(), "timeZone": TIMEZONE},
        "end": {"dateTime": fin.isoformat(), "timeZone": TIMEZONE},
        "attendees": [{"email": correo, "displayName": nombre}],
        "extendedProperties": {
            "private": {
                "telefono": telefono,
                "cliente": nombre,
                "correo": correo,
                "servicio_codigo": servicio_codigo,
                "origen": "whatsapp_asistente_diego",
            }
        },
    }
    try:
        resultado = calendar_service().events().insert(
            calendarId=CALENDAR_ID,
            body=body,
            sendUpdates="all",
        ).execute()
        return {"ok": True, "evento_id": resultado.get("id")}
    except Exception as e:
        print("GOOGLE CALENDAR CREAR EVENTO ERROR:", repr(e))
        return {"ok": False, "error": repr(e)}


# ============================================================
# FECHAS Y HORAS NATURALES
# ============================================================

DIAS_MAP = {
    "lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sabado": 5, "domingo": 6,
}
MESES_MAP = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def detectar_hora(texto):
    t = normalizar_texto(texto)
    m = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"\b(1[0-2]|[1-9])(?::([0-5]\d))?\s*(am|pm)\b", t)
    if m:
        h, mi, periodo = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if periodo == "pm" and h < 12:
            h += 12
        if periodo == "am" and h == 12:
            h = 0
        return h, mi
    m = re.search(r"\ba\s+las?\s+(\d{1,2})\b", t)
    if m:
        h = int(m.group(1))
        if 1 <= h <= 6:
            h += 12
        return h, 0
    return None


def detectar_fecha(texto, hora_data=None):
    t = normalizar_texto(texto)
    ahora = ahora_local()
    base = ahora.replace(hour=0, minute=0, second=0, microsecond=0)

    m = re.search(
        r"\b(?:el\s+)?([0-3]?\d)\s*(?:de\s+)?(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b",
        t,
    )
    if m:
        dia, mes = int(m.group(1)), MESES_MAP[m.group(2)]
        anio = ahora.year
        try:
            cand = zona_local().localize(datetime(anio, mes, dia))
            if cand.date() < ahora.date():
                cand = zona_local().localize(datetime(anio + 1, mes, dia))
            return cand
        except ValueError:
            return None

    if "pasado manana" in t:
        return base + timedelta(days=2)
    if "manana" in t:
        return base + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return base

    for nombre, weekday in DIAS_MAP.items():
        if re.search(rf"\b{nombre}\b", t):
            diferencia = (weekday - ahora.weekday()) % 7
            if diferencia == 0:
                # "lunes" hoy: si la hora pedida ya pasó, ir al siguiente lunes.
                if hora_data:
                    h, mi = hora_data
                    if ahora.replace(hour=h, minute=mi, second=0, microsecond=0) <= ahora:
                        diferencia = 7
                elif "proximo" in t:
                    diferencia = 7
            return base + timedelta(days=diferencia)
    return None


def fecha_hora_desde_texto(texto):
    h = detectar_hora(texto)
    f = detectar_fecha(texto, h)
    if f and h:
        return f.replace(hour=h[0], minute=h[1], second=0, microsecond=0)
    return None


def texto_menciona_fecha(texto):
    t = normalizar_texto(texto)
    claves = ["hoy", "manana", "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    claves += list(MESES_MAP.keys())
    return any(re.search(rf"\b{re.escape(x)}\b", t) for x in claves)


def formatear_fecha(fecha):
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    f = fecha.astimezone(zona_local())
    return f"{dias[f.weekday()]} {f.day} de {meses[f.month-1]} a las {f.strftime('%H:%M')}"


def listar_horas(horas):
    if not horas:
        return "No encontré horas disponibles para esa fecha."
    return "\n".join(f"{i}. {formatear_fecha(h)}" for i, h in enumerate(horas, 1))


# ============================================================
# GOOGLE SHEETS OPCIONAL - LOG DE CONVERSACIONES
# ============================================================

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Conversaciones")
SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEETS_LOCK = Lock()
SHEETS_READY = False


def guardar_mensaje(telefono, rol, mensaje):
    global SHEETS_READY
    if not GOOGLE_SHEET_ID:
        return
    try:
        svc = build("sheets", "v4", credentials=google_credentials(SHEETS_SCOPES), cache_discovery=False)
        with SHEETS_LOCK:
            if not SHEETS_READY:
                meta = svc.spreadsheets().get(
                    spreadsheetId=GOOGLE_SHEET_ID, fields="sheets.properties.title"
                ).execute()
                titulos = [x.get("properties", {}).get("title") for x in meta.get("sheets", [])]
                if GOOGLE_SHEET_TAB not in titulos:
                    svc.spreadsheets().batchUpdate(
                        spreadsheetId=GOOGLE_SHEET_ID,
                        body={"requests": [{"addSheet": {"properties": {"title": GOOGLE_SHEET_TAB}}}]},
                    ).execute()
                    svc.spreadsheets().values().update(
                        spreadsheetId=GOOGLE_SHEET_ID,
                        range=f"'{GOOGLE_SHEET_TAB}'!A1:F1",
                        valueInputOption="RAW",
                        body={"values": [["FechaHora", "Canal", "ClienteID", "Telefono", "Rol", "Mensaje"]]},
                    ).execute()
                SHEETS_READY = True
            svc.spreadsheets().values().append(
                spreadsheetId=GOOGLE_SHEET_ID,
                range=f"'{GOOGLE_SHEET_TAB}'!A:F",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [[ahora_local().isoformat(), "whatsapp", telefono, normalizar_telefono(telefono), rol, mensaje]]},
            ).execute()
    except Exception as e:
        print("GOOGLE SHEETS LOG ERROR:", repr(e))


# ============================================================
# OPENAI OPCIONAL: RESPUESTA LIBRE, PERO SOLO DENTRO DEL NEGOCIO
# ============================================================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def respuesta_general(texto):
    base = (
        f"Soy el asistente virtual de {ESTILISTA_NOMBRE} 😊. "
        "Estoy aquí para ayudarte con sus servicios, precios, horarios disponibles y para agendar una hora."
    )
    if not openai_client:
        return base + "\n\nPuedes preguntarme por un servicio o escribir *AGENDAR* para reservar."

    contexto_servicios = "; ".join(
        f"{s['nombre']}: {s['precio_texto']}" for s in SERVICIOS.values()
    )
    system = f"""
Eres el asistente virtual de {ESTILISTA_NOMBRE}, estilista en Chile.
Tu única función es ayudar a clientes con servicios, precios, horarios y reservas.
Dirección de atención: {DIRECCION_ATENCION}.
Si el cliente quiere hablar con Diego, con un ejecutivo o con una persona, indícale este teléfono: {TELEFONO_EJECUTIVO}.
Horario: lunes a sábado, de {HORA_APERTURA}:00 a {HORA_CIERRE}:00.
Servicios: {contexto_servicios}.
No inventes información. No hables de sistemas internos, APIs ni código.
Si el usuario escribe algo fuera de este ámbito, responde amablemente que eres el asistente de Diego y que puedes ayudar con servicios, precios, disponibilidad o agendar.
Mantén la respuesta breve y en español de Chile.
"""
    try:
        r = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": texto}],
        )
        return (r.choices[0].message.content or "").strip() or base
    except Exception as e:
        print("OPENAI FALLBACK ERROR:", repr(e))
        return base + "\n\nPuedes preguntarme por un servicio o escribir *AGENDAR* para reservar."


# ============================================================
# ESTADO WHATSAPP
# ============================================================

SESIONES = {}
SESIONES_LOCK = Lock()
PROCESADOS = {}
PROCESADOS_LOCK = Lock()


def estado_inicial(telefono):
    return {
        "telefono": telefono,
        "paso": "inicio",
        "servicio": None,
        "fecha_hora": None,
        "horas_ofrecidas": [],
        "nombre": None,
        "correo": None,
    }


def get_estado(telefono):
    with SESIONES_LOCK:
        if telefono not in SESIONES:
            SESIONES[telefono] = estado_inicial(telefono)
        return SESIONES[telefono]


def reset_estado(telefono):
    with SESIONES_LOCK:
        SESIONES[telefono] = estado_inicial(telefono)
        return SESIONES[telefono]


def mensaje_bienvenida():
    return (
        f"¡Hola! 👋 Soy el asistente virtual de {ESTILISTA_NOMBRE}.\n\n"
        "Estoy aquí para ayudarte con sus servicios, precios, horarios disponibles y para agendar tu hora 📅.\n\n"
        "Puedes escribirme de forma natural, por ejemplo:\n"
        "• Quiero agendar un corte de hombre mañana\n"
        "• ¿Qué servicios tienes?\n"
        "• ¿Tienes hora el viernes?"
    )


def pedir_servicio():
    return "Claro 😊 ¿Qué servicio quieres agendar?\n\n" + mostrar_servicios()


def intencion_agendar(texto):
    t = normalizar_texto(texto)
    return any(x in t for x in (
        "agendar", "reservar", "reserva", "hora", "cita", "turno",
        "disponibilidad", "quiero cortarme", "quiero un corte", "sacar hora",
    ))


def pregunta_servicios(texto):
    t = normalizar_texto(texto)
    return any(x in t for x in ("servicio", "servicios", "precio", "precios", "cuanto", "valor", "valores", "que haces"))


def quiere_hablar_con_persona(texto):
    t = normalizar_texto(texto)
    frases = (
        "hablar con diego", "hablar con una persona", "hablar con persona",
        "hablar con ejecutivo", "hablar con un ejecutivo", "hablar con alguien",
        "quiero hablar con diego", "quiero hablar con una persona",
        "quiero hablar con un ejecutivo", "contactar a diego", "contacto diego",
        "ejecutivo", "persona real", "humano", "asesor",
    )
    return any(x in t for x in frases)


def mensaje_contacto_persona():
    return (
        f"Claro 😊 Si quieres hablar directamente con {ESTILISTA_NOMBRE} o con una persona, "
        f"puedes comunicarte al *{TELEFONO_EJECUTIVO}*."
    )


def es_menu(texto):
    return normalizar_texto(texto) in {"menu", "inicio", "reiniciar", "empezar de nuevo", "hola"}


def es_cancelar(texto):
    return normalizar_texto(texto) in {"cancelar", "salir", "no", "no gracias", "chao"}


def email_valido(texto):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", (texto or "").strip()))


def procesar_agenda(estado, texto):
    t = normalizar_texto(texto)

    if es_cancelar(texto):
        telefono = estado["telefono"]
        reset_estado(telefono)
        return "No hay problema 😊. Cuando quieras agendar una hora con Diego, escríbeme nuevamente."

    # 1) SERVICIO
    if estado["paso"] in {"inicio", "servicio"}:
        if corte_ambiguo(texto):
            estado["paso"] = "servicio"
            return "Perfecto. ¿El corte es para *hombre* o *mujer*?"

        servicio = detectar_servicio(texto)
        if servicio:
            estado["servicio"] = servicio
            estado["paso"] = "fecha"

            # Si el mismo mensaje trae fecha y hora, aprovecharla.
            solicitada = fecha_hora_desde_texto(texto)
            if solicitada:
                if verificar_disponibilidad(solicitada):
                    estado["fecha_hora"] = solicitada.isoformat()
                    estado["paso"] = "nombre"
                    return f"Perfecto ✅ Tengo disponible {formatear_fecha(solicitada)}. ¿Cuál es tu nombre y apellido?"
                return "Esa hora no está disponible. Dime otro día/hora o escribe *PRÓXIMAS HORAS*."

            # Si trae solo fecha, listar ese día.
            fecha = detectar_fecha(texto)
            if fecha:
                horas = buscar_horas_dia(fecha)
                estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
                estado["paso"] = "seleccionar_hora"
                if not horas:
                    return "Ese día no tiene horas disponibles. Dime otra fecha o escribe *PRÓXIMAS HORAS*."
                return f"Estas son las horas disponibles para ese día 👇\n\n{listar_horas(horas)}\n\nResponde con el número de la hora que prefieres."

            return (
                f"Perfecto 👍 Servicio: *{SERVICIOS[servicio]['nombre']}* ({SERVICIOS[servicio]['precio_texto']}).\n\n"
                "¿Qué día te gustaría venir? Puedes escribir, por ejemplo, *mañana*, *viernes* o *12 de septiembre*."
            )

        estado["paso"] = "servicio"
        return pedir_servicio()

    # 2) FECHA
    if estado["paso"] == "fecha":
        if "proxima" in t or "disponible" in t or "cuando" in t:
            horas = buscar_proximas_horas()
        else:
            solicitada = fecha_hora_desde_texto(texto)
            if solicitada:
                if verificar_disponibilidad(solicitada):
                    estado["fecha_hora"] = solicitada.isoformat()
                    estado["paso"] = "nombre"
                    return f"Perfecto ✅ Tengo disponible {formatear_fecha(solicitada)}. ¿Cuál es tu nombre y apellido?"
                return "Esa hora no está disponible. Dime otra hora o escribe *PRÓXIMAS HORAS*."
            fecha = detectar_fecha(texto)
            horas = buscar_horas_dia(fecha) if fecha else buscar_proximas_horas()

        estado["horas_ofrecidas"] = [h.isoformat() for h in horas]
        estado["paso"] = "seleccionar_hora"
        if not horas:
            estado["paso"] = "fecha"
            return "No encontré horas disponibles. Dime otra fecha, por favor."
        return f"Tengo estas horas disponibles 👇\n\n{listar_horas(horas)}\n\nResponde con el número de la hora que prefieres."

    # 3) ELEGIR HORA DE LISTA
    if estado["paso"] == "seleccionar_hora":
        m = re.fullmatch(r"\s*(\d{1,2})\s*", t)
        ofrecidas = estado.get("horas_ofrecidas") or []
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(ofrecidas):
                slot = datetime.fromisoformat(ofrecidas[idx])
                if verificar_disponibilidad(slot):
                    estado["fecha_hora"] = slot.isoformat()
                    estado["paso"] = "nombre"
                    return f"Excelente ✅ {formatear_fecha(slot)}. ¿Cuál es tu nombre y apellido?"
                estado["paso"] = "fecha"
                return "Esa hora acaba de ocuparse. Dime otra fecha y te muestro nuevas opciones."

        solicitada = fecha_hora_desde_texto(texto)
        if solicitada and verificar_disponibilidad(solicitada):
            estado["fecha_hora"] = solicitada.isoformat()
            estado["paso"] = "nombre"
            return f"Excelente ✅ {formatear_fecha(solicitada)}. ¿Cuál es tu nombre y apellido?"
        return "Elige una de las horas escribiendo su número, o dime otra fecha."

    # 4) NOMBRE
    if estado["paso"] == "nombre":
        if len((texto or "").strip()) < 2 or email_valido(texto):
            return "Indícame tu nombre y apellido, por favor."
        estado["nombre"] = (texto or "").strip()[:120]
        estado["paso"] = "correo"
        return "Gracias 😊 ¿Cuál es tu correo electrónico? Lo usaremos para enviarte la invitación de la reserva."

    # 5) CORREO
    if estado["paso"] == "correo":
        correo = (texto or "").strip().lower()
        if not email_valido(correo):
            return "Ese correo no parece válido. Escríbelo nuevamente, por ejemplo: nombre@correo.cl"
        estado["correo"] = correo
        estado["paso"] = "confirmar"
        servicio = SERVICIOS[estado["servicio"]]
        fecha = datetime.fromisoformat(estado["fecha_hora"])
        return (
            "Confirma tu reserva 👇\n\n"
            f"✂️ Servicio: {servicio['nombre']}\n"
            f"💰 Valor: {servicio['precio_texto']}\n"
            f"📅 {formatear_fecha(fecha)}\n"
            f"👤 {estado['nombre']}\n"
            f"📧 {estado['correo']}\n"
            f"📍 {DIRECCION_ATENCION}\n\n"
            "Si todo está correcto, escribe *CONFIRMAR*."
        )

    # 6) CONFIRMAR -> RESERVA INMEDIATA, SIN PAGO
    if estado["paso"] == "confirmar":
        if t not in {"confirmar", "confirmo", "si", "sí", "ok"}:
            return "Para crear la reserva escribe *CONFIRMAR*. Si quieres salir, escribe *CANCELAR*."

        inicio = datetime.fromisoformat(estado["fecha_hora"])
        resultado = crear_evento(
            inicio=inicio,
            servicio_codigo=estado["servicio"],
            nombre=estado["nombre"],
            telefono=normalizar_telefono(estado["telefono"]),
            correo=estado["correo"],
        )
        if resultado.get("ocupada"):
            estado["paso"] = "fecha"
            estado["fecha_hora"] = None
            return "Esa hora acaba de ocuparse. Dime otra fecha y te muestro nuevas opciones."
        if not resultado.get("ok"):
            return "Tuve un problema creando la reserva en Calendar. Intenta nuevamente en unos segundos."

        servicio = SERVICIOS[estado["servicio"]]
        nombre = estado["nombre"]
        correo = estado["correo"]
        fecha_txt = formatear_fecha(inicio)
        telefono = estado["telefono"]
        reset_estado(telefono)
        return (
            "✅ *¡Reserva confirmada!*\n\n"
            f"✂️ Servicio: {servicio['nombre']}\n"
            f"💰 Valor: {servicio['precio_texto']}\n"
            f"📅 {fecha_txt}\n"
            f"👤 {nombre}\n"
            f"📧 {correo}\n"
            f"📍 {DIRECCION_ATENCION}\n"
            f"⏱️ Duración: {DURACION_RESERVA} minutos\n\n"
            "No necesitas realizar ningún pago para agendar. ¡Te esperamos! 😊"
        )

    estado["paso"] = "inicio"
    return mensaje_bienvenida()


# ============================================================
# TWILIO WHATSAPP WEBHOOK
# ============================================================

@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    twiml = MessagingResponse()
    try:
        telefono = (request.form.get("From") or "").strip()
        texto = (request.form.get("Body") or "").strip()
        message_id = (request.form.get("MessageSid") or "").strip()

        print("=" * 60)
        print("TWILIO WEBHOOK")
        print("From:", telefono)
        print("Body:", texto)
        print("MessageSid:", message_id)
        print("=" * 60)

        # Evita respuestas duplicadas ante reintentos de Twilio.
        if message_id:
            with PROCESADOS_LOCK:
                ahora_ts = datetime.now().timestamp()
                viejos = [k for k, ts in PROCESADOS.items() if ahora_ts - ts > 300]
                for k in viejos:
                    PROCESADOS.pop(k, None)
                if message_id in PROCESADOS:
                    return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}
                PROCESADOS[message_id] = ahora_ts

        if not telefono:
            return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

        if not texto:
            respuesta = mensaje_bienvenida()
        else:
            guardar_mensaje(telefono, "user", texto)
            estado = get_estado(telefono)

            if quiere_hablar_con_persona(texto):
                respuesta = mensaje_contacto_persona()
            elif es_menu(texto):
                reset_estado(telefono)
                respuesta = mensaje_bienvenida()
            elif estado.get("paso") != "inicio":
                respuesta = procesar_agenda(estado, texto)
            elif pregunta_servicios(texto):
                respuesta = mostrar_servicios()
            elif detectar_servicio(texto) or corte_ambiguo(texto) or intencion_agendar(texto) or texto_menciona_fecha(texto):
                estado["paso"] = "inicio"
                respuesta = procesar_agenda(estado, texto)
            else:
                # Cualquier otra cosa recibe una respuesta natural pero acotada al rol.
                respuesta = respuesta_general(texto)

        guardar_mensaje(telefono, "assistant", respuesta)
        twiml.message(respuesta)
        return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}

    except Exception as e:
        print("WHATSAPP ERROR:", repr(e))
        import traceback
        print(traceback.format_exc())
        twiml.message(
            f"Disculpa 🙏 Soy el asistente virtual de {ESTILISTA_NOMBRE}. "
            "Tuve un problema técnico. Intenta nuevamente en unos segundos; puedo ayudarte a revisar servicios, horarios y agendar tu hora."
        )
        return str(twiml), 200, {"Content-Type": "application/xml; charset=utf-8"}


# ============================================================
# HEALTHCHECK
# ============================================================

@app.route("/")
def health():
    return {
        "ok": True,
        "app": "Asistente Virtual Estilista Diego",
        "version": APP_VERSION,
        "channel": "Twilio WhatsApp",
        "calendar": "Google Calendar",
        "payments": "disabled",
    }, 200


if __name__ == "__main__":
    print("APP_VERSION:", APP_VERSION)
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
