import os
from flask import Flask, request, render_template_string, redirect
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import dateparser
import pytz

# ---------------- CONFIG ----------------
# Coloca aquí tu archivo de cuenta de servicio
SERVICE_ACCOUNT_FILE = "laortiga-chatbot-468222-cfc973bda5d3.json"

# El calendario central donde se crearán los eventos
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID")  # ejemplo: cristiancadiz987@gmail.com

# ---------------- APP ----------------
app = Flask(__name__)
app.secret_key = os.getenv('APP_SECRET_KEY', 'supersecretkey')
app.permanent_session_lifetime = timedelta(days=30)

# ---------------- CREDENCIALES ----------------
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE,
    scopes=['https://www.googleapis.com/auth/calendar']
)

service = build('calendar', 'v3', credentials=credentials)

# ---------------- FUNCIONES ----------------
def crear_evento(fecha_hora, correo_invitado):
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
        return "⚠️ No pude entender la fecha y hora. Intenta con algo como: 'mañana a las 10' o 'el jueves a las 16'."

    fin = inicio + timedelta(minutes=30)
    evento = {
        'summary': 'Consulta con LaOrtiga.cl',
        'description': 'Reserva automatizada con LaOrtiga 🌱',
        'start': {
            'dateTime': inicio.isoformat(),
            'timeZone': 'America/Santiago'
        },
        'end': {
            'dateTime': fin.isoformat(),
            'timeZone': 'America/Santiago'
        },
        'attendees': [{'email': correo_invitado}],
        'reminders': {'useDefault': True},
    }
    creado = service.events().insert(calendarId=CALENDAR_ID, body=evento, sendUpdates='all').execute()
    return f"✅ Evento creado y enviado a {correo_invitado}: <a href='{creado.get('htmlLink')}' target='_blank'>Ver en calendario</a>"

# ---------------- RUTAS ----------------
@app.route('/')
def home():
    return redirect('/chat')

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    mensaje = ""
    if request.method == 'POST':
        fecha_hora = request.form.get('fecha')
        correo = request.form.get('correo')
        if fecha_hora and correo:
            mensaje = crear_evento(fecha_hora, correo)
    return render_template_string("""
    <!DOCTYPE html>
    <html lang="es">
    <head><meta charset="UTF-8"><title>Reservas LaOrtiga</title></head>
    <body>
        <h2>Reserva una cita</h2>
        <form method="POST">
            Fecha y hora: <input type="text" name="fecha" placeholder="Ej: mañana a las 12"><br><br>
            Correo del invitado: <input type="email" name="correo" placeholder="correo@ejemplo.com"><br><br>
            <button type="submit">Agendar</button>
        </form>
        <p>{{ mensaje|safe }}</p>
    </body>
    </html>
    """, mensaje=mensaje)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
