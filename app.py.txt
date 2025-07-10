from flask import Flask, render_template_string, request, session
import requests
import openai
import re
import numpy as np
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "clave-super-secreta"

# --- CONFIGURACIÓN API ---
JUMPSELLER_LOGIN = "0f2a0a0976af739c8618cfb5e1680dda"
JUMPSELLER_AUTHTOKEN = "f837ba232aa21630109b290370c5ada7ca19025010331b2c59"
openai.api_key = "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"
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
            embedding = openai.Embedding.create(input=descripcion, model="text-embedding-ada-002")["data"][0]["embedding"]

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
    embedding_pregunta = openai.Embedding.create(input=pregunta, model="text-embedding-ada-002")["data"][0]["embedding"]
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
            palabras_clave = ["producto", "sostenible", "comprar", "oferta", "precio", "tienen", "quiere", "mostrar","muestreme", "alternativa","necesito", "quiero", "recomiendame","recomiendeme", "recomendar", "busco", "venden","quiero", "opciones"]

            if any(palabra in pregunta.lower() for palabra in palabras_clave):
                productos_mostrar = buscar_productos_por_embedding(pregunta)
                respuesta = "Aquí tienes algunas alternativas que podrían interesarte:"
            elif "ejecutivo" in pregunta.lower() or "contacto" in pregunta.lower():
                respuesta = "Para poder ayudarte mejor, por favor déjanos tu número de teléfono y tu consulta."
            else:
                mensajes = [
                    {"role": "system", "content": (
                        "Eres un asistente para una tienda ecológica online chilena llamada La Ortiga. "
                        "Tu único objetivo es ayudar a los usuarios con información sobre productos sostenibles, ecológicos, orgánicos y naturales. "
                        "No respondas preguntas que no estén relacionadas con compras, productos, precios o temas de la tienda. "
                        "Si el usuario pregunta algo fuera de ese contexto, responde amablemente que solo puedes ayudar con temas de productos ecológicos y sostenibles."
                    )}
                ] + [{"role": m["role"], "content": m["content"]} for m in session['historial'][-10:]]

                completion = openai.ChatCompletion.create(
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
<meta charset="UTF-8" />
<title>Chatbot Tienda La Ortiga</title>
<style>
    body { font-family: Arial, sans-serif; background: #f4f4f4; margin: 0; height: 100vh; overflow: hidden; }
    #chat-popup { position: fixed; bottom: 80px; right: 20px; width: 350px; height: 500px; background: white;
        border-radius: 12px; box-shadow: 0 0 15px rgba(0,0,0,0.3); display: flex; flex-direction: column;
        overflow: hidden; z-index: 1000; }
    #chat-header { background: #4CAF50; color: white; padding: 10px; font-weight: bold; font-size: 18px; text-align: center; }
    #chat-messages { flex: 1; padding: 10px; overflow-y: auto; font-size: 14px; max-height: 370px; clear: both; }
    .msg { margin-bottom: 12px; line-height: 1.3; max-width: 80%; padding: 8px 12px; border-radius: 18px; clear: both;
        word-wrap: break-word; white-space: pre-wrap; }
    .msg.user { background: #dcf8c6; float: right; text-align: right; }
    .msg.bot { background: #eee; float: left; text-align: left; }
    #chat-input-form { display: flex; border-top: 1px solid #ddd; background: #fafafa; }
    #chat-input { flex: 1; padding: 10px; border: none; font-size: 14px; outline: none; }
    #chat-send { background: #4CAF50; border: none; color: white; padding: 0 15px; cursor: pointer; font-weight: bold; font-size: 16px; }
    #chat-toggle-btn { position: fixed; bottom: 20px; right: 20px; background: #4CAF50; border-radius: 50%;
        width: 60px; height: 60px; border: none; color: white; font-size: 30px; cursor: pointer; z-index: 1001;
        box-shadow: 0 0 15px rgba(0,0,0,0.3); }
    .producto { display: flex; margin-top: 10px; background: #f9f9f9; border-radius: 10px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.1); padding: 8px; gap: 10px; clear: both; }
    .producto img { width: 80px; height: 80px; object-fit: cover; border-radius: 8px; }
    .producto-info h3 { margin: 0; font-size: 16px; }
    .producto-info p { margin: 2px 0; font-size: 13px; }
    .producto-info a { color: #4CAF50; font-size: 13px; text-decoration: none; }
</style>
</head>
<body>
<button id="chat-toggle-btn" title="Abrir chat">💬</button>
<div id="chat-popup">
    <div id="chat-header">Chat Tienda La Ortiga</div>
    <div id="chat-messages">
        {% for m in historial %}
            <div class="msg {% if m.role == 'user' %}user{% else %}bot{% endif %}">{{ m.content | safe }}</div>
        {% endfor %}
        {% for p in productos %}
        <div class="producto">
            <img src="{{ p.imagen }}" alt="{{ p.nombre }}">
            <div class="producto-info">
                <h3>{{ p.nombre }}</h3>
                <p><strong>Marca:</strong> {{ p.marca }}</p>
                <p><strong>Precio:</strong> ${{ '{:,.0f}'.format(p.precio|float).replace(',', '.') }}</p>
                <p>{{ p.descripcion }}</p>
                <a href="{{ p.url }}" target="_blank">Ver producto 🔗</a>
            </div>
        </div>
        {% endfor %}
    </div>
    <form id="chat-input-form" method="POST">
        <input type="text" id="chat-input" name="pregunta" autocomplete="off" placeholder="Escribe tu mensaje...">
        <button type="submit" id="chat-send">Enviar</button>
    </form>
</div>
<script>
    const toggleBtn = document.getElementById('chat-toggle-btn');
    const popup = document.getElementById('chat-popup');
    const messages = document.getElementById('chat-messages');
    const input = document.getElementById('chat-input');
    window.onload = () => {
        popup.style.display = 'flex';
        scrollToBottom();
        input.focus();
    };
    toggleBtn.onclick = () => {
        if (popup.style.display === 'flex') {
            popup.style.display = 'none';
        } else {
            popup.style.display = 'flex';
            scrollToBottom();
            input.focus();
        }
    };
    function scrollToBottom() {
        messages.scrollTop = messages.scrollHeight;
    }
</script>
</body>
</html>
'''

if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))

