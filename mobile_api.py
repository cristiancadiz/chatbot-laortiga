"""API y archivos públicos de la PWA La Ortiga Recicla & Fletes.

El módulo no conoce credenciales ni importa el núcleo histórico. Recibe las
dependencias necesarias al registrarse desde app.py.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock

import requests
from flask import request, send_from_directory


_RATE_LOCK = Lock()
_RATE_EVENTS: dict[str, deque[float]] = defaultdict(deque)


def _json(app, payload, status=200):
    response = app.json.response(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _client_key(scope: str) -> str:
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
    remote = forwarded or request.remote_addr or "unknown"
    session = str(request.headers.get("X-App-Session") or "")[:80]
    raw = f"{scope}:{remote}:{session}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _allow(scope: str, limit: int, window_seconds: int = 3600) -> bool:
    now = time.time()
    key = _client_key(scope)
    with _RATE_LOCK:
        events = _RATE_EVENTS[key]
        while events and events[0] <= now - window_seconds:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True


def _clean(value, limit=500):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _valid_email(value):
    value = _clean(value, 180).lower()
    return not value or bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def _valid_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return 8 <= len(digits) <= 15


def register_mobile_app(app, settings, supabase_headers, ai_generate):
    app_dir = settings["app_dir"]
    api_base = "/app/api"

    @app.get("/app")
    @app.get("/app/")
    def mobile_index():
        response = send_from_directory(app_dir, "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/app/manifest.webmanifest")
    def mobile_manifest():
        return send_from_directory(app_dir, "manifest.webmanifest", mimetype="application/manifest+json")

    @app.get("/app/sw.js")
    def mobile_service_worker():
        response = send_from_directory(app_dir, "sw.js", mimetype="application/javascript")
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Service-Worker-Allowed"] = "/app/"
        return response

    @app.get("/app/assets/<path:filename>")
    def mobile_assets(filename):
        return send_from_directory(os.path.join(app_dir, "assets"), filename)

    @app.get(f"{api_base}/config")
    def mobile_config():
        return _json(app, {
            "ok": True,
            "name": "La Ortiga Recicla & Fletes",
            "version": settings["app_version"],
            "services": ["reciclaje", "flete"],
            "ai": bool(settings["ai_enabled"]),
        })

    @app.post(f"{api_base}/chat")
    def mobile_chat():
        if not _allow("chat", limit=30):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de mensajes. Intenta más tarde."}, 429)
        if not settings["ai_enabled"]:
            return _json(app, {"ok": False, "error": "La asistencia con IA no está disponible en este momento."}, 503)

        body = request.get_json(silent=True) or {}
        message = _clean(body.get("message"), 1500)
        service = _clean(body.get("service"), 30).lower()
        if not message:
            return _json(app, {"ok": False, "error": "Escribe una pregunta."}, 400)
        if service not in {"", "reciclaje", "flete"}:
            service = ""

        history_lines = []
        history = body.get("history") if isinstance(body.get("history"), list) else []
        for item in history[-8:]:
            if not isinstance(item, dict):
                continue
            role = "Usuario" if item.get("role") == "user" else "Asistente"
            content = _clean(item.get("content"), 800)
            if content:
                history_lines.append(f"{role}: {content}")

        instructions = (
            "Eres Ortiguín, asistente de La Ortiga Recicla & Fletes en Chile. "
            "Responde en español claro, cercano y breve. Puedes atender preguntas generales, "
            "pero cuando la consulta trate de reciclaje o fletes debes ayudar a convertirla en "
            "una solicitud concreta. No inventes precios, disponibilidad, certificaciones, "
            "destinos ni estados. Explica que el valor final lo propone y confirma un prestador. "
            "Nunca pidas claves, datos bancarios ni documentos sensibles. Si existe una urgencia "
            "o riesgo físico, recomienda contactar servicios de emergencia. Cuando ya estén los "
            "datos esenciales, invita a pulsar 'Crear solicitud'."
        )
        context = (
            f"Servicio seleccionado: {service or 'sin seleccionar'}\n"
            + ("Conversación reciente:\n" + "\n".join(history_lines) + "\n" if history_lines else "")
            + f"Mensaje actual: {message}"
        )
        try:
            reply, usage = ai_generate(settings["ai_model"], instructions, context)
            if not reply:
                raise RuntimeError("La IA respondió sin contenido")
            return _json(app, {"ok": True, "reply": reply, "usage": {"api": usage.get("api")}})
        except Exception as exc:
            app.logger.exception("MOBILE CHAT ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude responder ahora. Intenta nuevamente en unos segundos."}, 502)

    @app.post(f"{api_base}/solicitudes")
    def mobile_create_request():
        if not _allow("request", limit=8):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de solicitudes."}, 429)

        body = request.get_json(silent=True) or {}
        service_type = _clean(body.get("tipo"), 20).lower()
        name = _clean(body.get("nombre"), 120)
        phone = _clean(body.get("telefono"), 40)
        email = _clean(body.get("email"), 180).lower()
        commune = _clean(body.get("comuna"), 120)
        origin = _clean(body.get("direccion_origen"), 500)
        destination = _clean(body.get("direccion_destino"), 500)
        details = _clean(body.get("detalles"), 2000)
        schedule = _clean(body.get("fecha_preferida"), 120)

        if service_type not in {"reciclaje", "flete"}:
            return _json(app, {"ok": False, "error": "Selecciona reciclaje o flete."}, 400)
        if not name or not commune or not origin or not details:
            return _json(app, {"ok": False, "error": "Completa nombre, comuna, dirección y detalle."}, 400)
        if not _valid_phone(phone):
            return _json(app, {"ok": False, "error": "Ingresa un teléfono válido."}, 400)
        if not _valid_email(email):
            return _json(app, {"ok": False, "error": "Ingresa un correo válido."}, 400)
        if service_type == "flete" and not destination:
            return _json(app, {"ok": False, "error": "Para un flete debes indicar el destino."}, 400)

        headers = supabase_headers()
        if not headers:
            return _json(app, {"ok": False, "error": "La base de datos no está configurada."}, 503)

        public_id = f"NX-{datetime.now(timezone.utc):%y%m%d}-{uuid.uuid4().hex[:8].upper()}"
        access_token = uuid.uuid4().hex
        payload = {
            "public_id": public_id,
            "access_token_hash": hashlib.sha256(access_token.encode("utf-8")).hexdigest(),
            "empresa_id": settings["empresa_id"],
            "tipo": service_type,
            "nombre": name,
            "telefono": phone,
            "email": email or None,
            "comuna": commune,
            "direccion_origen": origin,
            "direccion_destino": destination or None,
            "detalles": details,
            "fecha_preferida": schedule or None,
            "estado": "publicada",
            "canal": "app",
        }
        try:
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_solicitudes",
                headers={**headers, "Prefer": "return=representation"},
                json=payload,
                timeout=settings["supabase_timeout"],
            )
            if not response.ok:
                app.logger.error("MOBILE REQUEST DB ERROR %s: %s", response.status_code, response.text[:800])
                if response.status_code == 404 or "nexi_app_solicitudes" in response.text:
                    return _json(app, {"ok": False, "error": "Falta aplicar la migración de la app en Supabase."}, 503)
                return _json(app, {"ok": False, "error": "No pude guardar la solicitud."}, 502)
            return _json(app, {
                "ok": True,
                "solicitud": {
                    "codigo": public_id,
                    "token": access_token,
                    "tipo": service_type,
                    "estado": "publicada",
                },
            }, 201)
        except requests.RequestException as exc:
            app.logger.exception("MOBILE REQUEST NETWORK ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude conectar con la base de datos."}, 502)

    @app.get(f"{api_base}/solicitudes/<public_id>")
    def mobile_request_status(public_id):
        public_id = _clean(public_id, 40).upper()
        access_token = _clean(request.args.get("token"), 80)
        if not re.fullmatch(r"NX-\d{6}-[A-F0-9]{8}", public_id) or len(access_token) < 20:
            return _json(app, {"ok": False, "error": "Código o acceso inválido."}, 400)
        headers = supabase_headers()
        if not headers:
            return _json(app, {"ok": False, "error": "La base de datos no está configurada."}, 503)
        try:
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_solicitudes",
                headers=headers,
                params={
                    "select": "public_id,tipo,comuna,estado,fecha_preferida,created_at,updated_at",
                    "public_id": f"eq.{public_id}",
                    "access_token_hash": f"eq.{hashlib.sha256(access_token.encode('utf-8')).hexdigest()}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            if not rows:
                return _json(app, {"ok": False, "error": "Solicitud no encontrada."}, 404)
            return _json(app, {"ok": True, "solicitud": rows[0]})
        except requests.RequestException as exc:
            app.logger.exception("MOBILE STATUS ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude consultar la solicitud."}, 502)

