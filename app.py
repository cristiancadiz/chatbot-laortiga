import os
from flask import Flask, request, session, render_template_string
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import timedelta, datetime
import openai
from dotenv import load_dotenv
import dateparser
import pytz  

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('app.secret_key')
app.permanent_session_lifetime = timedelta(days=30)

# Variables de entorno
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise Exception("La variable de entorno OPENAI_API_KEY no está configurada.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

GOOGLE_CALENDAR_TOKEN = os.getenv('GOOGLE_CALENDAR_TOKEN')  # Token fijo
GOOGLE_CALENDAR_REFRESH_TOKEN = os.getenv('GOOGLE_CALENDAR_REFRESH_TOKEN')
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET')

FIXED_EMAIL = "tu-correo@gmail.com"  # Correo que se usará para agendar

def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

def crear_evento_google_calendar(fecha_hora):
    creds = Credentials(
        token=GOOGLE_CALENDAR_TOKEN,
        refresh_token=GOOGLE_CALENDAR_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/calendar.events"]
    )
    service = build('calendar', 'v3', credentials=creds)

    inicio = dateparser.parse(
        fecha_hora,
        settings={
            "PREFER_DATES_FROM": "future",
            "RETURN_AS_TIMEZONE_AWARE": True,
            "TIMEZONE": "America/Santiago",
            "TO_TIMEZONE": "America/Santiago",
            "RELATIVE_BASE": datetime.now(pytz.timezone("America/Santiago"))
        }
    )
    if not inicio:
        return "⚠️ No pude entender la fecha y hora. Intenta con algo como: 'mañana a las 10' o 'el jueves a las 4pm'."

    fin = inicio + timedelta(minutes=30)

    evento = {
        'summary': 'Consulta con LaOrtiga.cl',
        'description': 'Reserva automatizada 🌱',
        'start': {'dateTime': inicio.isoformat(), 'timeZone': 'America/Santiago'},
        'end': {'dateTime': fin.isoformat(), 'timeZone': 'America/Santiago'},
        'attendees': [{'email': FIXED_EMAIL}],
    }

    evento = service.events().insert(calendarId='primary', body=evento).execute()
    return f"✅ Evento creado: <a href=\"{evento.get('htmlLink')}\" target=\"_blank\">Ver en tu calendario</a>"

@app.route('/', methods=['GET', 'POST'])
def chat():
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿En qué puedo ayudarte hoy?"
        }]

    respuesta = ""

    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()
        if pregunta:
            session['historial'].append({"role": "user", "content": pregunta})

            # Detectar intención de agendar
            if any(p in pregunta.lower() for p in ['agendar', 'reserva', 'cita', 'calendar']):
                respuesta = "¿Para qué día y hora quieres agendar? (por ejemplo: 'mañana a las 10')"
            # Si responde con fecha/hora
            elif dateparser.parse(pregunta):
                respuesta = crear_evento_google_calendar(pregunta)
            else:
                # Llamada normal a OpenAI
                mensajes = [
                    {"role": "system", "content": "Eres un asistente conversacional de LaOrtiga.cl. Habla de forma amable, cercana y profesional. Solo responde preguntas sobre sostenibilidad, productos ecológicos o emprendimiento verde."}
                ] + session['historial'][-10:]

                completion = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=mensajes,
                    max_tokens=200,
                    temperature=0.7
                )
                respuesta = completion.choices[0].message.content.strip()

            session['historial'].append({"role": "assistant", "content": respuesta})
            guardar_historial_en_archivo(session['historial'])

    return render_template_string(TEMPLATE, historial=session['historial'])

TEMPLATE = """
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Asistente La Ortiga</title>
</head>
<body>
    <h2>Asistente La Ortiga 🌱</h2>
    <div id="chat">
        {% for m in historial %}
            <div><strong>{{ 'Tú' if m.role=='user' else 'Bot' }}:</strong> {{ m.content | safe }}</div>
        {% endfor %}
    </div>
    <form method="POST">
        <input type="text" name="pregunta" placeholder="Escribe tu mensaje..." required>
        <button>Enviar</button>
    </form>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
