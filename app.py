from flask import Flask, render_template_string, request, session, redirect, url_for
from datetime import datetime, timedelta
import os
import openai
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery

app = Flask(__name__)
app.secret_key = "clave-super-secreta"
app.permanent_session_lifetime = timedelta(days=30)

# --- CLIENTE OPENAI ---
client = openai.OpenAI()

# --- CONFIG GOOGLE OAUTH 2 CON CREDENCIALES EN DICT ---
CLIENT_CONFIG = {
    "web": {
        "client_id": "668109418611-nk1492ofov4n5drh4recsdbfukv3s8rf.apps.googleusercontent.com",
        "project_id": "reflected-drake-465417-a3",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": "GOCSPX-BueOrjcJtK1p5hOwm26PjQ",
        "redirect_uris": ["http://localhost:5000/oauth2callback"],
        "javascript_origins": ["http://localhost:5000"]
    }
}
SCOPES = ['https://www.googleapis.com/auth/calendar.events']
API_SERVICE_NAME = 'calendar'
API_VERSION = 'v3'

# --- GUARDAR HISTORIAL ---
def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

# --- RUTA PRINCIPAL DEL CHAT ---
@app.route('/', methods=['GET', 'POST'])
def index():
    session.permanent = True

    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]

    respuesta = ""

    credentials = None
    if 'credentials' in session:
        credentials = google.oauth2.credentials.Credentials(**session['credentials'])

    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()

        if pregunta.lower() == "login google":
            return redirect(url_for('authorize'))

        elif pregunta.lower().startswith("crear evento"):
            if not credentials or not credentials.valid:
                respuesta = "Por favor, inicia sesión con Google para crear eventos (escribe: login google)."
            else:
                try:
                    detalles = pregunta[13:].strip()
                    start_dt = (datetime.now() + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
                    end_dt = start_dt + timedelta(hours=1)
                    event = {
                        'summary': detalles or 'Evento desde chatbot',
                        'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'America/Santiago'},
                        'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'America/Santiago'},
                    }

                    service = googleapiclient.discovery.build(
                        API_SERVICE_NAME, API_VERSION, credentials=credentials)

                    evento_creado = service.events().insert(calendarId='primary', body=event).execute()
                    enlace = evento_creado.get('htmlLink')
                    respuesta = f"Evento creado con éxito: <a href='{enlace}' target='_blank'>Ver en Google Calendar</a>"

                except Exception as e:
                    respuesta = f"Error al crear evento: {str(e)}"

        else:
            if pregunta:
                session['historial'].append({"role": "user", "content": pregunta})

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

    if credentials:
        session['credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }

    return render_template_string(TEMPLATE, respuesta=respuesta, historial=session.get('historial', []))

# --- RUTAS PARA AUTORIZACION GOOGLE ---
@app.route('/authorize')
def authorize():
    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        CLIENT_CONFIG, scopes=SCOPES)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true')

    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session['state']

    flow = google_auth_oauthlib.flow.Flow.from_client_config(
        CLIENT_CONFIG, scopes=SCOPES, state=state)
    flow.redirect_uri = url_for('oauth2callback', _external=True)

    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)

    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

    return redirect(url_for('index'))

# --- TEMPLATE HTML CONVERSACIONAL ---
TEMPLATE = ''' 
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Asistente La Ortiga</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body {margin:0; font-family:'Inter', sans-serif; background:#f3f4f6;}
        #chat-container {position:fixed; bottom:90px; right:20px; width:370px; height:540px; background:#fff; border-radius:20px; box-shadow:0 8px 30px rgba(0,0,0,0.15); display:flex; flex-direction:column; overflow:hidden; z-index:9999;}
        #chat-header {display:flex; align-items:center; background:#4CAF50; padding:16px; color:#fff;}
        #chat-header img {border-radius:50%; width:40px; height:40px; margin-right:12px; border:2px solid #fff;}
        #chat-header .name {font-weight:bold; font-size:16px;}
        #chat-messages {flex:1; padding:14px; overflow-y:auto; background:#f9fafb;}
        .msg {padding:12px 16px; margin:8px 0; max-width:80%; border-radius:20px; font-size:14px; line-height:1.4; word-break:break-word;}
        .msg.user {align-self:flex-end; background:#DCF8C6; border-bottom-right-radius:4px;}
        .msg.bot {align-self:flex-start; background:#fff; border-bottom-left-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,0.05);}
        #chat-input-form {display:flex; padding:12px; border-top:1px solid #e0e0e0; background:#fff;}
        #chat-input {flex:1; padding:10px 14px; border-radius:12px; border:1px solid #ccc; font-size:14px;}
        #chat-send {background:#4CAF50; color:#fff; border:none; padding:0 16px; margin-left:10px; font-size:18px; border-radius:12px; cursor:pointer;}
        #chat-toggle-btn {position:fixed; bottom:20px; right:20px; width:60px; height:60px; border-radius:50%; background:#4CAF50; color:#fff; font-size:28px; border:none; box-shadow:0 4px 12px rgba(0,0,0,0.3); cursor:pointer; z-index:10000;}
    </style>
</head>
<body>
    <button id="chat-toggle-btn">💬</button>
    <div id="chat-container" style="display:none;">
        <div id="chat-header">
            <img src="https://cdn-icons-png.flaticon.com/512/194/194938.png" alt="Asistente">
            <div>
                <div class="name">Capitán Planeta</div>
                <small style="font-size:12px;">Disponible ahora</small>
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
            chatBox.style.display = chatBox.style.display === 'none' ? 'flex' : 'none';
            scrollToBottom(); input.focus();
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
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)), debug=True)
