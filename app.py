import os
import json
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from flask import Flask, jsonify

app = Flask(__name__)

# Función para obtener las credenciales desde la variable de entorno
def get_credentials_from_env():
    # Recupera el token JSON desde la variable de entorno
    token_data = os.getenv('GOOGLE_TOKEN_JSON')

    if not token_data:
        raise ValueError("La variable de entorno 'GOOGLE_TOKEN_JSON' no está configurada correctamente.")
    
    # Convertimos el JSON de la variable de entorno en un diccionario
    creds_dict = json.loads(token_data)

    # Verifica que los campos necesarios existan en el diccionario
    required_fields = ['token', 'expiry', '_token_uri', '_client_id', '_client_secret', '_refresh_token']
    for field in required_fields:
        if field not in creds_dict:
            raise ValueError(f"Falta el campo '{field}' en el token JSON.")

    # Crear el objeto de credenciales a partir de los datos JSON
    creds = Credentials.from_authorized_user_info(creds_dict)
    
    # Si las credenciales han expirado, se necesita refrescarlas
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return creds

# Función para obtener eventos del calendario de Google
def get_calendar_events():
    try:
        # Obtener las credenciales
        creds = get_credentials_from_env()

        # Construir el servicio de la API de Google Calendar
        service = build('calendar', 'v3', credentials=creds)

        # Obtener los próximos 10 eventos del calendario
        events_result = service.events().list(
            calendarId='primary', timeMin='2025-01-01T00:00:00Z', maxResults=10, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])

        return events

    except Exception as e:
        print(f"Error al obtener eventos: {e}")
        return []

# Ruta para mostrar los eventos del calendario
@app.route('/')
def show_events():
    events = get_calendar_events()
    if not events:
        return jsonify({'message': 'No upcoming events found.'}), 200
    return jsonify({'events': events}), 200

if __name__ == '__main__':
    app.run(debug=True)
