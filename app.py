import os
import requests
from flask import Flask, request, jsonify
import openai
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

# === CONFIGURACIÓN ===
ACCESS_TOKEN = os.getenv("MELI_TOKEN", "APP_USR-4665211924681074-072321")
openai.api_key = os.getenv("OPENAI_API_KEY")

app = Flask(__name__)

# === 1. Obtener publicaciones activas desde Mercado Libre ===
def obtener_publicaciones():
    url_user = "https://api.mercadolibre.com/users/me"
    headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
    res_user = requests.get(url_user, headers=headers)
    user_id = res_user.json()["id"]

    url_items = f"https://api.mercadolibre.com/users/{user_id}/items/search"
    res_items = requests.get(url_items, headers=headers)
    ids = res_items.json().get("results", [])

    publicaciones = []
    for item_id in ids:
        item_data = requests.get(f"https://api.mercadolibre.com/items/{item_id}").json()
        publicaciones.append({
            "id": item_id,
            "title": item_data.get("title", ""),
            "price": item_data.get("price", 0),
            "permalink": item_data.get("permalink", ""),
            "description": item_data.get("description", {}).get("plain_text", "")
        })
    return publicaciones

# === 2. Crear embeddings con OpenAI ===
def crear_embeddings(textos):
    embeddings = []
    for texto in textos:
        response = openai.Embedding.create(
            model="text-embedding-3-small",
            input=texto
        )
        embeddings.append(response["data"][0]["embedding"])
    return np.array(embeddings)

# === 3. Buscar publicación más parecida ===
def buscar_publicacion(user_question, publicaciones, embeddings):
    pregunta_embed = openai.Embedding.create(
        model="text-embedding-3-small",
        input=user_question
    )["data"][0]["embedding"]

    similitudes = cosine_similarity([pregunta_embed], embeddings)[0]
    idx = int(np.argmax(similitudes))
    return publicaciones[idx]

# === 4. Endpoint para preguntas ===
@app.route("/chat", methods=["POST"])
def chat():
    user_input = request.json.get("mensaje", "")
    publicaciones = obtener_publicaciones()
    textos = [f"{p['title']} {p['description']}" for p in publicaciones]
    embeddings = crear_embeddings(textos)

    pub_relacionada = buscar_publicacion(user_input, publicaciones, embeddings)

    prompt = f"""El usuario preguntó: {user_input}
Esta es la publicación más relevante:
Título: {pub_relacionada['title']}
Precio: {pub_relacionada['price']}
Descripción: {pub_relacionada['description']}

Responde de forma natural con un lenguaje amigable y profesional.
"""
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )

    respuesta = response.choices[0].message.content
    return jsonify({"respuesta": respuesta})

# === 5. Home simple ===
@app.route("/")
def home():
    return "ChatBot Mercado Libre OK"

# === Ejecutar localmente ===
if __name__ == "__main__":
    app.run(debug=True)
