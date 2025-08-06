import os
from datetime import datetime, timedelta
from flask import Flask, request, session, redirect, url_for, render_template_string
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from openai import OpenAI

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")  # Clave secreta desde entorno

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
REDIRECT_URI = "https://chatbot-laortiga-9.onrender.com/callback"  # Cambia a tu URL

client = OpenAI(api_key=OPENAI_API_KEY)

CHAT_HTML = """
<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8" />
    <title>Chatbot con Google Calendar</title>
  </head>
  <body>
    <h1>Chatbot para agendar en Google Calendar</h1>
    <form method="post">
      <label for="message">Escribe un mensaje para agendar:</label><br>
      <input id="message" name="message" style="width:300px" placeholder="Ej: Reunión mañana a las 3pm por 1 hora" required><br><br>
      <button type="submit">Enviar</button>
    </form>

    {% if response %}
      <h3>Respuesta del chatbot:</h3>
      <p>{{ response }}</p>
    {% endif %}

    {% if event_url %}
      <h3>Evento creado en Google Calendar:</h3>
      <p><a href="{{ event_url }}" target="_blank">Ver evento</a></p>
    {% endif %}

    <p><a href="/">Volver al inicio</a></p>
    <p><a href="/logout">Cerrar sesión</a></p>
  </body>
</html>
"""

@app.route("/")
def index():
    if "credentials" not in session:
        return redirect(url_for("login"))
    return '''
    <h1>Bienvenido al chatbot con Google Calendar</h1>
    <p>Ve a <a href="/chat">/chat</a> para enviar mensajes.</p>
    <p><a href="/logout">Cerrar sesión</a></p>
    '''

@app.route("/login")
def login():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["state"] = state
    return redirect(authorization_url)

@app.route("/callback")
def callback():
    state = session.get("state")
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )
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
    return redirect(url_for("chat"))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/chat", methods=["GET", "POST"])
def chat():
    if "credentials" not in session:
        return redirect(url_for("login"))

    response_text = None
    event_url = None

    if request.method == "POST":
        user_message = request.form.get("message")
        if not user_message:
            return render_template_string(CHAT_HTML, response="Por favor, escribe un mensaje.", event_url=None)

        # GPT-3.5 Turbo request
        messages = [
            {"role": "system", "content": "Eres un asistente para crear eventos en Google Calendar."},
            {"role": "user", "content": user_message},
        ]

        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300,
        )
        response_text = completion.choices[0].message.content

        # Crear credenciales Google desde sesión
        credentials_info = session["credentials"]
        creds = Credentials(
            token=credentials_info["token"],
            refresh_token=credentials_info.get("refresh_token"),
            token_uri=credentials_info["token_uri"],
            client_id=credentials_info["client_id"],
            client_secret=credentials_info["client_secret"],
            scopes=credentials_info["scopes"],
        )
        service = build("calendar", "v3", credentials=creds)

        # Aquí deberías procesar response_text para extraer fecha/hora y crear evento real.
        # Para demo, crea un evento simple ahora +1h
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(hours=1)

        event = {
            "summary": "Evento creado desde chatbot",
            "description": user_message,
            "start": {
                "dateTime": start_time.isoformat() + "Z",
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_time.isoformat() + "Z",
                "timeZone": "UTC"
            }
        }

        created_event = service.events().insert(calendarId="primary", body=event).execute()
        event_url = created_event.get("htmlLink")

    return render_template_string(CHAT_HTML, response=response_text, event_url=event_url)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
