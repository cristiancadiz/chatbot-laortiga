from flask import Flask, render_template_string, request, session, send_from_directory
from datetime import datetime, timedelta
import os
import openai

app = Flask(__name__)
app.secret_key = "clave-super-secreta"
app.permanent_session_lifetime = timedelta(days=30)

# --- CLIENTE OPENAI ---
client = openai.OpenAI()

# --- FUNCIONES ---
def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

# --- RUTA PRINCIPAL ---
@app.route('/', methods=['GET', 'POST'])
def index():
    session.permanent = True
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

    return render_template_string(TEMPLATE, respuesta=respuesta, historial=session.get('historial', []))

# --- VER CONVERSACIONES GUARDADAS ---
@app.route('/conversaciones')
def listar_conversaciones():
    carpeta = "conversaciones_guardadas"
    if not os.path.exists(carpeta):
        return "No hay conversaciones guardadas aún."
    archivos = sorted(os.listdir(carpeta))
    enlaces = [f'<li><a href="/conversaciones/{nombre}">{nombre}</a></li>' for nombre in archivos]
    return f"<h2>Conversaciones guardadas</h2><ul>{''.join(enlaces)}</ul>"

@app.route('/conversaciones/<nombre>')
def ver_conversacion(nombre):
    return send_from_directory("conversaciones_guardadas", nombre)

# --- HTML TEMPLATE (IGUAL) ---
TEMPLATE = ''' ... '''  # Usa el mismo TEMPLATE que ya tienes, sin la sección de productos.

# --- EJECUCIÓN LOCAL ---
if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
