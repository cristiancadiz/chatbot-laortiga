import os
from flask import Flask, request, render_template, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
import datetime

app = Flask(__name__)
app.secret_key = os.environ.get("app.secret_key", "supersecretkey")

# Configuración de Google Calendar
SERVICE_ACCOUNT_INFO = {
    "type": "service_account",
    "project_id": os.environ.get("GOOGLE_PROJECT_ID"),
    "private_key_id": os.environ.get("GOOGLE_PRIVATE_KEY_ID"),
    "private_key": os.environ.get("GOOGLE_PRIVATE_KEY").replace("\\n", "\n"),
    "client_email": os.environ.get("GOOGLE_CLIENT_EMAIL"),
    "client_id": os.environ.get("GOOGLE_CLIENT_ID_SERVICE"),
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": os.environ.get("GOOGLE_CLIENT_X509_CERT_URL")
}

SCOPES = ['https://www.googleapis.com/auth/calendar.events']
credentials = service_account.Credentials.from_service_account_info(
    SERVICE_ACCOUNT_INFO, scopes=SCOPES
)

calendar_id = os.environ.get("GOOGLE_CALENDAR_ID")

service = build('calendar', 'v3', credentials=credentials)

@app.route('/')
def home():
    return render_template('chat.html')

@app.route('/chat', methods=['POST'])
def chat():
    data = request.json
    fecha_hora = data.get('datetime')  # ej: "2025-08-07 12:00"
    correo = data.get('email')
    descripcion = data.get('description', 'Cita agendada desde chatbot LaOrtiga')

    start_time = datetime.datetime.strptime(fecha_hora, "%Y-%m-%d %H:%M")
    end_time = start_time + datetime.timedelta(hours=1)

    evento = {
        'summary': 'Cita LaOrtiga',
        'description': descripcion,
        'start': {
            'dateTime': start_time.isoformat(),
            'timeZone': 'America/Santiago',
        },
        'end': {
            'dateTime': end_time.isoformat(),
            'timeZone': 'America/Santiago',
        },
        'attendees': [{'email': correo}],
        'reminders': {
            'useDefault': True,
        },
    }

    event = service.events().insert(calendarId=calendar_id, body=evento, sendUpdates='all').execute()

    return jsonify({"status": "ok", "event_id": event['id'], "message": f"Invitación enviada a {correo}"})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=True)
