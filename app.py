import os
import json
from flask import Flask, request, render_template_string
from googleapiclient.discovery import build
from google.oauth2 import service_account

app = Flask(__name__)

# Cargar las credenciales desde una variable de entorno
service_account_info = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))
credentials = service_account.Credentials.from_service_account_info(service_account_info)

# Crear el servicio de Google Calendar
service = build('calendar', 'v3', credentials=credentials)

# Template HTML en el mismo archivo
HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crear Evento en Google Calendar</title>
</head>
<body>
    <h1>Crear un evento en Google Calendar</h1>
    <form action="/create_event" method="post">
        <label for="summary">Título del evento:</label><br>
        <input type="text" id="summary" name="summary" required><br><br>

        <label for="start_datetime">Fecha y hora de inicio (YYYY-MM-DD HH:MM):</label><br>
        <input type="text" id="start_datetime" name="start_datetime" required><br><br>

        <label for="end_datetime">Fecha y hora de fin (YYYY-MM-DD HH:MM):</label><br>
        <input type="text" id="end_datetime" name="end_datetime" required><br><br>

        <label for="attendee_email">Correo electrónico del asistente:</label><br>
        <input type="email" id="attendee_email" name="attendee_email" required><br><br>

        <input type="submit" value="Crear Evento">
    </form>
</body>
</html>
'''

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/create_event', methods=['POST'])
def create_event():
    try:
        # Obtener datos del formulario
        summary = request.form['summary']
        start_datetime = request.form['start_datetime']
        end_datetime = request.form['end_datetime']
        attendee_email = request.form['attendee_email']

        # Convertir las fechas a formato datetime con zona horaria
        event = {
            'summary': summary,
            'location': 'En línea',
            'description': 'Reunión programada.',
            'start': {
                'dateTime': f'{start_datetime}:00-07:00',  # Usamos la zona horaria de Los Ángeles (-07:00)
                'timeZone': 'America/Los_Angeles',
            },
            'end': {
                'dateTime': f'{end_datetime}:00-07:00',
                'timeZone': 'America/Los_Angeles',
            },
            'attendees': [
                {'email': attendee_email},
            ],
            'reminders': {
                'useDefault': True,
            },
        }

        # Crear el evento en el calendario
        event_result = service.events().insert(calendarId='primary', body=event).execute()

        # Responder con el enlace del evento creado
        return f'''
            ¡El evento ha sido creado con éxito!<br>
            Puede ver el evento en su calendario haciendo clic en el siguiente enlace:<br>
            <a href="{event_result.get('htmlLink')}" target="_blank">Ver evento en Google Calendar</a>
        '''

    except Exception as e:
        return f"Hubo un error al crear el evento: {e}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)

