import os
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template_string, request, session, redirect, url_for
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.errors import HttpError
from datetime import timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('app.secret_key')
if not app.secret_key:
    raise Exception("La variable de entorno SECRET_KEY no está configurada.")
app.permanent_session_lifetime = timedelta(days=30)

# Ruta del archivo de la cuenta de servicio
SERVICE_ACCOUNT_FILE = 'service_account.json'

# Define los alcances necesarios
SCOPES = ['https://www.googleapis.com/auth/gmail.send']

# Configuración del cliente de Gmail
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
service = build('gmail', 'v1', credentials=credentials)

def create_message(sender, to, subject, message_text):
    """Crea el mensaje de correo en formato MIME"""
    message = MIMEMultipart()
    message['to'] = to
    message['from'] = sender
    message['subject'] = subject
    
    msg = MIMEText(message_text)
    message.attach(msg)

    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
    return {'raw': raw_message}

def send_message(service, sender, to, subject, body):
    """Envía el mensaje usando la API de Gmail"""
    try:
        message = create_message(sender, to, subject, body)
        send_message = service.users().messages().send(userId="me", body=message).execute()
        print(f'Mensaje enviado a {to}, ID: {send_message["id"]}')
        return send_message
    except HttpError as error:
        print(f'Ha ocurrido un error: {error}')
        return None

def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

@app.route('/')
def home():
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]
    return redirect(url_for('chat'))

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

            # Detectar si la pregunta es sobre enviar un correo
            if 'enviar correo' in pregunta.lower():
                sender = "cristiancadiz987@gmail.com"  # Correo del que envías
                to = "destinatario@dominio.com"  # Cambia por el correo destinatario
                subject = "Asunto del Correo"
                body = "Este es el cuerpo del correo"

                send_message(service, sender, to, subject, body)
                respuesta = "✅ Correo enviado con éxito."

            else:
                respuesta = "No entendí tu mensaje. ¿Necesitas ayuda con algo más?"

            session['historial'].append({"role": "assistant", "content": respuesta})
            guardar_historial_en_archivo(session['historial'])

    return render_template_string(TEMPLATE, historial=session['historial'])

TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Asistente La Ortiga</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet" />
    <style>
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
                <small style="font-size:12px;">Conectado como {{ session.get('name') or 'Invitado' }}</small>
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
                input.focus();
            } else {
                chatBox.style.display = 'none';
            }
        };
        chatMessages.scrollTop = chatMessages.scrollHeight;
    </script>
</body>
</html>

