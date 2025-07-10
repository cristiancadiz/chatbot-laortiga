from flask import Flask, render_template_string, request, session
import requests
import re
import numpy as np
import os
from datetime import datetime
from openai import OpenAI

app = Flask(__name__)
app.secret_key = "clave-super-secreta"

# --- CONFIGURACIÓN API ---
JUMPSELLER_LOGIN = "0f2a0a0976af739c8618cfb5e1680dda"
JUMPSELLER_AUTHTOKEN = "f837ba232aa21630109b290370c5ada7ca19025010331b2c59"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
TIENDA_URL = "https://laortiga.cl"

# --- FUNCIONES UTILES ---
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
        resp = requests.get(url)
        data = resp.json()
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

@app.route('/', methods=['GET', 'POST'])
def index():
    if 'historial' not in session:
        session['historial'] = [{
            "role": "assistant",
            "content": "¡Hola! 👋 Bienvenido a LaOrtiga.cl, la vitrina verde de Chile 🌱. ¿En qué puedo ayudarte hoy?"
        }]
        guardar_historial_en_archivo(session['historial'])

    productos_mostrar = []
    respuesta = ""

    if request.method == 'POST':
        pregunta = request.form['pregunta'].strip()
        if pregunta:
            session['historial'].append({"role": "user", "content": pregunta})

            palabras_clave_productos = [
                "producto", "sostenible", "comprar", "oferta", "precio", "tienen", "quiere", "mostrar",
                "muestreme", "alternativa", "necesito", "quiero", "recomiendame", "recomiendeme",
                "recomendar", "busco", "venden", "opciones"
            ]

            palabras_clave_ejecutivo = [
                "ejecutivo", "humano", "persona", "agente", "alguien", "representante",
                "necesito ayuda real", "quiero hablar con", "quiero contacto", "quiero atención",
                "quiero que me llamen", "me llame alguien", "contacto humano", "hablar con alguien"
            ]

            if any(palabra in pregunta.lower() for palabra in palabras_clave_productos):
                productos_mostrar = buscar_productos_por_embedding(pregunta)
                respuesta = "Aquí tienes algunas alternativas que podrían interesarte:"

            elif any(palabra in pregunta.lower() for palabra in palabras_clave_ejecutivo):
                respuesta = (
                    "¡Por supuesto! Un ejecutivo humano de nuestro equipo puede ayudarte. "
                    "Por favor, déjanos tu número de teléfono y tu consulta, y te contactaremos pronto."
                )

            else:
                mensajes = [
                    {"role": "system", "content": (
                        "Eres un asistente para una tienda ecológica online chilena llamada La Ortiga. "
                        "Tu único objetivo es ayudar a los usuarios con información sobre productos sostenibles, ecológicos, orgánicos y naturales. "
                        "No respondas preguntas que no estén relacionadas con compras, productos, precios o temas de la tienda. "
                        "Si el usuario pregunta algo fuera de ese contexto, responde amablemente que solo puedes ayudar con temas de productos ecológicos y sostenibles."
                    )}
                ] + [{"role": m["role"], "content": m["content"]} for m in session['historial'][-10:]]

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

# --- HTML embebido ---
TEMPLATE = '''
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Chatbot La Ortiga</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { margin: 0; font-family: 'Segoe UI', sans-serif; background: #f0f0f0; }
        #chat-container {
            position: fixed;
            bottom: 80px;
            right: 20px;
            width: 360px;
            height: 520px;
            background: white;
            border-radius: 16px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.2);
            display: flex;
            flex-direction: column;
            overflow: hidden;
            z-index: 9999;
        }
        #chat-header {
            background: #4CAF50;
            color: white;
            padding: 14px;
            font-size: 18px;
            font-weight: bold;
            text-align: center;
        }
        #chat-messages {
            flex: 1;
            overflow-y: auto;
            padding: 10px;
            background: #f9f9f9;
        }
        .msg {
            margin: 10px 0;
            padding: 10px 14px;
            border-radius: 18px;
            max-width: 80%;
            line-height: 1.4;
            word-wrap: break-word;
        }
        .msg.user { background: #dcf8c6; align-self: flex-end; }
        .msg.bot { background: #eee; align-self: flex-start; }
        #chat-input-form {
            display: flex;
            border-top: 1px solid #ddd;
            background: #fff;
        }
        #chat-input {
            flex: 1;
            padding: 12px;
            border: none;
            outline: none;
            font-size: 14px;
        }
        #chat-send {
            background: #4CAF50;
            color: white;
            border: none;
            padding: 0 16px;
            font-weight: bold;
            font-size: 16px;
            cursor: pointer;
        }
        .producto {
            display: flex;
            margin: 8px 0;
            background: #fff;
            border-radius: 10px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.1);
            overflow: hidden;
        }
        .producto img {
            width: 80px;
            height: 80px;
            object-fit: cover;
        }
        .producto-info {
            padding: 8px;
            font-size: 13px;
        }
        .producto-info h4 {
            margin: 0 0 5px;
            font-size: 14px;
            font-weight: bold;
        }
        .producto-info a {
            color: #4CAF50;
            text-decoration: none;
            font-size: 12px;
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
        <div id="chat-header">Asistente La Ortiga</div>
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
            <input type="text" id="chat-input" name="pregunta" placeholder="Escribe tu pregunta..." autocomplete="off" required />
            <button id="chat-send">Enviar</button>
        </form>
    </div>

    <script>
        const toggleBtn = document.getElementById('chat-toggle-btn');
        const chatBox = document.getElementById('chat-container');
        const chatMessages = document.getElementById('chat-messages');
        const input = document.getElementById('chat-input');

        toggleBtn.onclick = () => {
            chatBox.style.display = chatBox.style.display === 'none' ? 'flex' : 'none';
            scrollToBottom();
            input.focus();
        };

        function scrollToBottom() {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        window.onload = () => {
            chatBox.style.display = 'flex';
            scrollToBottom();
            input.focus();
        };
    </script>
</body>
</html>
'''
if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
