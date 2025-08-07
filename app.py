from flask import Flask, jsonify
import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account

app = Flask(__name__)

@app.route('/')
def create_event():
    # Cargar las credenciales desde una variable de entorno
    service_account_info = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))

    # Crear las credenciales de la cuenta de servicio
    credentials = service_account.Credentials.from_service_account_info(service_account_info)

    # Crear el servicio de Google Calendar
    service = build('calendar', 'v3', credentials=credentials)

    # Datos del evento (sin asistentes)
    event = {
        'summary': 'Reunión de trabajo',
        'location': 'Oficina de Ejemplo',
        'description': 'Discutir los detalles del proyecto.',
        'start': {
            'dateTime': '2025-08-07T09:00:00-07:00',  # Fecha y hora de inicio
            'timeZone': 'America/Los_Angeles',
        },
        'end': {
            'dateTime': '2025-08-07T10:00:00-07:00',  # Fecha y hora de finalización
            'timeZone': 'America/Los_Angeles',
        },
        'reminders': {
            'useDefault': True,
        },
    }

    try:
        event_result = service.events().insert(calendarId='primary', body=event).execute()
        return jsonify({'message': 'Evento creado', 'event_url': event_result.get('htmlLink')})

    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Asegurarse de que se use el puerto proporcionado por Render
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
