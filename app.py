import os
from flask import Flask, redirect, url_for, session, request, render_template_string, flash
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "tu_secret_key_aqui")

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = "https://chatbot-laortiga-3-zvsx.onrender.com/oauth2callback"  # cambia por tu URL en render + /oauth2callback
SCOPES = ['https://www.googleapis.com/auth/calendar.events']

# Template HTML embebido0
HTML_TEMPLATE = """
<!doctype html>
<title>Crear Evento Google Calendar</title>
<h2>Crear un Evento en Google Calendar</h2>
{% with messages = get_flashed_messages() %}
  {% if messages %}
    <ul style="color:red;">
    {% for msg in messages %}
      <li>{{ msg }}</li>
    {% endfor %}
    </ul>
  {% endif %}
{% endwith %}
{% if not session.get('credentials') %}
  <a href="{{ url_for('authorize') }}">Iniciar sesión con Google para crear eventos</a>
{% else %}
  <form method="post" action="{{ url_for('create_event') }}">
    <label>Título del Evento:</label><br>
    <input name="title" required><br><br>
    
    <label>Fecha y Hora de Inicio (YYYY-MM-DD HH:MM):</label><br>
    <input name="start" placeholder="2025-08-08 15:00" required><br><br>
    
    <label>Fecha y Hora de Fin (YYYY-MM-DD HH:MM):</label><br>
    <input name="end" placeholder="2025-08-08 16:00" required><br><br>
    
    <label>Correo Electrónico del Asistente (opcional):</label><br>
    <input name="attendee_email" placeholder="correo@ejemplo.com"><br><br>
    
    <button type="submit">Crear Evento</button>
  </form>
  <br>
  <a href="{{ url_for('logout') }}">Cerrar sesión</a>
{% endif %}
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/authorize')
def authorize():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        prompt='consent'
    )
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    state = session.get('state')
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [REDIRECT_URI]
            }
        },
        scopes=SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI
    )
    authorization_response = request.url
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    session['credentials'] = {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }
    return redirect(url_for('index'))

@app.route('/create_event', methods=['POST'])
def create_event():
    if 'credentials' not in session:
        flash("No autenticado, por favor inicia sesión.")
        return redirect(url_for('index'))
    
    credentials = Credentials(**session['credentials'])
    service = build('calendar', 'v3', credentials=credentials)
    
    title = request.form.get('title')
    start_str = request.form.get('start')
    end_str = request.form.get('end')
    attendee_email = request.form.get('attendee_email')
    
    # Validar formato básico de fecha y hora
    try:
        from datetime import datetime
        start_dt = datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        if end_dt <= start_dt:
            flash("La fecha y hora de fin debe ser posterior a la de inicio.")
            return redirect(url_for('index'))
    except Exception as e:
        flash("Formato de fecha y hora incorrecto. Use YYYY-MM-DD HH:MM")
        return redirect(url_for('index'))
    
    event = {
        'summary': title,
        'start': {
            'dateTime': start_dt.isoformat(),
            'timeZone': 'America/Santiago',  # Cambia a tu zona horaria
        },
        'end': {
            'dateTime': end_dt.isoformat(),
            'timeZone': 'America/Santiago',
        },
    }
    if attendee_email:
        event['attendees'] = [{'email': attendee_email}]
    
    try:
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        flash(f"Evento creado: {created_event.get('htmlLink')}")
    except Exception as e:
        flash(f"Error al crear el evento: {e}")
    
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('credentials', None)
    flash("Sesión cerrada.")
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')

