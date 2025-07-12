from flask import Flask, render_template_string, request, session, send_from_directory
import requests
import re
import numpy as np
import os
from datetime import datetime, timedelta
import openai

app = Flask(__name__)
app.secret_key = "clave-super-secreta"
app.permanent_session_lifetime = timedelta(days=30)

# --- CONFIGURACIÓN API ---
JUMPSELLER_LOGIN = "0f2a0a0976af739c8618cfb5e1680dda"
JUMPSELLER_AUTHTOKEN = "f837ba232aa21630109b290370c5ada7ca19025010331b2c59"
TIENDA_URL = "https://laortiga.cl"

# --- CLIENTE OPENAI ---
client = openai.OpenAI()

# --- TEXTO DE EMPRENDE ---
EMPRENDE_INFO = """
🌱 ¡Súmate al Buscador Verde de Chile!
¿Tienes un emprendimiento con impacto ambiental? En LaOrtiga.cl conectamos a emprendedores como tú con personas que buscan alternativas conscientes y sostenibles en todo Chile.
Puedes mostrar lo que haces, vender sin intermediarios o distribuir tus productos a nivel nacional:
🪴 Plan Promociona — $4.990 mensual
🛍️ Plan Comercializa — $14.990 mensual
🚚 Plan Distribuye — $39.990 mensual
📬 Postula en: https://laortiga.cl/emprende
"""

# --- FUNCIONES ---
def limpiar_html(texto):
    return re.sub('<.*?>', '', texto or "").replace("&nbsp;", " ").strip()

def cosine_similarity(vec1, vec2):
    norm1, norm2 = np.linalg.norm(vec1), np.linalg.norm(vec2)
    return np.dot(vec1, vec2) / (norm1 * norm2) if norm1 and norm2 else 0

def cargar_productos_con_embeddings():
    productos = []
    page = 1
    while True:
        url = f"https://api.jumpseller.com/v1/products.json?login={JUMPSELLER_LOGIN}&authtoken={JUMPSELLER_AUTHTOKEN}&page={page}&per_page=50"
        try:
            resp = requests.get(url)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"❌ Error al cargar productos: {e}")
            break
        if not data:
            break
        for item in data:
            prod = item.get("product", {})
            variants = prod.get("variants", [])
            if not variants:
                continue
            variant_id = variants[0].get("id")
            permalink = prod.get("permalink")
            descripcion = limpiar_html(prod.get("description"))
            imagen = prod.get("main_image", {}).get("url", "") or (prod.get("images", [{}])[0].get("url", "") if prod.get("images") else "")
            embedding = client.embeddings.create(
                model="text-embedding-ada-002",
                input=descripcion
            ).data[0].embedding

            productos.append({
                "nombre": prod.get("name", ""),
                "marca": prod.get("brand", ""),
                "precio": prod.get("price", 0),
                "descripcion": descripcion,
                "imagen": imagen,
                "categorias": ", ".join([c["name"] for c in prod.get("categories", [])]),
                "url": f"{TIENDA_URL}/{permalink}?variant_id={variant_id}",
                "embedding": embedding
            })
        page += 1
    return productos

print("🔁 Cargando productos y calculando embeddings...")
TODOS_LOS_PRODUCTOS = cargar_productos_con_embeddings()
print("✅ Productos cargados:", len(TODOS_LOS_PRODUCTOS))

def buscar_productos_por_embedding(pregunta, top_n=3):
    embedding_pregunta = client.embeddings.create(
        model="text-embedding-ada-002",
        input=pregunta
    ).data[0].embedding
    return sorted(TODOS_LOS_PRODUCTOS, key=lambda p: cosine_similarity(p["embedding"], embedding_pregunta), reverse=True)[:top_n]

def guardar_historial_en_archivo(historial):
    carpeta = "conversaciones_guardadas"
    os.makedirs(carpeta, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    ruta = f"{carpeta}/chat_{timestamp}.txt"
    with open(ruta, "w", encoding="utf-8") as f:
        for m in historial:
            rol = "Tú" if m['role'] == 'user' else "Bot"
            f.write(f"{rol}: {m['content']}\n\n")

# --- RUTAS FLASK ---
@app.route('/', methods=['GET', 'POST'])
def index():
    session.permanent = True
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]

    productos_mostrar = []
    respuesta = ""

    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()
        if pregunta:
            session['historial'].append({"role": "user", "content": pregunta})

            palabras_clave_productos = ["producto", "sostenible", "comprar", "oferta", "precio", "tienen", "quiero", "mostrar", "muestreme", "alternativa", "necesito", "recomiendame", "recomiendeme", "recomendar", "busco", "venden", "opciones"]
            palabras_clave_ejecutivo = ["ejecutivo", "humano", "persona", "agente", "alguien", "representante", "necesito ayuda real", "quiero hablar con", "quiero contacto", "quiero atención", "quiero que me llamen", "me llame alguien", "contacto humano", "hablar con alguien"]
            palabras_clave_emprende = ["emprender", "vender", "vender con ustedes", "colaborar", "vender productos", "sumarse", "postular", "emprendimiento", "vendo", "ofrecer productos", "emprendedores", "quiero sumarme", "trabajar", "trabajar con ustedes"]

            if any(p in pregunta.lower() for p in palabras_clave_productos):
                productos_mostrar = buscar_productos_por_embedding(pregunta)
                respuesta = "Aquí tienes algunas alternativas que podrían interesarte:"

            elif any(p in pregunta.lower() for p in palabras_clave_ejecutivo):
                respuesta = "¡Por supuesto! Un ejecutivo humano puede ayudarte. Por favor, déjanos tu número de teléfono y tu consulta."

            elif any(p in pregunta.lower() for p in palabras_clave_emprende):
                respuesta = f"¡Qué buena noticia que quieras emprender con nosotros! 🙌<br><br>{EMPRENDE_INFO.strip().replace('\n', '<br>')}<br><br><a href='https://laortiga.cl/emprende' target='_blank' style='display:inline-block;margin-top:10px;padding:8px 12px;background:#4CAF50;color:white;text-decoration:none;border-radius:8px;'>👉 Ir a la página para emprender</a>"

            else:
                mensajes = [
                    {"role": "system", "content": "Eres un asistente de LaOrtiga.cl. Solo responde preguntas sobre productos ecológicos, sostenibles o servicios de la tienda."}
                ] + session['historial'][-10:]

                completion = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=mensajes,
                    max_tokens=150,
                    temperature=0.7
                )
                respuesta = completion.choices[0].message.content.strip()

            session['historial'].append({"role": "assistant", "content": respuesta})
            guardar_historial_en_archivo(session['historial'])

    return render_template_string(TEMPLATE, respuesta=respuesta, productos=productos_mostrar, historial=session.get('historial', []))

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

