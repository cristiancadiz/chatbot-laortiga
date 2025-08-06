import os
import re
from datetime import datetime
from flask import Flask, redirect, url_for, session, request, render_template_string
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
import google.auth.transport.requests
import requests

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
            "redirect_uris": [
                "http://localhost:5000/callback",
                "https://TU-APP-NOMBRE.onrender.com/callback"
            ],
        }
    },
    scopes=["https://www.googleapis.com/auth/calendar",
            "openid",
            "https://www.googleapis.com/auth/userinfo.email"]
)
flow.redirect_uri = "https://TU-APP-NOMBRE.onrender.com/callback"

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>La Ortiga 🌱</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; }
        h2 { color: #2E7D32; }
        input[type=text] { width: 300px; padding: 5px; margin: 5px 0; }
        input[type=submit] { padding: 5px 10px; background-color: #2E7D32; color: white; border: none; cursor: pointer; }
        input[type=submit]:hover { background-color: #1B5E20; }
        p { margin-top: 15px; font-weight: bold; }
    </style>
</head>
<body>
    <h2>Hola{{ ' ' + name if name else '' }}! 🌱</h2>
    {% if not credentials %}
        <a href="{{ url_for('login') }}">Iniciar sesión con Google</a>
    {% else %}
        <form method="POST" action="{{ url_for('index') }}">
            <label>Escribe la fecha y hora y correo del invitado:</label><br>
            <input type="text" name="mensaje" placeholder="YYYY-MM-DD HH:MM correo@ejemplo.com"><br>
            <input type="submit" value="Agendar">
        </form>
        {% if respuesta %}
            <p>{{ respuesta }}</p>
        {% endif %}
    {% endif %}
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    credentials = session.get("credentials")
    name = session.get("user_name")
    respuesta = None
    if request.method == "POST" and credentials:
        mensaje = request.form["mensaje"]
        respuesta = procesar_mensaje(mensaje, credentials)
    return render_template_string(HTML_TEMPLATE, credentials=credentials, name=name, respuesta=respuesta)

@app.route("/login")
def login():
    authorization_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true")
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
    id_info = id_token.verify_oauth2_token(credentials.id_token, token_request, GOOGLE_CLIENT_ID)
    session["user_name"] = id_info.get("name")
    return redirect(url_for("index"))

def procesar_mensaje(mensaje, credentials_dict):
    mensaje = mensaje.strip()
    pattern = r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+(\S+@\S+\.\S+)$"
    match = re.match(pattern, mensaje)

    if not match:
        return "Formato incorrecto. Usa 'YYYY-MM-DD HH:MM correo@ejemplo.com'"

    fecha, hora, correo = match.groups()
    try:
        inicio = datetime.strptime(f"{fecha} {hora}", "%Y-%m-%d %H:%M")
        fin = inicio.replace(hour=inicio.hour + 1)
    except ValueError:
        return "Fecha u hora inválida."

    from google.oauth2.credentials import Credentials
    creds = Credentials(**credentials_dict)

    from googleapiclient.discovery import build
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
        return f"Cita agendada para {correo} el {fecha} a las {hora} hrs ✅"
    except Exception as e:
        return f"Error al agendar: {e}"

# Para Render: usar gunicorn
# gunicorn app:app --bind 0.0.0.0:$PORT
