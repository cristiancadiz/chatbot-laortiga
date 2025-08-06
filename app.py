import os
from flask import Flask, redirect, url_for, session, request, render_template_string
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import timedelta, datetime
import openai
from dotenv import load_dotenv
import dateparser
import pytz  

# Cargar variables de entorno
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('app.secret_key')
if not app.secret_key:
    raise Exception("La variable de entorno SECRET_KEY no está configurada.")
app.permanent_session_lifetime = timedelta(days=30)

# OpenAI
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise Exception("La variable de entorno OPENAI_API_KEY no está configurada.")
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Google Calendar (admin)
GOOGLE_CREDENTIALS = {
    'token': os.getenv('GOOGLE_TOKEN'),
    'refresh_token': os.getenv('GOOGLE_REFRESH_TOKEN'),
    'token_uri': 'https://oauth2.googleapis.com/token',
    'client_id': os.getenv('GOOGLE_CLIENT_ID'),
    'client_secret': os.getenv('GOOGLE_CLIENT_SECRET'),
    'scopes': ['https://www.googleapis.com/auth/calendar.events']
}

def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

def crear_evento_google_calendar(session, fecha_hora, correo_cliente=None):
    creds = Credentials(**GOOGLE_CREDENTIALS)
    service = build('calendar', 'v3', credentials=creds)

    inicio = dateparser.parse(
        fecha_hora,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "America/Santiago",
            "TO_TIMEZONE": "America/Santiago",
            "RELATIVE_BASE": datetime.now(pytz.timezone("America/Santiago"))
        }
    )

    if not inicio:
        return "⚠️ No pude entender la fecha y hora. Intenta con algo como: 'mañana a las 10' o 'el jueves a las 4pm'."

    fin = inicio + timedelta(minutes=30)

    evento = {
        'summary': 'Consulta con LaOrtiga.cl',
        'description': 'Reserva automatizada con Capitán Planeta 🌱',
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Santiago'},
        'end': {'dateTime': fin.isoformat(), 'timeZone': 'America/Santiago'},
    }

    if correo_cliente:
        evento['attendees'] = [{'email': correo_cliente}]

    evento = service.events().insert(calendarId='primary', body=evento, sendUpdates='all').execute()
    return f"✅ Evento creado: <a href=\"{evento.get('htmlLink')}\" target=\"_blank\">Ver en tu calendario</a>"

@app.route('/')
def home():
    if 'historial' not in session:
        session['historial'] = [{"role": "assistant", "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿En qué puedo ayudarte hoy?"}]
    return redirect(url_for('chat'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'historial' not in session:
        session['historial'] = [{"role": "assistant", "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿En qué puedo ayudarte hoy?"}]

    respuesta = ""

    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()
        if pregunta:
            session['historial'].append({"role": "user", "content": pregunta})

            # Modo agendar
            if 'modo_agendar' not in session and any(p in pregunta.lower() for p in ['agendar', 'reserva', 'cita', 'calendar']):
                session['modo_agendar'] = True
                respuesta = "¿Para qué día y hora quieres agendar? (ej: 'mañana a las 10')"

            elif session.get('modo_agendar') and 'fecha_hora' not in session:
                session['fecha_hora'] = pregunta
                respuesta = "Perfecto. ¿Cuál es tu correo electrónico para enviarte la invitación?"

            elif session.get('modo_agendar') and 'correo_cliente' not in session:
                session['correo_cliente'] = pregunta
                respuesta = crear_evento_google_calendar(session, session['fecha_hora'], session['correo_cliente'])
                # Limpiar modo agendar
                session.pop('modo_agendar')
                session.pop('fecha_hora')
                session.pop('correo_cliente')

            else:
                # Llamada normal a OpenAI
                mensajes = [{"role": "system", "content": "Eres un asistente conversacional de LaOrtiga.cl. Habla de forma amable, cercana y profesional."}] + session['historial'][-10:]
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
        body { font-family: 'Inter', sans-serif; background: #f4f9f4; margin:0; padding:0; }
        #chat-toggle-btn { position: fixed; bottom: 20px; right: 20px; font-size: 2rem; background: #2c7a2c; color: white; border: none; border-radius: 50%; width: 60px; height: 60px; cursor: pointer; }
        #chat-container { position: fixed; bottom: 90px; right: 20px; width: 350px; max-height: 500px; background: white; border-radius: 10px; box-shadow: 0 0 12px rgba(0,0,0,0.1); display: flex; flex-direction: column; }
        #chat-header { display: flex; align-items: center; padding: 10px; background: #2c7a2c; color: white; border-top-left-radius: 10px; border-top-right-radius: 10px; }
        #chat-header img { width: 40px; height: 40px; margin-right: 10px; }
        .name { font-weight: 600; }
        #chat-messages { flex-grow: 1; padding: 10px; overflow-y: auto; background: #eaf3ea; }
        .msg { margin-bottom: 10px; padding: 8px 12px; border-radius: 20px; max-width: 80%; word-wrap: break-word; }
        .bot { background: #2c7a2c; color: white; align-self: flex-start; }
        .user { background: #a3d1a3; color: #000; align-self: flex-end; }
        #chat-input-form { display: flex; border-top: 1px solid #ccc; }
        #chat-input { flex-grow: 1; border: none; padding: 10px; font-size: 1rem; border-bottom-left-radius: 10px; }
        #chat-send { border: none; background: #2c7a2c; color: white; padding: 0 20px; cursor: pointer; font-size: 1.2rem; border-bottom-right-radius: 10px; }
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
            if (chatBox.style.display === 'none') { chatBox.style.display = 'flex'; scrollToBottom(); input.focus(); } 
            else { chatBox.style.display = 'none'; }
        };
        function scrollToBottom() { chatMessages.scrollTop = chatMessages.scrollHeight; }
        window.onload = () => { chatBox.style.display = 'flex'; scrollToBottom(); };
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
