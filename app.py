import os
import re
from flask import Flask, redirect, url_for, session, request, render_template_string
import requests
from datetime import datetime, timedelta
import dateparser

from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
import google.auth.transport.requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# --------------------- Configuración ---------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "supersecret")
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")

flow = Flow.from_client_config(
    {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost:5000/callback", "https://tu-app-en-render.onrender.com/callback"],
        }
    },
    scopes=[
        "https://www.googleapis.com/auth/calendar",
        "openid",
        "https://www.googleapis.com/auth/userinfo.email"
    ]
)
flow.redirect_uri = "https://tu-app-en-render.onrender.com/callback"

# --------------------- Template HTML ---------------------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>La Ortiga 🌱</title>
</head>
<body>
    <h2>Hola{{ ' ' + name if name else '' }}! 🌱</h2>
    {% if not credentials %}
        <a href="{{ url_for('login') }}">Iniciar sesión con Google</a>
    {% else %}
        <form method="POST" action="{{ url_for('index') }}">
            <label>Escribe la fecha y hora y correo del invitado:</label><br>
            <input type="text" name="mensaje" placeholder="Ej: 2025-12-10 22:30 correo@ejemplo.com"><br>
            <input type="submit" value="Agendar">
        </form>
        {% if respuesta %}
            <p>{{ respuesta }}</p>
        {% endif %}
    {% endif %}
</body>
</html>
"""

# --------------------- Rutas Flask ---------------------
@app.route("/")
def index():
    credentials = session.get("credentials")
    name = session.get("user_name")
    return render_template_string(HTML_TEMPLATE, credentials=credentials, name=name, respuesta=None)

@app.route("/", methods=["POST"])
def handle_post():
    credentials = session.get("credentials")
    name = session.get("user_name")
    mensaje = request.form["mensaje"]
    respuesta = procesar_mensaje(mensaje, credentials)
    return render_template_string(HTML_TEMPLATE, credentials=credentials, name=name, respuesta=respuesta)

@app.route("/login")
def login():
    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true"
    )
    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

    request_session = requests.session()
    token_request = google.auth.transport.requests.Request(session=request_session)
    id_info = id_token.verify_oauth2_token(
        credentials.id_token, token_request, GOOGLE_CLIENT_ID
    )
    session["user_name"] = id_info.get("name")
    return redirect(url_for("index"))

# --------------------- Función principal ---------------------
def procesar_mensaje(mensaje, credentials_dict):
    mensaje = mensaje.strip()

    # Intentamos parsear usando dateparser
    try:
        partes = mensaje.split()
        if len(partes) < 2:
            return "Formato incorrecto. Usa 'YYYY-MM-DD HH:MM correo@ejemplo.com'"
        fecha_hora_str = " ".join(partes[:2])
        correo = partes[2] if len(partes) > 2 else ""
        fecha = dateparser.parse(fecha_hora_str)
        if not fecha or not re.match(r"\S+@\S+\.\S+", correo):
            return "Formato incorrecto. Usa 'YYYY-MM-DD HH:MM correo@ejemplo.com'"
    except Exception:
        return "Formato incorrecto. Usa 'YYYY-MM-DD HH:MM correo@ejemplo.com'"

    inicio = fecha
    fin = inicio + timedelta(hours=1)

    creds = Credentials(**credentials_dict)
    service = build("calendar", "v3", credentials=creds)

    evento = {
        "summary": "Cita en La Ortiga 🌱",
        "description": "Sesión agendada automáticamente",
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Santiago"},
        "attendees": [{"email": correo}],
    }

    try:
        service.events().insert(calendarId="primary", body=evento, sendUpdates="all").execute()
        return f"Cita agendada para {correo} el {inicio.strftime('%Y-%m-%d a las %H:%M')} hrs ✅"
    except Exception as e:
        return f"Error al agendar: {e}"

# --------------------- Ejecutar ---------------------
# Render usará Gunicorn, no app.run()
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
