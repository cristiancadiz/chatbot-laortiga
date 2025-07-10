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
client = OpenAI()  # Cliente de la nueva API
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
<!-- HTML completo igual que antes -->
'''

if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
