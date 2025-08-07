import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# Cargar las credenciales desde una variable de entorno
# Asegúrate de que la variable de entorno 'GOOGLE_APPLICATION_CREDENTIALS' esté correctamente configurada
service_account_info = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))

# Crear las credenciales de la cuenta de servicio
credentials = service_account.Credentials.from_service_account_info(service_account_info)

# Crear el servicio de Google Calendar
service = build('calendar', 'v3', credentials=credentials)

# HTML Template de la página para crear el evento
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Crear Evento - Google Calendar</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            padding: 30px;
            background-color: #f5f5f5;
        }
        h1 {
            text-align: center;
        }
        form {
            background-color: #fff;
            padding: 20px;
            border-radius: 5px;
            box-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
            max-width: 400px;
            margin: 0 auto;
        }
        label {
            font-size: 14px;
            margin-bottom: 5px;
            display: block;
        }
        input, button {
            width: 100%;
            padding: 10px;
            margin: 10px 0;
            font-size: 16px;
            border: 1px solid #ccc;
            border-radius: 5px;
        }
        button {
            background-color: #4CAF50;
            color: white;
            border: none;
        }
        button:hover {
            background-color: #45a049;
        }
        .result {
            margin-top: 20px;
            text-align: center;
        }
    </style>
</head>
<body>
    <h1>Crear un Evento en Google Calendar</h1>
    <form method="POST" action="/create_event">
        <label for="title">Título del Evento:</label>
        <input type="text" id="title" name="title" required>
        
        <label for="start_time">Fecha y Hora de Inicio (YYYY-MM-DD HH:MM):</label>
        <input type="text" id="start_time" name="start_time" required>
        
        <label for="end_time">Fecha y Hora de Fin (YYYY-MM-DD HH:MM):</label>
        <input type="text" id="end_time" name="end_time" required>
        
        <label for="attendee_email">Correo Electrónico del Asistente:</label>
        <input type="email" id="attendee_email" name="attendee_email" required>
        
        <button type="submit">Crear Evento</button>
    </form>

    <div class="result" id="result">
        <!-- El resultado del evento creado se mostrará aquí -->
    </div>

    <script>
        // Controlar el formulario con JavaScript
        const form = document.querySelector('form');
        const resultDiv = document.getElementById('result');

        form.addEventListener('submit', async (event) => {
            event.preventDefault();

            const formData = new FormData(form);
            const data = {
                title: formData.get('title'),
                start_time: formData.get('start_time'),
                end_time: formData.get('end_time'),
                attendee_email: formData.get('attendee_email')
            };

            try {
                const response = await fetch('/create_event', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(data),
                });

                const result = await response.json();
                if (response.ok) {
                    resultDiv.innerHTML = `<p>¡Evento creado con éxito! <a href="${result.event_link}" target="_blank">Ver Evento</a></p>`;
                } else {
                    resultDiv.innerHTML = `<p>Error al crear el evento: ${result.error}</p>`;
                }
            } catch (error) {
                resultDiv.innerHTML = `<p>Error al crear el evento: ${error}</p>`;
            }
        });
    </script>
</body>
</html>
"""

# Ruta para mostrar el formulario de creación de evento
@app.route('/')
def home():
    return render_template_string(HTML_TEMPLATE)

# Ruta para crear el evento
@app.route('/create_event', methods=['POST'])
def create_event():
    # Obtener datos del formulario
    data = request.get_json()

    title = data.get('title')
    start_time = data.get('start_time')
    end_time = data.get('end_time')
    attendee_email = data.get('attendee_email')

    # Formato del evento
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
        return jsonify({
            'message': '¡Evento creado con éxito!',
            'event_link': event_result.get('htmlLink')
        })
    except Exception as e:
        return jsonify({'error': f'Hubo un error al crear el evento: {str(e)}'}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
