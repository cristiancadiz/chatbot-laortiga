import os
import json
from flask import Flask, request, session, render_template_string, redirect, url_for
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import dateparser
import pytz
import openai

# --- Configuración de entorno ---
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")
app.permanent_session_lifetime = timedelta(days=30)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise Exception("Debes configurar OPENAI_API_KEY")
client = openai.OpenAI(api_key=OPENAI_API_KEY)

GOOGLE_CALENDAR_EMAIL = os.environ.get("GOOGLE_CALENDAR_EMAIL")
SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
if not GOOGLE_CALENDAR_EMAIL or not SERVICE_ACCOUNT_JSON:
    raise Exception("Debes configurar GOOGLE_CALENDAR_EMAIL y GOOGLE_SERVICE_ACCOUNT_FILE")

# Cargar credenciales de Service Account
credentials_info = json.loads(SERVICE_ACCOUNT_JSON)
credentials = service_account.Credentials.from_service_account_info(
    credentials_info,
    scopes=["https://www.googleapis.com/auth/calendar"]
)

service = build('calendar', 'v3', credentials=credentials)

# --- HTML template ---
TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Asistente La Ortiga</title>
</head>
<body>
<h2>Asistente La Ortiga 🌱</h2>
<form method="POST">
    <label>Escribe fecha, hora y correo del invitado:</label><br>
    <input type="text" name="mensaje" placeholder="YYYY-MM-DD HH:MM correo@ejemplo.com"><br>
    <input type="submit" value="Agendar">
</form>
{% if respuesta %}
<p>{{ respuesta|safe }}</p>
{% endif %}
</body>
</html>
"""

# --- Función para crear evento ---
def crear_evento(mensaje):
    mensaje = mensaje.strip()
    pattern = r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\S+@\S+\.\S+)$"
    import re
    match = re.match(pattern, mensaje)
    if not match:
        return "Formato incorrecto. Usa 'YYYY-MM-DD HH:MM correo@ejemplo.com'"

    fecha, hora, correo = match.groups()
    inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
    inicio = pytz.timezone("America/Santiago").localize(inicio)
    fin = inicio + timedelta(hours=1)

    evento = {
        'summary': 'Cita en La Ortiga 🌱',
        'description': 'Sesión agendada automáticamente',
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Santiago'},
        'end': {'dateTime': fin.isoformat(), 'timeZone': 'America/Santiago'},
        'attendees': [{'email': correo}],
    }

    try:
        created_event = service.events().insert(
            calendarId=GOOGLE_CALENDAR_EMAIL,
            body=evento,
            sendUpdates='all'
        ).execute()
        return f"Cita agendada ✅ <a href='{created_event.get('htmlLink')}' target='_blank'>Ver en Google Calendar</a>"
    except Exception as e:
        return f"Error al crear evento: {e}"

# --- Rutas ---
@app.route("/", methods=["GET", "POST"])
def index():
    respuesta = None
    if request.method == "POST":
        mensaje = request.form.get("mensaje")
        if mensaje:
            respuesta = crear_evento(mensaje)
    return render_template_string(TEMPLATE, respuesta=respuesta)

# --- Ejecutar app ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
