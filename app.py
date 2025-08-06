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
import dateparser
import pytz  

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
    with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

def crear_evento_google_calendar(session, fecha_hora):
    if 'credentials' not in session:
        return "No tengo permisos para acceder a tu calendario."

    creds = Credentials(**session['credentials'])
    service = build('calendar', 'v3', credentials=creds)

    # Usa timezone explícito
    tz = pytz.timezone("America/Santiago")
    inicio = dateparser.parse(fecha_hora, settings={"PREFER_DATES_FROM": "future"})
    if not inicio:
        return "⚠️ No pude entender la fecha y hora. Intenta con algo como: 'mañana a las 10' o 'el jueves a las 4pm'."

    if inicio.tzinfo is None:
        inicio = tz.localize(inicio)

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
    # Usuario puede chatear sin loguearse con Google
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]
    return redirect(url_for('chat'))

@app.route('/login')
def login():
    authorization_url, state = flow.authorization_url(
        include_granted_scopes='true',
        access_type='offline',
        prompt='consent'  # fuerza a Google a pedir consentimiento y dar refresh_token
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/callback')
def callback():
    state = session.get('state')
    flow.fetch_token(authorization_response=request.url)

    if not flow.credentials:
        return "No se pudo autenticar con Google.", 400

    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

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
    session.permanent = True
    return redirect(url_for('chat'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/chat', methods=['GET', 'POST'])
def chat():
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

            # Detectar intención de agendar
            if any(p in pregunta.lower() for p in ['agendar', 'reserva', 'cita', 'calendar']):
                if 'credentials' not in session:
                    # Pide login solo si no está autenticado
                    respuesta = "Para agendar necesito que te autentiques con Google Calendar. Por favor, <a href='/login'>inicia sesión aquí</a>."
                else:
                    respuesta = "¿Para qué día y hora quieres agendar? (por ejemplo: 'mañana a las 10')"

            # Si ya estaba en modo agendar y responde con fecha y hora
            elif ('credentials' in session and
                  any(p in session['historial'][-2]['content'].lower() for p in ['agendar', 'reserva', 'cita']) and
                  dateparser.parse(pregunta)):
                respuesta = crear_evento_google_calendar(session, pregunta)

            else:
                # Llamada normal a OpenAI
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
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Asistente La Ortiga</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet" />
    <style>
        /* tu CSS va aquí */
        body {
            font-family: 'Inter', sans-serif;
            background: #f4f9f4;
            margin: 0;
            padding: 0;
        }
        #chat-toggle-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            font-size: 2rem;
            background: #2c7a2c;
            color: white;
            border: none;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            cursor: pointer;
        }
        #chat-container {
            position: fixed;
            bottom: 90px;
            right: 20px;
            width: 350px;
            max-height: 500px;
            background: white;
            border-radius: 10px;
            box-shadow: 0 0 12px rgba(0,0,0,0.1);
            display: flex;
            flex-direction: column;
        }
        #chat-header {
            display: flex;
            align-items: center;
            padding: 10px;
            background: #2c7a2c;
            color: white;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
        }
        #chat-header img {
            width: 40px;
            height: 40px;
            margin-right: 10px;
        }
        .name {
            font-weight: 600;
        }
        #chat-messages {
            flex-grow: 1;
            padding: 10px;
            overflow-y: auto;
            background: #eaf3ea;
        }
        .msg {
            margin-bottom: 10px;
            padding: 8px 12px;
            border-radius: 20px;
            max-width: 80%;
            word-wrap: break-word;
        }
        .bot {
            background: #2c7a2c;
            color: white;
            align-self: flex-start;
        }
        .user {
            background: #a3d1a3;
            color: #000;
            align-self: flex-end;
        }
        #chat-input-form {
            display: flex;
            border-top: 1px solid #ccc;
        }
        #chat-input {
            flex-grow: 1;
            border: none;
            padding: 10px;
            font-size: 1rem;
            border-bottom-left-radius: 10px;
        }
        #chat-send {
            border: none;
            background: #2c7a2c;
            color: white;
            padding: 0 20px;
            cursor: pointer;
            font-size: 1.2rem;
            border-bottom-right-radius: 10px;
        }
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
                <div class="msg {% if m.role == 'user' %}user{% else %}bot{% endif %}">{{ m.content | safe }}</div>
            {% endfor %}
        </div>
        <form id="chat-input-form" method="POST">
            <input
                type="text"
                id="chat-input"
                name="pregunta"
                placeholder="Escribe tu mensaje..."
                autocomplete="off"
                required
            />
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)


