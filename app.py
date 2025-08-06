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

TEMPLATE = '''<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Asistente La Ortiga</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet" />
    <style>
        body {
            margin: 0;
            font-family: 'Inter', sans-serif;
            background: #f3f4f6;
        }
        #chat-container {
            position: fixed;
            bottom: 90px;
            right: 20px;
            width: 370px;
            height: 540px;
            background: #ffffff;
            border-radius: 20px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.15);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            z-index: 9999;
        }
        #chat-header {
            display: flex;
            align-items: center;
            background: #4CAF50;
            padding: 16px;
            color: white;
        }
        #chat-header img {
            border-radius: 50%;
            width: 40px;
            height: 40px;
            margin-right: 12px;
            border: 2px solid white;
        }
        #chat-header .name {
            font-weight: bold;
            font-size: 16px;
        }
        #chat-messages {
            flex: 1;
            padding: 14px;
            overflow-y: auto;
            background: #f9fafb;
        }
        .msg {
            padding: 12px 16px;
            margin: 8px 0;
            max-width: 80%;
            border-radius: 20px;
            font-size: 14px;
            line-height: 1.4;
            word-break: break-word;
        }
        .msg.user {
            align-self: flex-end;
            background-color: #DCF8C6;
            border-bottom-right-radius: 4px;
        }
        .msg.bot {
            align-self: flex-start;
            background-color: #ffffff;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        }
        #chat-input-form {
            display: flex;
            padding: 12px;
            border-top: 1px solid #e0e0e0;
            background: #fff;
        }
        #chat-input {
            flex: 1;
            padding: 10px 14px;
            border-radius: 12px;
            border: 1px solid #ccc;
            font-size: 14px;
        }
        #chat-send {
            background: #4CAF50;
            color: white;
            border: none;
            padding: 0 16px;
            margin-left: 10px;
            font-size: 18px;
            border-radius: 12px;
            cursor: pointer;
        }
        #chat-toggle-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: #4CAF50;
            color: white;
            font-size: 28px;
            border: none;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            cursor: pointer;
            z-index: 10000;
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
                <small style="font-size:12px;">Conectado como {{ user_name }}</small>
            </div>
        </div>
        <div id="chat-messages">
            {% for m in historial %}
                <div class="msg {% if m.role == 'user' %}user{% else %}bot{% endif %}">{{ m.content | safe }}</div>
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
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

