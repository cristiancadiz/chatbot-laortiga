import os
import json
from flask import Flask, request, render_template_string, jsonify
from googleapiclient.discovery import build
from google.oauth2 import service_account

app = Flask(__name__)

# Cargar las credenciales desde una variable de entorno (asegúrate de tener la clave JSON de la cuenta de servicio)
service_account_info = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))

# Crear las credenciales de la cuenta de servicio
credentials = service_account.Credentials.from_service_account_info(service_account_info)

# Crear el servicio de Google Calendar
service = build('calendar', 'v3', credentials=credentials)

# HTML Template para crear evento
HTML_TEMPLATE = '''
<!doctype html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Crear Evento en Google Calendar</title>
</head>
<body>
  <h1>Crear un Evento en Google Calendar</h1>
  <form action="/create_event" method="post">
    <label for="title">Título del Evento:</label>
    <input type="text" id="title" name="title" required><br><br>

    <label for="start_time">Fecha y Hora de Inicio (YYYY-MM-DD HH:MM):</label>
    <input type="text" id="start_time" name="start_time" required><br><br>

    <label for="end_time">Fecha y Hora de Fin (YYYY-MM-DD HH:MM):</label>
    <input type="text" id="end_time" name="end_time" required><br><br>

    <label for="attendee_email">Correo Electrónico del Asistente:</label>
    <input type="email" id="attendee_email" name="attendee_email" required><br><br>

    <button type="submit">Crear Evento</button>
  </form>

  {% if message %}
    <h2>{{ message }}</h2>
    {% if event_link %}
      <p><a href="{{ event_link }}" target="_blank">Ver el Evento en Google Calendar</a></p>
    {% endif %}
  {% endif %}
</body>
</html>
'''

@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

@app.route('/create_event', methods=['POST'])
def create_event():
    # Obtener los datos del formulario
    title = request.form.get('title')
    start_time = request.form.get('start_time')
    end_time = request.form.get('end_time')
    attendee_email = request.form.get('attendee_email')

    # Verificación básica de campos
    if not title or not start_time or not end_time or not attendee_email:
        return render_template_string(HTML_TEMPLATE, message="Todos los campos son obligatorios")

    # Asegurarse de que las fechas estén en el formato adecuado (YYYY-MM-DD HH:MM)
    try:
        start_time = start_time + ":00-00:00"  # Agregar segundos y zona horaria
        end_time = end_time + ":00-00:00"  # Agregar segundos y zona horaria
    except Exception as e:
        return render_template_string(HTML_TEMPLATE, message=f"Error al formatear las fechas: {str(e)}")

    # Formato del evento para Google Calendar
    event = {
        'summary': title,
        'location': 'Online',
        'description': 'Evento creado mediante la API',
        'start': {
            'dateTime': start_time,  # Fecha y hora de inicio
            'timeZone': 'America/Los_Angeles',
        },
        'end': {
            'dateTime': end_time,  # Fecha y hora de finalización
            'timeZone': 'America/Los_Angeles',
        },
        'attendees': [
            {'email': attendee_email},
        ],
        'reminders': {
            'useDefault': True,
        },
    }

    try:
        # Crear el evento en el calendario
        event_result = service.events().insert(calendarId='primary', body=event).execute()
        return render_template_string(HTML_TEMPLATE, message="¡Evento creado con éxito!", event_link=event_result.get('htmlLink'))
    except Exception as e:
        # Captura el error y proporciona detalles adicionales
        return render_template_string(HTML_TEMPLATE, message=f"Hubo un error al crear el evento: {str(e)}")

# Ejecutar la aplicación Flask
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
