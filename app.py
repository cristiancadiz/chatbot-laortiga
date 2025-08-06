import os
from flask import Flask, request, render_template_string, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz

# Configuración de Flask
app = Flask(__name__)
app.secret_key = os.environ.get("app.secret_key", "default_secret")

# Google Calendar: configuración con cuenta de servicio
SERVICE_ACCOUNT_INFO = {
    "type": "service_account",
    "project_id": os.environ.get("GOOGLE_PROJECT_ID"),
    "private_key_id": os.environ.get("GOOGLE_PRIVATE_KEY_ID"),
    "private_key": os.environ.get("GOOGLE_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": os.environ.get("GOOGLE_CLIENT_EMAIL"),
    "client_id": os.environ.get("GOOGLE_CLIENT_ID_SERVICE"),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": os.environ.get("GOOGLE_CLIENT_X509_CERT_URL")
}

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
credentials = service_account.Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO, scopes=SCOPES
)
CALENDAR_ID = os.environ.get("GOOGLE_CALENDAR_ID")

service = build("calendar", "v3", credentials=credentials)

# HTML template del chat
CHAT_TEMPLATE = """
<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <title>Chatbot LaOrtiga</title>
    <style>
      body { font-family: Arial; margin: 50px; }
      .chat { max-width: 600px; margin: auto; }
      .message { margin-bottom: 10px; }
      .user { color: blue; }
      .bot { color: green; }
    </style>
  </head>
  <body>
    <div class="chat">
      <h2>Chatbot LaOrtiga.cl 🌱</h2>
      <div id="messages"></div>
      <input type="text" id="user_input" placeholder="Escribe tu mensaje..." style="width:80%;">
      <button onclick="sendMessage()">Enviar</button>
    </div>
    <script>
      function appendMessage(sender, text){
        const messages = document.getElementById("messages");
        const div = document.createElement("div");
        div.className = "message " + sender;
        div.innerText = text;
        messages.appendChild(div);
      }

      function sendMessage(){
        const input = document.getElementById("user_input");
        const text = input.value;
        if(!text) return;
        appendMessage("user", text);
        input.value = "";
        fetch("/chat", {
          method:"POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({message:text})
        })
        .then(response => response.json())
        .then(data => {
          appendMessage("bot", data.reply);
        });
      }
    </script>
  </body>
</html>
"""

# Función para crear evento en Google Calendar
def crear_evento(fecha_hora_str, invitado_email):
    try:
        tz = pytz.timezone("America/Santiago")
        fecha_hora = datetime.strptime(fecha_hora_str, "%Y-%m-%d %H:%M")
        fecha_hora = tz.localize(fecha_hora)
        evento = {
            "summary": "Cita LaOrtiga.cl",
            "start": {"dateTime": fecha_hora.isoformat()},
            "end": {"dateTime": (fecha_hora + timedelta(hours=1)).isoformat()},
            "attendees": [{"email": invitado_email}],
            "reminders": {"useDefault": True},
        }
        service.events().insert(calendarId=CALENDAR_ID, body=evento, sendUpdates="all").execute()
        return True, "Invitación enviada correctamente a " + invitado_email
    except Exception as e:
        return False, f"Error al crear el evento: {str(e)}"

# Ruta principal
@app.route("/")
def index():
    return render_template_string(CHAT_TEMPLATE)

# Ruta para chat
@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_msg = data.get("message", "").lower()
    reply = ""

    # Manejo de agendamiento simple
    if "agendar" in user_msg or "hora" in user_msg:
        reply = "Por favor indica la fecha y hora en formato YYYY-MM-DD HH:MM (ej: 2025-08-07 12:00)"
    elif "@" in user_msg and "2025" in user_msg:
        # Supone que el usuario envía "YYYY-MM-DD HH:MM correo@ejemplo.com"
        try:
            fecha_hora_str, invitado_email = user_msg.split()
            ok, mensaje = crear_evento(fecha_hora_str, invitado_email)
            reply = mensaje
        except:
            reply = "Formato incorrecto. Usa 'YYYY-MM-DD HH:MM correo@ejemplo.com'"
    else:
        reply = "Hola! 🌱 ¿Deseas agendar una cita? Escribe la fecha, hora y correo del invitado."

    return jsonify({"reply": reply})

# Ejecutar Flask
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)
