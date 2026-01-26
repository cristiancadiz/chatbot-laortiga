import os
import time
import requests
from flask import Flask, redirect, url_for, session, request, render_template_string, abort
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as grequests
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from datetime import timedelta, datetime
import openai
from dotenv import load_dotenv
import dateparser
import pytz

load_dotenv()

app = Flask(__name__)

# =========================
# ✅ Config básica + seguridad
# =========================
app.secret_key = os.getenv("SECRET_KEY")
if not app.secret_key:
    raise Exception("La variable de entorno SECRET_KEY no está configurada.")

app.permanent_session_lifetime = timedelta(days=30)

# Solo permitir insecure transport en desarrollo local
if os.getenv("FLASK_ENV") == "development":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

# =========================
# OpenAI
# =========================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise Exception("La variable de entorno OPENAI_API_KEY no está configurada.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

# =========================
# Google OAuth + Calendar
# =========================
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    raise Exception("Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET en variables de entorno.")

REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "https://chatbot-laortiga-9.onrender.com/callback")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.events",
]

flow = Flow.from_client_config(
    {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
            "scopes": SCOPES,
        }
    },
    scopes=SCOPES,
    redirect_uri=REDIRECT_URI,
)

# =========================
# WhatsApp Cloud API (TEST)
# =========================
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")  # ej: 884166571456836
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID or not WHATSAPP_VERIFY_TOKEN:
    print("⚠️ Faltan variables WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID / WHATSAPP_VERIFY_TOKEN.")

# Sesiones simples en memoria para WhatsApp (en Render se reinicia si redeploy)
WA_SESSIONS = {}

# Deduplicación simple (en memoria) por message_id
PROCESSED_MSG_IDS = {}
DEDUP_TTL_SECONDS = 120


def _dedup_seen(msg_id: str) -> bool:
    """Retorna True si ya procesamos ese message_id recientemente."""
    if not msg_id:
        return False
    now = time.time()
    # limpia antiguos
    for k in list(PROCESSED_MSG_IDS.keys()):
        if now - PROCESSED_MSG_IDS[k] > DEDUP_TTL_SECONDS:
            del PROCESSED_MSG_IDS[k]
    if msg_id in PROCESSED_MSG_IDS:
        return True
    PROCESSED_MSG_IDS[msg_id] = now
    return False


def wa_send_text(to_number: str, text: str):
    """
    Envía un mensaje por WhatsApp Cloud API.
    En modo prueba: solo llega a recipients permitidos.
    """
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        print("❌ WhatsApp no configurado (token o phone_number_id faltante).")
        return None

    url = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": (text or "")[:3900]},
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        print("📤 WA SEND:", r.status_code, r.text[:500])
        if r.status_code >= 300:
            print("❌ Error WA:", r.status_code, r.text)
        return r
    except Exception as e:
        print("❌ Exception WA SEND:", str(e))
        return None


def get_wa_session(wa_id: str):
    if wa_id not in WA_SESSIONS:
        WA_SESSIONS[wa_id] = {
            "historial": [
                {"role": "assistant", "content": "¡Hola! 👋 Soy Capitán Planeta de LaOrtiga.cl 🌱 ¿En qué te ayudo?"}
            ],
            "modo_agendar": False,
        }
    return WA_SESSIONS[wa_id]


# =========================
# Utilidades
# =========================
def guardar_historial_en_archivo(historial):
    """
    Nota: en Render el filesystem es efímero; esto sirve solo para debugging.
    """
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    try:
        with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
            for m in historial:
                rol = "Tú" if m["role"] == "user" else "Bot"
                f.write(f"{rol}: {m['content']}\n\n")
    except Exception as e:
        print("⚠️ No pude guardar historial:", str(e))


def es_intencion_agendar(texto: str) -> bool:
    t = (texto or "").lower()
    return any(k in t for k in ["agendar", "agenda", "reserva", "reservar", "cita", "calendar", "calendario"])


def parse_fecha_hora(texto: str):
    return dateparser.parse(
        texto,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "America/Santiago",
            "TO_TIMEZONE": "America/Santiago",
            "RELATIVE_BASE": datetime.now(pytz.timezone("America/Santiago")),
        },
    )


def crear_evento_google_calendar(fecha_hora_texto: str):
    if "credentials" not in session:
        return "No tengo permisos para acceder a tu calendario."

    creds = Credentials(**session["credentials"])
    service = build("calendar", "v3", credentials=creds)

    inicio = parse_fecha_hora(fecha_hora_texto)
    if not inicio:
        return "⚠️ No pude entender la fecha/hora. Ej: 'mañana a las 10' o 'el jueves a las 16:00'."

    fin = inicio + timedelta(minutes=30)

    evento_body = {
        "summary": "Consulta con LaOrtiga.cl",
        "description": "Reserva automatizada con Capitán Planeta 🌱",
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Santiago"},
    }

    evento = service.events().insert(calendarId="primary", body=evento_body).execute()
    link = evento.get("htmlLink", "")
    if link:
        return f"✅ Listo. Evento creado. Link: {link}"
    return "✅ Listo. Evento creado."


