from flask import Flask, request, render_template_string
from google.oauth2 import service_account
from googleapiclient.discovery import build
import dateparser
from datetime import datetime, timedelta
import pytz
import os

app = Flask(__name__)

# Ruta al archivo JSON con credenciales de cuenta de servicio
SERVICE_ACCOUNT_FILE = 'credenciales_calendar.json'  # ponlo en .gitignore
SCOPES = ['https://www.googleapis.com/auth/calendar']
CALENDAR_ID = 'cristiancadiz987@gmailcom'  # el correo del calendario compartido

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)

@app.route('/', methods=['GET', 'POST'])
def home():
    mensaje = ""
    if request.method == 'POST':
        correo = request.form.get('correo')
        fecha_texto = request.form.get('fecha')

        if not correo or not fecha_texto:
            mensaje = "⚠️ Por favor completa ambos campos."
        else:
            mensaje = crear_evento_y_enviar_invite(correo, fecha_texto)

    return render_template_string(TEMPLATE, mensaje=mensaje)

def crear_evento_y_enviar_invite(correo, fecha_texto):
    try:
        service = build('calendar', 'v3', credentials=credentials)

        # Analiza la fecha
        zona = pytz.timezone("America/Santiago")
        inicio = dateparser.parse(
            fecha_texto,
            settings={
                'PREFER_DATES_FROM': 'future',
                'RETURN_AS_TIMEZONE_AWARE': True,
                'TIMEZONE': 'America/Santiago',
                'TO_TIMEZONE': 'UTC',
                'RELATIVE_BASE': datetime.now(zona)
            }
        )

        if not inicio:
            return "⚠️ No pude entender la fecha y hora. Intenta con algo como: 'mañana a las 10'."

        fin = inicio + timedelta(minutes=30)

        evento = {
            'summary': 'Consulta con LaOrtiga.cl',
            'description': 'Reserva automatizada con Capitán Planeta 🌱',
            'start': {
                'dateTime': inicio.isoformat(),
                'timeZone': 'America/Santiago',
            },
            'end': {
                'dateTime': fin.isoformat(),
                'timeZone': 'America/Santiago',
            },
            'attendees': [{'email': correo}],
            'guestsCanModify': False,
            'guestsCanInviteOthers': False,
            'guestsCanSeeOtherGuests': False,
            'reminders': {
                'useDefault': True,
            }
        }

        evento = service.events().insert(calendarId=CALENDAR_ID, body=evento, sendUpdates='all').execute()

        return f"✅ Evento creado y enviado a <b>{correo}</b>. <a href='{evento.get('htmlLink')}' target='_blank'>Ver evento</a>"

    except Exception as e:
        return f"❌ Error al crear el evento: {str(e)}"


TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Agendar cita - LaOrtiga</title>
    <meta charset="UTF-8">
</head>
<body>
    <h1>Reserva una consulta 🌱</h1>
    <form method="POST">
        <label>Tu correo:</label><br>
        <input type="email" name="correo" required><br><br>

        <label>¿Cuándo quieres agendar?</label><br>
        <input type="text" name="fecha" placeholder="Ej: mañana a las 10" required><br><br>

        <button type="submit">Agendar</button>
    </form>
    <p>{{ mensaje | safe }}</p>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True)

