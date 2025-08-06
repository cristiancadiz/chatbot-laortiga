import os
from flask import Flask, redirect, url_for, session, request, render_template_string
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as grequests
import google.oauth2.credentials
import googleapiclient.discovery
from datetime import timedelta, datetime
import openai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'clave-super-secreta')
app.permanent_session_lifetime = timedelta(days=30)

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai.api_key = OPENAI_API_KEY

REDIRECT_URI = "https://chatbot-laortiga-9.onrender.com/callback"

def crear_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=[
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/calendar.events"
        ],
        redirect_uri=REDIRECT_URI
    )

def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

@app.route('/')
def home():
    if 'google_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('chat'))

@app.route('/login')
def login():
    flow = crear_flow()
    auth_url, state = flow.authorization_url(include_granted_scopes='true')
    session['state'] = state
    return redirect(auth_url)

@app.route('/callback')
def callback():
    state = session.get('state')
    flow = crear_flow()
    flow.fetch_token(authorization_response=request.url)

    if not flow.credentials:
        return "No se pudo autenticar con Google.", 400

    credentials = flow.credentials
    request_session = grequests.Request()

    try:
        idinfo = id_token.verify_oauth2_token(
            credentials._id_token,
            request_session,
            GOOGLE_CLIENT_ID
        )
    except ValueError:
        return "Token inválido", 400

    session['google_id'] = idinfo.get("sub")
    session['name'] = idinfo.get("name")
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    session.permanent = True
    session['historial'] = [{
        "role": "assistant",
        "content": f"Hola {session['name']}! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
    }]
    return redirect(url_for('chat'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'google_id' not in session:
        return redirect(url_for('login'))

    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]

    respuesta = ""
    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()
        session['historial'].append({"role": "user", "content": pregunta})

        # Si detecta intención de agendar
        if "agendar" in pregunta.lower():
            creds = google.oauth2.credentials.Credentials(**session['credentials'])
            service = googleapiclient.discovery.build('calendar', 'v3', credentials=creds)
            # Para ejemplo: evento mañana a las 15:00 por 1 hora
            tz = "America/Santiago"
            start_dt = datetime.now().replace(hour=15, minute=0, second=0, microsecond=0)
            end_dt = start_dt + timedelta(hours=1)
            event = {
                'summary': pregunta,
                'start': {'dateTime': start_dt.isoformat(), 'timeZone': tz},
                'end': {'dateTime': end_dt.isoformat(), 'timeZone': tz}
            }
            new_event = service.events().insert(calendarId='primary', body=event).execute()
            enlace = new_event.get('htmlLink')
            respuesta = f"✅ Evento creado: <a href='{enlace}' target='_blank'>Ver en Google Calendar</a>"
        else:
            mensajes = [
                {"role": "system", "content": "Eres un asistente de LaOrtiga.cl. Responde preguntas sobre sostenibilidad o emprendimiento verde."}
            ] + session['historial'][-10:]
            completion = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=mensajes,
                max_tokens=200,
                temperature=0.7
            )
            respuesta = completion.choices[0].message.content.strip()

        session['historial'].append({"role": "assistant", "content": respuesta})
        guardar_historial_en_archivo(session['historial'])

    return render_template_string(TEMPLATE, historial=session['historial'], user_name=session.get('name'))

TEMPLATE = ''' ... incluye aquí tu HTML completo del chat ... '''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
