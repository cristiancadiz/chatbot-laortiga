import os
import json
from flask import Flask, render_template_string
from googleapiclient.discovery import build
from google.oauth2 import service_account

# Crear la aplicación Flask
app = Flask(__name__)

# Página principal para crear el evento
@app.route('/')
def create_event():
    try:
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

        # Crear el evento en el calendario
        event_result = service.events().insert(calendarId='primary', body=event).execute()
        event_url = event_result.get('htmlLink')

        # HTML para mostrar el evento creado
        html_content = '''
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Calendario - Crear Evento</title>
            <style>
                body {
                    font-family: Arial, sans-serif;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 0;
                }
                .container {
                    width: 80%;
                    margin: 50px auto;
                    background-color: #fff;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }
                h1 {
                    text-align: center;
                    color: #333;
                }
                a {
                    display: block;
                    margin-top: 20px;
                    text-align: center;
                    color: #007bff;
                    text-decoration: none;
                }
                a:hover {
                    text-decoration: underline;
                }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Evento de Google Calendar</h1>
                <p>¡El evento ha sido creado con éxito!</p>
                <p>Puede ver el evento en su calendario haciendo clic en el siguiente enlace:</p>
                <a href="{{ event_url }}" target="_blank">Ver evento en Google Calendar</a>
            </div>
        </body>
        </html>
        '''

        return render_template_string(html_content, event_url=event_url)

    except Exception as e:
        error_message = str(e)
        # HTML para mostrar el error
        error_html_content = f'''
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Error al crear evento</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background-color: #f4f4f4;
                    margin: 0;
                    padding: 0;
                }}
                .container {{
                    width: 80%;
                    margin: 50px auto;
                    background-color: #fff;
                    padding: 20px;
                    border-radius: 8px;
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                h1 {{
                    text-align: center;
                    color: #333;
                }}
                p {{
                    color: red;
                    text-align: center;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Error al crear el evento</h1>
                <p>{error_message}</p>
            </div>
        </body>
        </html>
        '''

        return render_template_string(error_html_content)


if __name__ == '__main__':
    # Asegurarse de que se use el puerto proporcionado por Render
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
