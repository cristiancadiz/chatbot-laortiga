import os
import json
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from flask import Flask, jsonify, render_template_string

app = Flask(__name__)

# El alcance de la API que vas a usar (Google Calendar)
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

# Función para obtener las credenciales de Google desde la variable de entorno
def get_credentials_from_env():
    """Obtiene las credenciales de Google desde la variable de entorno GOOGLE_TOKEN_JSON"""
    token_data = os.getenv('GOOGLE_TOKEN_JSON')
    
    if not token_data:
        raise ValueError("La variable de entorno 'GOOGLE_TOKEN_JSON' no está configurada correctamente.")
    
    # Convertimos el token JSON que está en formato string en un diccionario
    creds_dict = json.loads(token_data)

    # Crear el objeto de credenciales a partir de los datos JSON
    creds = Credentials.from_authorized_user_info(creds_dict)
    
    return creds

# Función para crear el servicio de la API de Google Calendar
def build_calendar_service(creds):
    """Construye el servicio de Google Calendar"""
    try:
        service = build('calendar', 'v3', credentials=creds)
        return service
    except HttpError as error:
        print(f'Error al construir el servicio de Google Calendar: {error}')
        return None

# Ruta principal para renderizar la página de inicio (HTML template)
@app.route('/')
def index():
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Chatbot Calendar Events</title>
            <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
            <style>
                body {
                    background-color: #f5f5f5;
                    padding: 50px;
                }
                .container {
                    background-color: #fff;
                    border-radius: 10px;
                    padding: 30px;
                    box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
                }
                h1 {
                    color: #007bff;
                }
                .card {
                    margin-top: 20px;
                }
            </style>
        </head>
        <body>

            <div class="container">
                <h1 class="text-center">Bienvenido al Chatbot de Eventos de Google Calendar</h1>
                <p class="text-center">A continuación se listan los próximos eventos en tu calendario de Google:</p>

                <div id="events-container">
                    <!-- Los eventos se mostrarán aquí -->
                </div>

                <div class="text-center">
                    <button class="btn btn-primary" onclick="fetchEvents()">Obtener Eventos</button>
                </div>
            </div>

            <script>
                function fetchEvents() {
                    fetch('/events')
                        .then(response => response.json())
                        .then(data => {
                            let eventsContainer = document.getElementById('events-container');
                            eventsContainer.innerHTML = '';  // Limpiar cualquier evento anterior

                            if (data.error) {
                                eventsContainer.innerHTML = `<p class="text-danger">${data.error}</p>`;
                            } else if (data.message) {
                                eventsContainer.innerHTML = `<p class="text-info">${data.message}</p>`;
                            } else {
                                let eventsList = '<ul class="list-group">';
                                data.forEach(event => {
                                    eventsList += `
                                        <li class="list-group-item">
                                            <strong>${event.summary}</strong><br>
                                            ${new Date(event.start.dateTime || event.start.date).toLocaleString()}<br>
                                            <a href="${event.htmlLink}" target="_blank">Ver en Google Calendar</a>
                                        </li>
                                    `;
                                });
                                eventsList += '</ul>';
                                eventsContainer.innerHTML = eventsList;
                            }
                        })
                        .catch(error => {
                            console.error('Error al obtener eventos:', error);
                            document.getElementById('events-container').innerHTML = `<p class="text-danger">Hubo un problema al obtener los eventos.</p>`;
                        });
                }
            </script>

        </body>
        </html>
    ''')

# Función para listar los próximos eventos en el calendario
@app.route('/events', methods=['GET'])
def get_events():
    try:
        # Obtener las credenciales desde la variable de entorno
        creds = get_credentials_from_env()

        # Construir el servicio de la API de Google Calendar
        service = build_calendar_service(creds)

        if not service:
            return jsonify({'error': 'No se pudo construir el servicio de Google Calendar.'}), 500

        # Obtener los próximos eventos en el calendario
        events_result = service.events().list(calendarId='primary', timeMin='2025-08-01T00:00:00Z', maxResults=10, singleEvents=True, orderBy='startTime').execute()
        events = events_result.get('items', [])

        # Si no hay eventos
        if not events:
            return jsonify({'message': 'No se encontraron eventos.'}), 200

        # Devolver los eventos en formato JSON
        return jsonify(events), 200

    except Exception as e:
        print(f'Error: {e}')
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Ejecutar la app de Flask
    app.run(debug=True, host='0.0.0.0', port=5000)
