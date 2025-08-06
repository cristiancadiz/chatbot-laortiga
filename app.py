import os
from flask import Flask, render_template_string, request, session, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("app.secret_key", "default_secret")

# Configuración cuenta de servicio desde variables de entorno
SERVICE_ACCOUNT_INFO = {
    "type": os.environ.get("SA_TYPE"),
    "project_id": os.environ.get("SA_PROJECT_ID"),
    "private_key_id": os.environ.get("SA_PRIVATE_KEY_ID"),
    "private_key": os.environ.get("SA_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": os.environ.get("SA_CLIENT_EMAIL"),
    "client_id": os.environ.get("SA_CLIENT_ID"),
    "auth_uri": os.environ.get("SA_AUTH_URI"),
    "token_uri": os.environ.get("SA_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.environ.get("SA_AUTH_CERT_URL"),
    "client_x509_cert_url": os.environ.get("SA_CLIENT_CERT_URL"),
    "universe_domain": "googleapis.com"
}

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

credentials = service_account.Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO, scopes=SCOPES
)
service = build("calendar", "v3", credentials=credentials)

# Plantilla única con chat conversacional
CHAT_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Chat LaOrtiga</title>
<style>
body { font-family: Arial, sans-serif; margin: 40px; }
#chat { max-width: 500px; margin: auto; }
input, button { padding: 8px; margin: 5px 0; width: 100%; }
.message { margin: 5px 0; }
.bot { color: green; }
.user { color: blue; text-align: right; }
</style>
</head>
<body>
<h2>Chat de Agendamiento - LaOrtiga</h2>
<div id="chat"></div>
<input type="text" id="user_input" placeholder="Escribe tu respuesta aquí...">
<button onclick="sendMessage()">Enviar</button>

<script>
let step = 0;
let chatDiv = document.getElementById('chat');
let conversation = [];

function addMessage(text, sender) {
    conversation.push({text, sender});
    let div = document.createElement('div');
    div.className = 'message ' + sender;
    div.innerText = text;
    chatDiv.appendChild(div);
    chatDiv.scrollTop = chatDiv.scrollHeight;
}

function sendMessage() {
    let input = document.getElementById('user_input');
    let text = input.value.trim();
    if(!text) return;
    addMessage(text, 'user');
    input.value = '';
    fetch('/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, step})
    })
    .then(res => res.json())
    .then(data => {
        addMessage(data.reply, 'bot');
        step = data.next_step;
    });
}

// Mensaje inicial
addMessage("¡Hola! 👋 Bienvenido a LaOrtiga. ¿Para qué día y hora quieres agendar tu cita? (ej: 2025-08-07 12:00)", 'bot');
</script>
</body>
</html>
"""

@app.route("/")
def index():
    session.clear()
    return render_template_string(CHAT_TEMPLATE)

@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("text")
    step = request.json.get("step", 0)

    if "conversation" not in session:
        session["conversation"] = {}
    
    reply = ""
    next_step = step

    try:
        if step == 0:
            # Guardar fecha y hora
            session["conversation"]["datetime_str"] = user_input
            reply = "Perfecto, ahora indícame el correo electrónico del invitado."
            next_step = 1
        elif step == 1:
            session["conversation"]["email"] = user_input
            reply = "¿Deseas agregar una descripción para la cita? (opcional)"
            next_step = 2
        elif step == 2:
            session["conversation"]["description"] = user_input or "Cita agendada desde LaOrtiga"
            # Crear evento en Google Calendar
            dt_str = session["conversation"]["datetime_str"]
            email = session["conversation"]["email"]
            description = session["conversation"]["description"]
            start_time = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            end_time = start_time.replace(hour=start_time.hour+1)
            event = {
                "summary": description,
                "start": {"dateTime": start_time.isoformat(), "timeZone": "America/Santiago"},
                "end": {"dateTime": end_time.isoformat(), "timeZone": "America/Santiago"},
                "attendees": [{"email": email}],
            }
            service.events().insert(calendarId=CALENDAR_ID, body=event, sendUpdates="all").execute()
            reply = f"✅ Invitación enviada a {email} para {dt_str}. ¡Listo!"
            next_step = 3
        else:
            reply = "Si des
