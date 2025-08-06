import os
from flask import Flask, request, render_template_string, session
from flask_socketio import SocketIO, emit
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import dateparser
import pytz
import json

app = Flask(__name__)
app.secret_key = os.getenv('APP_SECRET_KEY', 'supersecretkey')
app.permanent_session_lifetime = timedelta(days=30)
socketio = SocketIO(app)

# ⚡ Configuración de Google Service Account
SERVICE_ACCOUNT_FILE = 'service_account.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = os.getenv('GOOGLE_CALENDAR_ID')

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
service = build('calendar', 'v3', credentials=credentials)

# Carpeta y archivo para historial de reservas
HISTORIAL_DIR = 'conversaciones_guardadas'
os.makedirs(HISTORIAL_DIR, exist_ok=True)
HISTORIAL_FILE = os.path.join(HISTORIAL_DIR, 'historial_reservas.json')

# Inicializar historial de reservas
if not os.path.exists(HISTORIAL_FILE):
    with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)

def guardar_reserva(fecha_hora, email_invitado, link):
    with open(HISTORIAL_FILE, 'r+', encoding='utf-8') as f:
        reservas = json.load(f)
        reservas.append({
            'fecha_hora': fecha_hora,
            'email_invitado': email_invitado,
            'link': link
        })
        f.seek(0)
        json.dump(reservas, f, indent=2)
        f.truncate()
    # Emitir evento a todos los clientes conectados
    socketio.emit('nueva_reserva', {'fecha_hora': fecha_hora, 'email_invitado': email_invitado, 'link': link}, broadcast=True)

def crear_evento(fecha_hora, email_invitado):
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
        return None, "⚠️ No pude entender la fecha y hora."

    fin = inicio + timedelta(minutes=30)

    evento = {
        'summary': 'Reserva LaOrtiga.cl 🌱',
        'description': 'Reserva automatizada con Capitán Planeta',
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Santiago'},
        'end': {'dateTime': fin.isoformat(), 'timeZone': 'America/Santiago'},
        'attendees': [{'email': email_invitado}],
        'reminders': {'useDefault': True},
    }

    evento = service.events().insert(
        calendarId=CALENDAR_ID, body=evento, sendUpdates='all'
    ).execute()

    return evento.get('htmlLink'), f"✅ Evento creado: <a href='{evento.get('htmlLink')}' target='_blank'>Ver en calendario</a>"

@app.route('/')
def home():
    session['step'] = 'inicio'
    session['fecha_hora'] = None
    session['email_invitado'] = None
    session['respuesta'] = "¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿Quieres agendar una reserva?"
    session['historial'] = [{"role": "assistant", "content": session['respuesta']}]
    return render_template_string(TEMPLATE, respuesta=session['historial'][-1]['content'])

@app.route('/chat', methods=['POST'])
def chat():
    texto = request.form.get('mensaje').strip()
    session['historial'].append({"role": "user", "content": texto})
    respuesta = ""

    if session['step'] == 'inicio':
        session['step'] = 'fecha_hora'
        respuesta = "Perfecto. ¿Para qué día y hora quieres agendar? (ej: 'mañana a las 12')"

    elif session['step'] == 'fecha_hora':
        session['fecha_hora'] = texto
        session['step'] = 'correo'
        respuesta = "Genial. ¿Cuál es el correo electrónico del invitado?"

    elif session['step'] == 'correo':
        session['email_invitado'] = texto
        link, mensaje = crear_evento(session['fecha_hora'], session['email_invitado'])
        respuesta = mensaje if link else "⚠️ Error al crear el evento. " + mensaje
        if link:
            guardar_reserva(session['fecha_hora'], session['email_invitado'], link)
        session['step'] = 'inicio'

    session['historial'].append({"role": "assistant", "content": respuesta})
    return respuesta

@app.route('/get_reservas')
def get_reservas():
    with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
        reservas = json.load(f)
    return json.dumps(reservas)

TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Chat Reserva LaOrtiga.cl</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.2/socket.io.min.js" integrity="sha512-XC1PrYxgI+FAYDK9HT+Jxw5jWfS/j+RGEhf4MuzcGzQ2jtkxA0rpiLCbgvhLO6jF+Zem/YvQMQz0FkYxLQuO7w==" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<style>
body { font-family: Arial; padding: 20px; background: #f4f9f4; }
#chat { max-width: 500px; margin: auto; background: white; padding: 20px; border-radius: 10px; }
input, button { padding: 8px; margin: 5px 0; width: 100%; }
button { background: #2c7a2c; color: white; border: none; cursor: pointer; }
.bot { background: #2c7a2c; color: white; padding: 10px; border-radius: 10px; margin-bottom: 10px; }
.user { background: #a3d1a3; color: black; padding: 10px; border-radius: 10px; margin-bottom: 10px; text-align:right;}
h3 { margin-top: 30px; }
</style>
</head>
<body>
<div id="chat">
    <div class="bot" id="respuesta">{{ respuesta|safe }}</div>
    <form id="formulario" method="POST">
        <input type="text" name="mensaje" placeholder="Escribe tu respuesta..." required>
        <button type="submit">Enviar</button>
    </form>
    <h3>Historial de reservas (actualizado en tiempo real):</h3>
    <ul id="reservas_list"></ul>
</div>

<script>
const socket = io();
const reservasList = document.getElementById('reservas_list');

// Cargar historial inicial
fetch('/get_reservas').then(r => r.json()).then(reservas => {
    reservas.forEach(r => {
        reservasList.innerHTML += `<li>${r.fecha_hora} - ${r.email_invitado} - <a href="${r.link}" target="_blank">Ver evento</a></li>`;
    });
});

// Escuchar nuevas reservas
socket.on('nueva_reserva', function(r) {
    reservasList.innerHTML += `<li>${r.fecha_hora} - ${r.email_invitado} - <a href="${r.link}" target="_blank">Ver evento</a></li>`;
});

// Enviar chat sin recargar
const form = document.getElementById('formulario');
form.onsubmit = async (e) => {
    e.preventDefault();
    const formData = new FormData(form);
    const res = await fetch('/chat', {method:'POST', body: formData});
    const respuesta = await res.text();
    document.getElementById('respuesta').innerHTML = respuesta;
    form.reset();
};
</script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=True)
