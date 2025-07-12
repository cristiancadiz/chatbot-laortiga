@app.route('/', methods=['GET', 'POST'])
def index():
    session.permanent = True  # Mantener historial por más tiempo

    # Crear historial solo si no existe
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

            palabras_clave_productos = [
                "producto", "sostenible", "comprar", "oferta", "precio", "tienen", "quiero", "mostrar",
                "muestreme", "alternativa", "necesito", "recomiendame", "recomiendeme",
                "recomendar", "busco", "venden", "opciones"
            ]

            palabras_clave_ejecutivo = [
                "ejecutivo", "humano", "persona", "agente", "alguien", "representante",
                "necesito ayuda real", "quiero hablar con", "quiero contacto", "quiero atención",
                "quiero que me llamen", "me llame alguien", "contacto humano", "hablar con alguien"
            ]

            palabras_clave_emprende = [
                "emprender", "vender", "vender con ustedes", "colaborar", 
                "vender productos", "sumarse", "postular", "emprendimiento", "vendo",
                "ofrecer productos", "emprendedores", "quiero sumarme", "trabajar", "trabajar con ustedes"
            ]

            if any(palabra in pregunta.lower() for palabra in palabras_clave_productos):
                productos_mostrar = buscar_productos_por_embedding(pregunta)
                respuesta = "Aquí tienes algunas alternativas que podrían interesarte:"

            elif any(palabra in pregunta.lower() for palabra in palabras_clave_ejecutivo):
                respuesta = (
                    "¡Por supuesto! Un ejecutivo humano de nuestro equipo puede ayudarte. "
                    "Por favor, déjanos tu número de teléfono y tu consulta, y te contactaremos pronto."
                )

            elif any(palabra in pregunta.lower() for palabra in palabras_clave_emprende):
                respuesta = (
                    f"¡Qué buena noticia que quieras emprender con nosotros! 🙌<br><br>"
                    f"{EMPRENDE_INFO.strip().replace('\n', '<br>')}<br><br>"
                    f'<a href="https://laortiga.cl/emprende" target="_blank" '
                    f'style="display:inline-block;margin-top:10px;padding:8px 12px;'
                    f'background:#4CAF50;color:white;text-decoration:none;border-radius:8px;">'
                    f'👉 Ir a la página para emprender</a>'
                )

            else:
                mensajes = [
                    {"role": "system", "content": (
                        "Eres un asistente para una tienda ecológica online chilena llamada La Ortiga. "
                        "Tu único objetivo es ayudar a los usuarios con información sobre productos sostenibles, ecológicos, orgánicos y naturales. "
                        "No respondas preguntas que no estén relacionadas con compras, productos, precios o temas de la tienda."
                    )}
                ] + session['historial'][-10:]  # ya no pasamos de nuevo assistant innecesariamente

                completion = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=mensajes,
                    max_tokens=150,
                    temperature=0.7
                )
                respuesta = completion.choices[0].message.content.strip()

            # Solo se agrega si hay una respuesta generada
            if respuesta:
                session['historial'].append({"role": "assistant", "content": respuesta})

            guardar_historial_en_archivo(session['historial'])

    return render_template_string(TEMPLATE, respuesta=respuesta, productos=productos_mostrar, historial=session.get('historial', []))