# --- HTML TEMPLATE EMBEBIDO ---
TEMPLATE = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Asistente La Ortiga</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body {
            margin: 0;
            font-family: 'Inter', sans-serif;
            background: #f3f4f6;
        }

        #chat-container {
            position: fixed;
            bottom: 90px;
            right: 20px;
            width: 370px;
            height: 540px;
            background: #ffffff;
            border-radius: 20px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.15);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            z-index: 9999;
        }

        #chat-header {
            display: flex;
            align-items: center;
            background: #4CAF50;
            padding: 16px;
            color: white;
        }

        #chat-header img {
            border-radius: 50%;
            width: 40px;
            height: 40px;
            margin-right: 12px;
            border: 2px solid white;
        }

        #chat-header .name {
            font-weight: bold;
            font-size: 16px;
        }

        #chat-messages {
            flex: 1;
            padding: 14px;
            overflow-y: auto;
            background: #f9fafb;
        }

        .msg {
            padding: 12px 16px;
            margin: 8px 0;
            max-width: 80%;
            border-radius: 20px;
            font-size: 14px;
            line-height: 1.4;
            word-break: break-word;
        }

        .msg.user {
            align-self: flex-end;
            background-color: #DCF8C6;
            border-bottom-right-radius: 4px;
        }

        .msg.bot {
            align-self: flex-start;
            background-color: #ffffff;
            border-bottom-left-radius: 4px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        }

        .producto {
            display: flex;
            margin: 12px 0;
            background: #fff;
            border-radius: 14px;
            overflow: hidden;
            box-shadow: 0 1px 5px rgba(0,0,0,0.08);
        }

        .producto img {
            width: 80px;
            height: 80px;
            object-fit: cover;
        }

        .producto-info {
            padding: 10px;
            font-size: 13px;
        }

        .producto-info h4 {
            margin: 0 0 4px;
            font-size: 14px;
            font-weight: bold;
        }

        .producto-info a {
            color: #4CAF50;
            font-size: 12px;
            text-decoration: none;
        }

        #chat-input-form {
            display: flex;
            padding: 12px;
            border-top: 1px solid #e0e0e0;
            background: #fff;
        }

        #chat-input {
            flex: 1;
            padding: 10px 14px;
            border-radius: 12px;
            border: 1px solid #ccc;
            font-size: 14px;
        }

        #chat-send {
            background: #4CAF50;
            color: white;
            border: none;
            padding: 0 16px;
            margin-left: 10px;
            font-size: 18px;
            border-radius: 12px;
            cursor: pointer;
        }

        #chat-toggle-btn {
            position: fixed;
            bottom: 20px;
            right: 20px;
            width: 60px;
            height: 60px;
            border-radius: 50%;
            background: #4CAF50;
            color: white;
            font-size: 28px;
            border: none;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            cursor: pointer;
            z-index: 10000;
        }
    </style>
</head>
<body>
    <button id="chat-toggle-btn">💬</button>
    <div id="chat-container" style="display:none;">
        <div id="chat-header">
            <img src="https://cdn-icons-png.flaticon.com/512/194/194938.png" alt="Asistente">
            <div>
                <div class="name">Capitán Planeta</div>
                <small style="font-size:12px;">Disponible ahora</small>
            </div>
        </div>
        <div id="chat-messages">
            {% for m in historial %}
                <div class="msg {% if m.role == 'user' %}user{% else %}bot{% endif %}">{{ m.content | safe }}</div>
            {% endfor %}
            {% for p in productos %}
                <div class="producto">
                    <img src="{{ p.imagen }}" alt="{{ p.nombre }}">
                    <div class="producto-info">
                        <h4>{{ p.nombre }}</h4>
                        <p>${{ '{:,.0f}'.format(p.precio|float).replace(',', '.') }}</p>
                        <a href="{{ p.url }}" target="_blank">Ver producto 🔗</a>
                    </div>
                </div>
            {% endfor %}
        </div>
        <form id="chat-input-form" method="POST">
            <input type="text" id="chat-input" name="pregunta" placeholder="Escribe tu mensaje..." autocomplete="off" required />
            <button id="chat-send">➤</button>
        </form>
    </div>
    <script>
        const toggleBtn = document.getElementById('chat-toggle-btn');
        const chatBox = document.getElementById('chat-container');
        const chatMessages = document.getElementById('chat-messages');
        const input = document.getElementById('chat-input');

        toggleBtn.onclick = () => {
            chatBox.style.display = chatBox.style.display === 'none' ? 'flex' : 'none';
            scrollToBottom(); input.focus();
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
'''

# --- EJECUCIÓN LOCAL ---
if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
