import os
from flask import Flask, session, request, render_template_string, redirect, url_for
import openai
from datetime import timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('app.secret_key')
if not app.secret_key:
    raise Exception("La variable de entorno SECRET_KEY no está configurada.")
app.permanent_session_lifetime = timedelta(days=30)

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise Exception("La variable de entorno OPENAI_API_KEY no está configurada.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Establecer un valor por defecto para los mensajes de bienvenida
def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8", errors="ignore") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

@app.route('/')
def home():
    # Usuario puede chatear sin loguearse
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]
    return redirect(url_for('chat'))

@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]

    respuesta = ""

    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()
        if pregunta:
            session['historial'].append({"role": "user", "content": pregunta})

            # Si el mensaje contiene palabras clave relacionadas con agendar
            if any(p in pregunta.lower() for p in ['agendar', 'reserva', 'cita', 'calendar']):
                respuesta = "¿Para qué día y hora quieres agendar? (por ejemplo: 'mañana a las 10')"

            # Si el usuario proporciona una fecha y hora válida
            elif dateparser.parse(pregunta):
                respuesta = "¡Evento agendado correctamente! 🗓️"

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
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet" />
    <style>
        /* tu CSS va aquí */
        body {
            font-family: 'Inter', sans-serif;
            background: #f4f9f4;
            margin: 0;
            padding: 0;
        }
        #chat-toggle-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            font-size: 2rem;
            background: #2c7a2c;
            color: white;
            border: none;
            border-radius: 50%;
            width: 60px;
            height: 60px;
            cursor: pointer;
        }
        #chat-container {
            position: fixed;
            bottom: 90px;
            right: 20px;
            width: 350px;
            max-height: 500px;
            background: white;
            border-radius: 10px;
            box-shadow: 0 0 12px rgba(0,0,0,0.1);
            display: flex;
            flex-direction: column;
        }
        #chat-header {
            display: flex;
            align-items: center;
            padding: 10px;
            background: #2c7a2c;
            color: white;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
        }
        #chat-header img {
            width: 40px;
            height: 40px;
            margin-right: 10px;
        }
        .name {
            font-weight: 600;
        }
        #chat-messages {
            flex-grow: 1;
            padding: 10px;
            overflow-y: auto;
            background: #eaf3ea;
        }
        .msg {
            margin-bottom: 10px;
            padding: 8px 12px;
            border-radius: 20px;
            max-width: 80%;
            word-wrap: break-word;
        }
        .bot {
            background: #2c7a2c;
            color: white;
            align-self: flex-start;
        }
        .user {
            background: #a3d1a3;
            color: #000;
            align-self: flex-end;
        }
        #chat-input-form {
            display: flex;
            border-top: 1px solid #ccc;
        }
        #chat-input {
            flex-grow: 1;
            border: none;
            padding: 10px;
            font-size: 1rem;
            border-bottom-left-radius: 10px;
        }
        #chat-send {
            border: none;
            background: #2c7a2c;
            color: white;
            padding: 0 20px;
            cursor: pointer;
            font-size: 1.2rem;
            border-bottom-right-radius: 10px;
        }
    </style>
</head>
<body>
    <button id="chat-toggle-btn">💬</button>
    <div id="chat-container" style="display:flex;">
        <div id="chat-header">
            <img src="https://cdn-icons-png.flaticon.com/512/194/194938.png" alt="Asistente" />
            <div>
                <div class="name">Capitán Planeta</div>
                <small style="font-size:12px;">Conectado como Invitado</small>
            </div>
        </div>
        <div id="chat-messages">
            {% for m in historial %}
                <div class="msg {% if m.role == 'user' %}user{% else %}bot{% endif %}">{{ m.content | safe }}</div>
            {% endfor %}
        </div>
        <form id="chat-input-form" method="POST">
            <input
                type="text"
                id="chat-input"
                name="pregunta"
                placeholder="Escribe tu mensaje..."
                autocomplete="off"
                required
            />
            <button id="chat-send">➤</button>
        </form>
    </div>
    <script>
        const toggleBtn = document.getElementById('chat-toggle-btn');
        const chatBox = document.getElementById('chat-container');
        const chatMessages = document.getElementById('chat-messages');
        const input = document.getElementById('chat-input');

        toggleBtn.onclick = () => {
            if (chatBox.style.display === 'none') {
                chatBox.style.display = 'flex';
                scrollToBottom();
                input.focus();
            } else {
                chatBox.style.display = 'none';
            }
        };

        function scrollToBottom() {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        window.onload = () => {
            chatBox.style.display = 'flex';
            scrollToBottom();
        };
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
