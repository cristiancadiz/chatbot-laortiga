import os
from flask import Flask, request, render_template_string, session
from datetime import datetime, timedelta
import openai
import dateparser
import pytz
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Cargar variables de entorno
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise Exception("La variable OPENAI_API_KEY no está configurada.")

GOOGLE_CALENDAR_EMAIL = os.getenv("GOOGLE_CALENDAR_EMAIL")  # Correo del calendario
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")  # JSON del Service Account

if not GOOGLE_CALENDAR_EMAIL or not GOOGLE_SERVICE_ACCOUNT_FILE:
    raise Exception("Debes configurar GOOGLE_CALENDAR_EMAIL y GOOGLE_SERVICE_ACCOUNT_FILE.")

openai.api_key = OPENAI_API_KEY

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "supersecret")
app.permanent_session_lifetime = timedelta(days=30)

# Inicializa Google Calendar con Service Account
credentials = service_account.Credentials.from_service_account_file(
    GOOGLE_SERVICE_ACCOUNT_FILE,
    scopes=["https://www.googleapis.com/auth/calendar"]
)
calendar_service = build("calendar", "v3", credentials=credentials)

# Función para crear eventos
def crear_evento_google_calendar(fecha_hora, duracion_minutos=30):
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
        return "⚠️ No pude entender la fecha y hora. Usa algo como: 'mañana a las 10' o 'el jueves a las 16'."
    
    fin = inicio + timedelta(minutes=duracion_minutos)
    evento = {
        "summary": "Consulta con LaOrtiga.cl",
        "description": "Reserva automatizada 🌱",
        "start": {"dateTime": inicio.isoformat(), "timeZone": "America/Santiago"},
        "end": {"dateTime": fin.isoformat(), "timeZone": "America/Santiago"},
        "attendees": [{"email": GOOGLE_CALENDAR_EMAIL}],
    }

    creado = calendar_service.events().insert(
        calendarId=GOOGLE_CALENDAR_EMAIL, body=evento, sendUpdates="all"
    ).execute()
    return f"✅ Evento creado: <a href='{creado.get('htmlLink')}' target='_blank'>Ver en tu calendario</a>"

# Función para guardar historial
def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

# Rutas de Flask
@app.route('/')
def home():
    if 'historial' not in session:
        session['historial'] = [{"role":"assistant","content":"¡Hola! 👋 Bienvenido a LaOrtiga.cl 🌱. ¿En qué puedo ayudarte?"}]
    return render_template_string(TEMPLATE, historial=session['historial'])

@app.route('/chat', methods=['POST'])
def chat():
    pregunta = request.form['pregunta'].strip()
    if 'historial' not in session:
        session['historial'] = []

    session['historial'].append({"role": "user", "content": pregunta})
    respuesta = ""

    # Detectar intención de agendar
    if any(p in pregunta.lower() for p in ['agendar', 'reserva', 'cita', 'calendar']):
        respuesta = "Por favor indica la fecha y hora para agendar la cita (ej: 'el jueves a las 16')."
    elif dateparser.parse(pregunta):
        respuesta = crear_evento_google_calendar(pregunta)
    else:
        # Llamada a OpenAI
        mensajes = [{"role": "system", "content": "Eres un asistente conversacional de LaOrtiga.cl. Habla de forma amable, cercana y profesional. Solo responde sobre sostenibilidad, productos ecológicos o emprendimiento verde."}] + session['historial'][-10:]
        completion = openai.ChatCompletion.create(
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
<meta charset="UTF-8">
<title>Asistente La Ortiga</title>
<style>
body { font-family: sans-serif; background:#f4f9f4; margin:0; padding:0; }
#chat-container { max-width:400px; margin:20px auto; background:white; padding:10px; border-radius:10px; }
.msg { margin:5px 0; padding:8px; border-radius:10px; }
.bot { background:#2c7a2c; color:white; }
.user { background:#a3d1a3; color:black; text-align:right; }
form { display:flex; margin-top:10px; }
input { flex:1; padding:8px; border-radius:5px; border:1px solid #ccc; }
button { padding:8px 12px; background:#2c7a2c; color:white; border:none; border-radius:5px; margin-left:5px; cursor:pointer; }
</style>
</head>
<body>
<div id="chat-container">
    {% for m in historial %}
        <div class="msg {% if m.role=='user' %}user{% else %}bot{% endif %}">{{ m.content | safe }}</div>
    {% endfor %}
    <form method="POST" action="/chat">
        <input type="text" name="pregunta" placeholder="Escribe tu mensaje..." required>
        <button type="submit">Enviar</button>
    </form>
</div>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