def responder_openai(historial, pregunta: str) -> str:
    try:
        mensajes = [
            {
                "role": "system",
                "content": (
                    "Eres un asistente conversacional de LaOrtiga.cl. "
                    "Habla de forma amable, cercana y profesional (español de Chile). "
                    "Solo responde preguntas sobre sostenibilidad, productos ecológicos o emprendimiento verde."
                ),
            }
        ] + (historial[-10:] if historial else [])

        mensajes.append({"role": "user", "content": pregunta})

        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=mensajes,
            max_tokens=240,
            temperature=0.7,
        )
        return (completion.choices[0].message.content or "").strip()
    except Exception as e:
        print("❌ OpenAI error:", str(e))
        return "Ups, tuve un problema técnico 😅. ¿Me lo repites?"


# =========================
# Rutas WEB (chat + login)
# =========================
@app.route("/")
def home():
    if "historial" not in session:
        session["historial"] = [
            {"role": "assistant", "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿En qué puedo ayudarte hoy?"}
        ]
    if "modo_agendar" not in session:
        session["modo_agendar"] = False
    return redirect(url_for("chat"))


@app.route("/login")
def login():
    authorization_url, state = flow.authorization_url(
        include_granted_scopes="true",
        access_type="offline",
        prompt="consent",
    )
    session["state"] = state
    return redirect(authorization_url)


