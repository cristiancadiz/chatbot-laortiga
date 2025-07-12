from flask import Flask, render_template_string, request, session, send_from_directory
import requests
import re
import numpy as np
import os
from datetime import datetime, timedelta
import openai

app = Flask(__name__)
app.secret_key = "clave-super-secreta"
app.permanent_session_lifetime = timedelta(days=30)  # Sesiones persistentes por 30 días

# --- CONFIGURACIÓN API ----
JUMPSELLER_LOGIN = "0f2a0a0976af739c8618cfb5e1680dda"
JUMPSELLER_AUTHTOKEN = "f837ba232aa21630109b290370c5ada7ca19025010331b2c59"
TIENDA_URL = "https://laortiga.cl"

# --- CLIENTE OPENAI NUEVO ---
client = openai.OpenAI()

# --- TEXTO DE EMPRENDE ---
EMPRENDE_INFO = """... (sin cambios) ..."""

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

# --- CHAT PRINCIPAL ---
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

            palabras_clave_productos = [ ... ]
            palabras_clave_ejecutivo = [ ... ]
            palabras_clave_emprende = [ ... ]

            if any(palabra in pregunta.lower() for palabra in palabras_clave_productos):
                productos_mostrar = buscar_productos_por_embedding(pregunta)
                respuesta = "Aquí tienes algunas alternativas que podrían interesarte:"

            elif any(palabra in pregunta.lower() for palabra in palabras_clave_ejecutivo):
                respuesta = "¡Por supuesto!... (respuesta ejecutivo)..."

            elif any(palabra in pregunta.lower() for palabra in palabras_clave_emprende):
                respuesta = f"...(respuesta emprende con HTML)..."

            else:
                mensajes = [
                    {"role": "system", "content": "...(instrucciones del sistema)..."}
                ] + session['historial'][-10:]

                completion = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=mensajes,
                    max_tokens=150,
                    temperature=0.7
                )
                respuesta = completion.choices[0].message.content.strip()

            if respuesta:
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

# --- TEMPLATE HTML embebido ---
TEMPLATE = '''...(igual al tuyo)...'''

if __name__ == '__main__':
    from os import environ
    app.run(host='0.0.0.0', port=int(environ.get('PORT', 5000)))
