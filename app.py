import os
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Ruta correcta al archivo de credenciales
SERVICE_ACCOUNT_FILE = '/opt/render/project/src/service-account-file.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']

# Autenticación con las credenciales de la cuenta de servicio
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)

# Crear el servicio de Google Calendar
service = build('calendar', 'v3', credentials=credentials)

# Datos del evento
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
    'attendees': [
        {'email': 'correo@ejemplo.com'},
    ],
    'reminders': {
        'useDefault': True,
    },
}

# Crear el evento en el calendario
event_result = service.events().insert(calendarId='primary', body=event).execute()

print(f"Evento creado: {event_result.get('htmlLink')}")