@app.route("/callback")
def callback():
    if "state" not in session:
        abort(400)

    flow.fetch_token(authorization_response=request.url)

    if not flow.credentials:
        return "No se pudo autenticar con Google.", 400

    credentials = flow.credentials

    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

    request_session = grequests.Request()
    idinfo = {}
    try:
        # más robusto que _id_token
        tok = getattr(credentials, "id_token", None) or getattr(credentials, "_id_token", None)
        if tok:
            idinfo = id_token.verify_oauth2_token(tok, request_session, GOOGLE_CLIENT_ID)
    except Exception as e:
        print("⚠️ No pude verificar id_token:", str(e))
        idinfo = {}

    session["google_id"] = idinfo.get("sub")
    session["email"] = idinfo.get("email")
    session["name"] = idinfo.get("name")
    session.permanent = True

    return redirect(url_for("chat"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/chat", methods=["GET", "POST"])
def chat():
    if "historial" not in session:
        session["historial"] = [
            {"role": "assistant", "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿En qué puedo ayudarte hoy?"}
        ]
    if "modo_agendar" not in session:
        session["modo_agendar"] = False

    respuesta = ""

    if request.method == "POST":
        pregunta = (request.form.get("pregunta") or "").strip()
        if pregunta:
            session["historial"].append({"role": "user", "content": pregunta})

            if es_intencion_agendar(pregunta):
                if "credentials" not in session:
                    respuesta = "Para agendar necesito que te autentiques con Google. Haz click acá: /login"
                else:
                    session["modo_agendar"] = True
                    respuesta = "Perfecto. ¿Para qué día y hora quieres agendar? (ej: 'mañana a las 10')"

            elif session.get("modo_agendar") and "credentials" in session:
                inicio = parse_fecha_hora(pregunta)
                if not inicio:
                    respuesta = "⚠️ No pude entender la fecha/hora. Prueba: 'mañana a las 10' / 'jueves 16:00'."
                else:
                    respuesta = crear_evento_google_calendar(pregunta)
                    session["modo_agendar"] = False

            else:
                respuesta = responder_openai(session["historial"], pregunta)

            session["historial"].append({"role": "assistant", "content": respuesta})
            guardar_historial_en_archivo(session["historial"])

    return render_template_string(TEMPLATE, historial=session["historial"], user_name=session.get("name"))


# =========================
# Rutas WhatsApp Webhook
# =========================
@app.route("/whatsapp/webhook", methods=["GET"])
def whatsapp_verify():
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    data = request.get_json(silent=True) or {}
    print("📩 WA IN RAW:", str(data)[:800])

    try:
        entry = (data.get("entry") or [])[0]
        changes = (entry.get("changes") or [])[0]
        value = changes.get("value") or {}

        # Si es status update, no respondemos
        if value.get("statuses"):
            print("🟡 WA: status update (no reply)")
            return "ok", 200

        messages = value.get("messages") or []
        if not messages:
            print("🟡 WA: no messages")
            return "ok", 200

        msg = messages[0]
        msg_id = msg.get("id")
        wa_id = msg.get("from")
        text = (msg.get("text") or {}).get("body", "").strip()

        print("📩 WA IN PARSED:", {"id": msg_id, "from": wa_id, "text": text})

        # Dedup por message id (Meta puede reenviar)
        if _dedup_seen(msg_id):
            print("🟡 WA: duplicate message_id, ignored:", msg_id)
            return "ok", 200

        if not wa_id or not text:
            return "ok", 200

        s = get_wa_session(wa_id)
        s["historial"].append({"role": "user", "content": text})

        if es_intencion_agendar(text):
            respuesta = (
                "Para agendar en tu Google Calendar necesito que inicies sesión desde el chat web.\n"
                "Entra a: https://chatbot-laortiga-9.onrender.com\n"
                "y escribe: agendar"
            )
        else:
            respuesta = responder_openai(s["historial"], text)

        s["historial"].append({"role": "assistant", "content": respuesta})
        wa_send_text(wa_id, respuesta)

    except Exception as e:
        print("❌ Error webhook WA:", str(e))

    return "ok", 200


# =========================
# Template (escape)
# =========================
TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <title>Asistente La Ortiga</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet" />
  <style>
    body { font-family: 'Inter', sans-serif; background: #f4f9f4; margin: 0; padding: 0; }
    #chat-toggle-btn {
      position: fixed; bottom: 20px; right: 20px; font-size: 2rem;
      background: #2c7a2c; color: white; border: none; border-radius: 50%;
      width: 60px; height: 60px; cursor: pointer;
    }
    #chat-container {
      position: fixed; bottom: 90px; right: 20px; width: 350px; max-height: 500px;
      background: white; border-radius: 10px; box-shadow: 0 0 12px rgba(0,0,0,0.1);
      display: flex; flex-direction: column;
    }
    #chat-header {
      display: flex; align-items: center; padding: 10px; background: #2c7a2c;
      color: white; border-top-left-radius: 10px; border-top-right-radius: 10px;
    }
    #chat-header img { width: 40px; height: 40px; margin-right: 10px; }
    .name { font-weight: 600; }
    #chat-messages { flex-grow: 1; padding: 10px; overflow-y: auto; background: #eaf3ea; }
    .msg { margin-bottom: 10px; padding: 8px 12px; border-radius: 20px; max-width: 80%;
      word-wrap: break-word; white-space: pre-wrap; }
    .bot { background: #2c7a2c; color: white; align-self: flex-start; }
    .user { background: #a3d1a3; color: #000; align-self: flex-end; }
    #chat-input-form { display: flex; border-top: 1px solid #ccc; }
    #chat-input { flex-grow: 1; border: none; padding: 10px; font-size: 1rem; border-bottom-left-radius: 10px; }
    #chat-send { border: none; background: #2c7a2c; color: white; padding: 0 20px; cursor: pointer;
      font-size: 1.2rem; border-bottom-right-radius: 10px; }
  </style>
</head>
<body>
  <button id="chat-toggle-btn">💬</button>

  <div id="chat-container" style="display:flex;">
    <div id="chat-header">
      <img src="https://cdn-icons-png.flaticon.com/512/194/194938.png" alt="Asistente" />
      <div>
        <div class="name">Capitán Planeta</div>
        <small style="font-size:12px;">Conectado como {{ user_name or 'Invitado' }}</small>
      </div>
    </div>

    <div id="chat-messages">
      {% for m in historial %}
        <div class="msg {% if m['role'] == 'user' %}user{% else %}bot{% endif %}">{{ m['content'] | e }}</div>
      {% endfor %}
    </div>

    <form id="chat-input-form" method="POST">
      <input type="text" id="chat-input" name="pregunta" placeholder="Escribe tu mensaje..." autocomplete="off" required />
      <button id="chat-send">➤</button>
    </form>
  </div>

  <script>
    const toggleBtn = document.getElementById('chat-toggle-btn');
    const chatBox = document.getElementById('chat-container');
    const chatMessages = document.getElementById('chat-messages');
    const input = document.getElementById('chat-input');

    toggleBtn.onclick = () => {
      if (chatBox.style.display === 'none') {
        chatBox.style.display = 'flex';
        scrollToBottom();
        input.focus();
      } else {
        chatBox.style.display = 'none';
      }
    };

    function scrollToBottom() {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    }

    window.onload = () => {
      chatBox.style.display = 'flex';
      scrollToBottom();
    };
  </script>
</body>
</html>
"""

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.getenv("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
