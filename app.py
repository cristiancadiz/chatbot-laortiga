import os
from flask import Flask, redirect, url_for, session, request, render_template_string
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as grequests
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from datetime import timedelta, datetime
import openai
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('app.secret_key')
if not app.secret_key:
    raise Exception("La variable de entorno SECRET_KEY no está configurada.")
app.permanent_session_lifetime = timedelta(days=30)

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise Exception("La variable de entorno OPENAI_API_KEY no está configurada.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

REDIRECT_URI = "https://chatbot-laortiga-9.onrender.com/callback"

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.events"
]

flow = Flow.from_client_config(
    {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
            "scopes": SCOPES
        }
    },
    scopes=SCOPES,
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

def crear_evento_google_calendar(session, fecha_hora):
    if 'credentials' not in session:
        return "No tengo permisos para acceder a tu calendario."

    creds = Credentials(**session['credentials'])
    service = build('calendar', 'v3', credentials=creds)

    inicio = datetime.strptime(fecha_hora, "%Y-%m-%d %H:%M")
    fin = inicio + timedelta(minutes=30)

    evento = {
        'summary': 'Consulta con LaOrtiga.cl',
        'description': 'Reserva automatizada con Capitán Planeta 🌱',
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Santiago'},
        'end': {'dateTime': fin.isoformat(), 'timeZone': 'America/Santiago'},
    }

    evento = service.events().insert(calendarId='primary', body=evento).execute()
    return f"✅ Evento creado: <a href=\"{evento.get('htmlLink')}\" target=\"_blank\">Ver en tu calendario</a>"

@app.route('/')
def home():
    if 'google_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('chat'))

@app.route('/login')
def login():
    authorization_url, state = flow.authorization_url(include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
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
    session['email'] = idinfo.get("email")
    session['name'] = idinfo.get("name")
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    session['esperando_fecha'] = False
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
        if pregunta:
            session['historial'].append({"role": "user", "content": pregunta})

            if session.get('esperando_fecha'):
                session['esperando_fecha'] = False
                try:
                    respuesta = crear_evento_google_calendar(session, pregunta)
                except Exception as e:
                    respuesta = f"Ocurrió un error al crear el evento: {str(e)}"
                session['historial'].append({"role": "assistant", "content": respuesta})
                guardar_historial_en_archivo(session['historial'])
                return render_template_string(TEMPLATE, historial=session['historial'], user_name=session.get('name'))

            if "agendar" in pregunta.lower():
                session['esperando_fecha'] = True
                respuesta = "¡Perfecto! ¿Para qué día y hora quieres agendar? (Ej: 2025-08-07 15:00)"
                session['historial'].append({"role": "assistant", "content": respuesta})
                return render_template_string(TEMPLATE, historial=session['historial'], user_name=session.get('name'))

            mensajes = [
                {"role": "system", "content": "Eres un asistente conversacional de LaOrtiga.cl. Habla de forma amable, cercana y profesional. Solo responde preguntas sobre sostenibilidad, productos ecológicos o emprendimiento verde."}
            ] + session['historial'][-10:]

            completion = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=mensajes,
                max_tokens=200,
                temperature=0.7
            )
            respuesta = completion.choices[0].message.content.strip()
            session['historial'].append({"role": "assistant", "content": respuesta})
            guardar_historial_en_archivo(session['historial'])

    return render_template_string(TEMPLATE, historial=session['historial'], user_name=session.get('name'))

TEMPLATE = """
<!DOCTYPE html>
<html lang=\"es\">
<head>
    <meta charset=\"UTF-8\" />
    <title>Asistente La Ortiga</title>
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <link href=\"https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap\" rel=\"stylesheet\" />
</head>
<body>
    <button id=\"chat-toggle-btn\">\ud83d\udcac</button>
    <div id=\"chat-container\" style=\"display:flex; flex-direction:column; width:300px; height:500px; border:1px solid #ccc;\">
        <div id=\"chat-header\">
            <img src=\"https://cdn-icons-png.flaticon.com/512/194/194938.png\" alt=\"Asistente\" width=\"30\" />
            <div>
                <div class=\"name\">Capitán Planeta</div>
                <small style=\"font-size:12px;\">Conectado como {{ user_name }}</small>
            </div>
        </div>
        <div id=\"chat-messages\" style=\"flex:1; overflow-y:auto; padding:10px;\">
            {% for m in historial %}
                <div class=\"msg {% if m.role == 'user' %}user{% else %}bot{% endif %}\">{{ m.content | safe }}</div>
            {% endfor %}
        </div>
        <form id=\"chat-input-form\" method=\"POST\" style=\"display:flex;\">
            <input type=\"text\" id=\"chat-input\" name=\"pregunta\" placeholder=\"Escribe tu mensaje...\" autocomplete=\"off\" required style=\"flex:1;\" />
            <button id=\"chat-send\">\u2794</button>
        </form>
    </div>
    <script>
        const toggleBtn = document.getElementById('chat-toggle-btn');
        const chatBox = document.getElementById('chat-container');
        const chatMessages = document.getElementById('chat-messages');
        const input = document.getElementById('chat-input');

        toggleBtn.onclick = () => {
            chatBox.style.display = chatBox.style.display === 'none' ? 'flex' : 'none';
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
