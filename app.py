import os
import json
from flask import Flask, request, render_template_string
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import dateparser
from datetime import timedelta
import pytz

app = Flask(__name__)
app.secret_key = os.getenv('APP_SECRET_KEY', 'clave_secreta_para_testing')

SCOPES = ['https://www.googleapis.com/auth/calendar.events']

# Ruta temporal para guardar token.json
TOKEN_PATH = 'token.json'

def guardar_token_desde_env():
    token_json_str = os.getenv('GOOGLE_TOKEN_JSON')
    if not token_json_str:
        raise Exception("La variable de entorno GOOGLE_TOKEN_JSON no está configurada.")
    # Guarda el contenido en token.json
    with open(TOKEN_PATH, 'w') as f:
        f.write(token_json_str)

def crear_evento_tu_calendario(fecha_hora, nombre_usuario, email_usuario=None):
    # Asegura que token.json existe
    if not os.path.exists(TOKEN_PATH):
        guardar_token_desde_env()

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    service = build('calendar', 'v3', credentials=creds)

    inicio = dateparser.parse(fecha_hora, settings={
        "PREFER_DATES_FROM": "future",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "TIMEZONE": "America/Santiago",
        "TO_TIMEZONE": "America/Santiago"
    })

    if not inicio:
        return None, "⚠️ No pude entender la fecha y hora. Usa algo como 'mañana a las 10am'."

    fin = inicio + timedelta(minutes=30)

    evento = {
        'summary': f'Cita con {nombre_usuario}',
        'description': 'Reserva automática desde el sitio web',
        'start': {
            'dateTime': inicio.isoformat(),
            'timeZone': 'America/Santiago',
        },
        'end': {
            'dateTime': fin.isoformat(),
            'timeZone': 'America/Santiago',
        },
    }

    if email_usuario:
        evento['attendees'] = [{'email': email_usuario}]

    creado = service.events().insert(calendarId='primary', body=evento).execute()
    return creado.get('htmlLink'), None


@app.route('/', methods=['GET', 'POST'])
def home():
    mensaje = ""
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        fecha = request.form.get('fecha', '').strip()
        email = request.form.get('email', '').strip() or None

        if not nombre or not fecha:
            mensaje = "Por favor completa tu nombre y la fecha/hora de la cita."
        else:
            link, error = crear_evento_tu_calendario(fecha, nombre, email)
            if error:
                mensaje = error
            else:
                mensaje = f"✅ ¡Tu cita fue agendada con éxito! Puedes verla <a href='{link}' target='_blank'>aquí</a>. Te contactaremos pronto."

    return render_template_string(TEMPLATE, mensaje=mensaje)


TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Agendar Cita - LaOrtiga.cl</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
        body {
            font-family: Arial, sans-serif;
            background: #f4f9f4;
            padding: 20px;
            max-width: 400px;
            margin: auto;
        }
        h1 {
            color: #2c7a2c;
            text-align: center;
        }
        form {
            background: white;
            padding: 15px;
            border-radius: 8px;
            box-shadow: 0 0 10px rgba(0,0,0,0.1);
        }
        label {
            display: block;
            margin-top: 15px;
            font-weight: bold;
        }
        input[type="text"],
        input[type="email"] {
            width: 100%;
            padding: 8px;
            margin-top: 5px;
            border: 1px solid #ccc;
            border-radius: 6px;
            box-sizing: border-box;
        }
        button {
            margin-top: 20px;
            width: 100%;
            padding: 10px;
            background: #2c7a2c;
            border: none;
            color: white;
            font-size: 1.1rem;
            border-radius: 6px;
            cursor: pointer;
        }
        button:hover {
            background: #246d24;
        }
        .mensaje {
            margin-top: 20px;
            padding: 15px;
            background: #dff0d8;
            border: 1px solid #d0e9c6;
            color: #3c763d;
            border-radius: 6px;
        }
        .error {
            background: #f2dede;
            border-color: #ebcccc;
            color: #a94442;
        }
        a {
            color: #2c7a2c;
            text-decoration: none;
            font-weight: bold;
        }
        a:hover {
            text-decoration: underline;
        }
    </style>
</head>
<body>
    <h1>Agendar una Cita</h1>
    <form method="POST">
        <label for="nombre">Tu Nombre:</label>
        <input type="text" id="nombre" name="nombre" placeholder="Ej: Juan Pérez" required />

        <label for="fecha">Fecha y Hora de la Cita:</label>
        <input type="text" id="fecha" name="fecha" placeholder="Ej: mañana a las 10am" required />

        <label for="email">Correo Electrónico (opcional):</label>
        <input type="email" id="email" name="email" placeholder="Ej: correo@ejemplo.com" />

        <button type="submit">Agendar</button>
    </form>

    {% if mensaje %}
        <div class="mensaje">{{ mensaje | safe }}</div>
    {% endif %}
</body>
</html>
"""

if __name__ == '__main__':
    # Guarda token.json la primera vez que arranca la app
    guardar_token_desde_env()

    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
