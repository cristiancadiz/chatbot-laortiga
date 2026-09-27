"""API y archivos públicos de la PWA La Ortiga Recicla & Fletes.

El módulo no conoce credenciales ni importa el núcleo histórico. Recibe las
dependencias necesarias al registrarse desde app.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from threading import Lock, Thread

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


def _list_clean(value, item_limit=120, max_items=50):
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    output = []
    seen = set()
    for item in value[:max_items]:
        clean = _clean(item, item_limit)
        key = _norm(clean)
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _norm(value):
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in text if not unicodedata.combining(c))).strip()


def register_mobile_app(app, settings, supabase_headers, ai_generate, legacy_dispatch=None):
    app_dir = settings["app_dir"]
    api_base = "/app/api"

    def _db_headers(prefer=None):
        headers = supabase_headers()
        if not headers:
            raise RuntimeError("Supabase no está configurado")
        return {**headers, **({"Prefer": prefer} if prefer else {})}

    def _provider_auth(code, token):
        code = _clean(code, 40).upper()
        token = _clean(token, 100)
        if not re.fullmatch(r"PR-\d{6}-[A-F0-9]{8}", code) or len(token) < 20:
            return None
        response = requests.get(
            f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
            headers=_db_headers(),
            params={
                "select": "*",
                "public_id": f"eq.{code}",
                "access_token_hash": f"eq.{hashlib.sha256(token.encode('utf-8')).hexdigest()}",
                "activo": "eq.true",
                "limit": "1",
            },
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        rows = response.json() if response.content else []
        return rows[0] if rows else None

    def _send_push(provider_id, request_row):
        if not settings.get("vapid_public_key") or not settings.get("vapid_private_key"):
            return 0
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            app.logger.warning("PUSH DESACTIVADO: falta pywebpush")
            return 0

        response = requests.get(
            f"{settings['supabase_url']}/rest/v1/nexi_app_push_suscripciones",
            headers=_db_headers(),
            params={
                "select": "id,endpoint,p256dh,auth",
                "prestador_id": f"eq.{provider_id}",
                "activa": "eq.true",
                "limit": "10",
            },
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        subscriptions = response.json() if response.content else []
        sent = 0
        payload = json.dumps({
            "title": "Nueva oportunidad · La Ortiga",
            "body": f"{str(request_row.get('tipo') or '').title()} disponible en {request_row.get('comuna') or 'tu zona'}.",
            "url": "/app/?view=provider",
            "tag": f"solicitud-{request_row.get('id')}",
        }, ensure_ascii=False)

        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription["endpoint"],
                        "keys": {
                            "p256dh": subscription["p256dh"],
                            "auth": subscription["auth"],
                        },
                    },
                    data=payload,
                    vapid_private_key=settings["vapid_private_key"],
                    vapid_claims={"sub": settings["vapid_contact"]},
                    ttl=3600,
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                app.logger.warning("PUSH ERROR provider=%s status=%s", provider_id, status)
                if status in {404, 410}:
                    requests.patch(
                        f"{settings['supabase_url']}/rest/v1/nexi_app_push_suscripciones",
                        headers=_db_headers("return=minimal"),
                        params={"id": f"eq.{subscription['id']}"},
                        json={"activa": False},
                        timeout=settings["supabase_timeout"],
                    )
        return sent

    def _provider_matches_request(provider, request_row):
        roles = {_norm(x) for x in (provider.get("roles") or [])}
        communes = {_norm(x) for x in (provider.get("comunas") or [])}
        materials = {_norm(x) for x in (provider.get("materiales") or [])}
        request_commune = _norm(request_row.get("comuna"))
        request_materials = {_norm(x) for x in (request_row.get("materiales") or []) if _norm(x)}
        if _norm(request_row.get("tipo")) not in roles:
            return False
        if request_commune and request_commune not in communes:
            return False
        if request_row.get("tipo") == "reciclaje" and request_materials and materials and not (request_materials & materials):
            return False
        return True

    def _create_match(provider, request_row, notify=True):
        match_response = requests.post(
            f"{settings['supabase_url']}/rest/v1/nexi_app_matches",
            headers=_db_headers("return=representation,resolution=ignore-duplicates"),
            json={
                "empresa_id": settings["empresa_id"],
                "solicitud_id": request_row["id"],
                "prestador_id": provider["id"],
                "estado": "pendiente",
            },
            timeout=settings["supabase_timeout"],
        )
        if not match_response.ok:
            app.logger.warning("MATCH DB ERROR %s: %s", match_response.status_code, match_response.text[:500])
            return 0, 0
        return 1, _send_push(provider["id"], request_row) if notify else 0

    def _match_request(request_row):
        try:
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params={
                    "select": "id,roles,comunas,materiales,vehiculo",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "activo": "eq.true",
                    "disponible": "eq.true",
                    "limit": "500",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            providers = response.json() if response.content else []
            matched = 0
            notified = 0

            for provider in providers:
                if not _provider_matches_request(provider, request_row):
                    continue
                created, sent = _create_match(provider, request_row, notify=True)
                matched += created
                notified += sent

            app.logger.info(
                "MOBILE DISPATCH solicitud=%s matches=%s pushes=%s",
                request_row.get("public_id"), matched, notified,
            )
        except Exception as exc:
            app.logger.exception("MOBILE DISPATCH ERROR: %r", exc)

    def _match_provider(provider):
        """Entrega solicitudes abiertas a quien recién crea su perfil."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_solicitudes",
                headers=_db_headers(),
                params={
                    "select": "id,public_id,tipo,comuna,materiales,estado,created_at",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "estado": "in.(publicada,revisando)",
                    "created_at": f"gte.{cutoff}",
                    "order": "created_at.desc",
                    "limit": "100",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            for request_row in (response.json() if response.content else []):
                if _provider_matches_request(provider, request_row):
                    _create_match(provider, request_row, notify=False)
        except Exception as exc:
            app.logger.exception("PROVIDER BACKFILL ERROR: %r", exc)

    def _dispatch_request(request_row):
        _match_request(request_row)
        if request_row.get("tipo") == "reciclaje" and legacy_dispatch:
            try:
                legacy_dispatch(request_row)
            except Exception as exc:
                app.logger.exception("MOBILE LEGACY DISPATCH ERROR: %r", exc)

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
            "dispatch": True,
            "push": bool(settings.get("vapid_public_key") and settings.get("vapid_private_key")),
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
        materials = _list_clean(body.get("materiales"), item_limit=120, max_items=30)

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
        if service_type == "reciclaje" and not materials:
            return _json(app, {"ok": False, "error": "Selecciona al menos un material."}, 400)

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
            "materiales": materials,
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
            rows = response.json() if response.content else []
            created = rows[0] if rows else {**payload, "id": None}
            if created.get("id"):
                Thread(target=_dispatch_request, args=(created,), daemon=True).start()
            return _json(app, {
                "ok": True,
                "solicitud": {
                    "codigo": public_id,
                    "token": access_token,
                    "tipo": service_type,
                    "estado": "publicada",
                    "despacho": "buscando_prestadores",
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

    @app.post(f"{api_base}/prestadores")
    def mobile_create_provider():
        if not _allow("provider-register", limit=5):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de registros."}, 429)
        body = request.get_json(silent=True) or {}
        name = _clean(body.get("nombre"), 120)
        phone = _clean(body.get("telefono"), 40)
        email = _clean(body.get("email"), 180).lower()
        roles = [_norm(x) for x in _list_clean(body.get("roles"), 30, 2)]
        roles = list(dict.fromkeys(x for x in roles if x in {"reciclaje", "flete"}))
        communes = _list_clean(body.get("comunas"), 120, 80)
        materials = _list_clean(body.get("materiales"), 120, 50)
        vehicle = _clean(body.get("vehiculo"), 120)
        try:
            radius = float(body.get("radio_km")) if body.get("radio_km") not in (None, "") else None
            if radius is not None:
                radius = min(300, max(1, radius))
        except (TypeError, ValueError):
            return _json(app, {"ok": False, "error": "El radio de cobertura no es válido."}, 400)

        if not name or not _valid_phone(phone) or not roles or not communes:
            return _json(app, {"ok": False, "error": "Completa nombre, teléfono, tipo de servicio y comunas."}, 400)
        if not _valid_email(email):
            return _json(app, {"ok": False, "error": "Ingresa un correo válido."}, 400)
        if "reciclaje" in roles and not materials:
            return _json(app, {"ok": False, "error": "Selecciona los materiales que recibes."}, 400)
        if "flete" in roles and not vehicle:
            return _json(app, {"ok": False, "error": "Indica el vehículo que utilizas para fletes."}, 400)

        code = f"PR-{datetime.now(timezone.utc):%y%m%d}-{uuid.uuid4().hex[:8].upper()}"
        token = uuid.uuid4().hex
        payload = {
            "public_id": code,
            "access_token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "empresa_id": settings["empresa_id"],
            "nombre": name,
            "telefono": phone,
            "email": email or None,
            "roles": roles,
            "comunas": communes,
            "materiales": materials,
            "vehiculo": vehicle or None,
            "radio_km": radius,
            "disponible": True,
            "activo": True,
        }
        try:
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers("return=representation"),
                json=payload,
                timeout=settings["supabase_timeout"],
            )
            if not response.ok:
                app.logger.error("PROVIDER DB ERROR %s: %s", response.status_code, response.text[:800])
                return _json(app, {"ok": False, "error": "No pude completar el registro. Revisa la migración V4.2."}, 502)
            rows = response.json() if response.content else []
            created_provider = rows[0] if rows else None
            if created_provider:
                Thread(target=_match_provider, args=(created_provider,), daemon=True).start()
            return _json(app, {
                "ok": True,
                "prestador": {"codigo": code, "token": token, "nombre": name, "roles": roles, "disponible": True},
            }, 201)
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER NETWORK ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude conectar con la base de datos."}, 502)

    @app.route(f"{api_base}/prestadores/me", methods=["GET", "PATCH"])
    def mobile_provider_me():
        body = request.get_json(silent=True) or {}
        code = request.args.get("codigo") or body.get("codigo")
        token = request.args.get("token") or body.get("token")
        try:
            provider = _provider_auth(code, token)
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            if request.method == "GET":
                safe = {key: provider.get(key) for key in (
                    "public_id", "nombre", "email", "roles", "comunas", "materiales",
                    "vehiculo", "radio_km", "disponible", "created_at"
                )}
                return _json(app, {"ok": True, "prestador": safe})

            update = {}
            if isinstance(body.get("disponible"), bool):
                update["disponible"] = body["disponible"]
            if "comunas" in body:
                communes = _list_clean(body.get("comunas"), 120, 80)
                if communes:
                    update["comunas"] = communes
            if "materiales" in body:
                update["materiales"] = _list_clean(body.get("materiales"), 120, 50)
            if "vehiculo" in body:
                update["vehiculo"] = _clean(body.get("vehiculo"), 120) or None
            if not update:
                return _json(app, {"ok": False, "error": "No hay cambios válidos."}, 400)
            response = requests.patch(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers("return=representation"),
                params={"id": f"eq.{provider['id']}"},
                json=update,
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            return _json(app, {"ok": True, "prestador": rows[0] if rows else update})
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER ME ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude consultar el perfil."}, 502)

    @app.get(f"{api_base}/oportunidades")
    def mobile_opportunities():
        try:
            provider = _provider_auth(request.args.get("codigo"), request.args.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_matches",
                headers=_db_headers(),
                params={
                    "select": "id,solicitud_id,estado,created_at",
                    "prestador_id": f"eq.{provider['id']}",
                    "estado": "in.(pendiente,tomada)",
                    "order": "created_at.desc",
                    "limit": "50",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            matches = response.json() if response.content else []
            request_ids = [row["solicitud_id"] for row in matches]
            requests_by_id = {}
            if request_ids:
                request_response = requests.get(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_solicitudes",
                    headers=_db_headers(),
                    params={
                        "select": "id,public_id,tipo,comuna,direccion_origen,direccion_destino,detalles,materiales,fecha_preferida,estado,telefono,nombre,created_at",
                        "id": f"in.({','.join(request_ids)})",
                    },
                    timeout=settings["supabase_timeout"],
                )
                request_response.raise_for_status()
                requests_by_id = {row["id"]: row for row in (request_response.json() if request_response.content else [])}

            output = []
            for match in matches:
                item = dict(requests_by_id.get(match["solicitud_id"]) or {})
                if not item:
                    continue
                taken = match["estado"] == "tomada"
                if not taken:
                    item.pop("telefono", None)
                    item.pop("nombre", None)
                    item.pop("direccion_origen", None)
                    item.pop("direccion_destino", None)
                output.append({"match_id": match["id"], "match_estado": match["estado"], "solicitud": item})
            return _json(app, {"ok": True, "disponible": provider.get("disponible"), "oportunidades": output})
        except requests.RequestException as exc:
            app.logger.exception("OPPORTUNITIES ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude consultar las oportunidades."}, 502)

    @app.post(f"{api_base}/oportunidades/<match_id>/tomar")
    def mobile_take_opportunity(match_id):
        body = request.get_json(silent=True) or {}
        try:
            provider = _provider_auth(body.get("codigo"), body.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/rpc/nexi_app_tomar_match",
                headers=_db_headers(),
                json={"p_match_id": _clean(match_id, 80), "p_prestador_id": provider["id"]},
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            result = response.json() if response.content else {}
            if isinstance(result, list):
                result = result[0] if result else {}
            if not result.get("ok"):
                return _json(app, {"ok": False, "error": result.get("error") or "No se pudo tomar la solicitud."}, 409)
            return _json(app, {"ok": True, "resultado": result})
        except requests.RequestException as exc:
            app.logger.exception("TAKE OPPORTUNITY ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude tomar la solicitud."}, 502)

    @app.get(f"{api_base}/push/public-key")
    def mobile_push_public_key():
        key = settings.get("vapid_public_key") or ""
        return _json(app, {"ok": bool(key), "public_key": key}, 200 if key else 503)

    @app.post(f"{api_base}/push/subscribe")
    def mobile_push_subscribe():
        body = request.get_json(silent=True) or {}
        subscription = body.get("subscription") if isinstance(body.get("subscription"), dict) else {}
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        endpoint = _clean(subscription.get("endpoint"), 2000)
        p256dh = _clean(keys.get("p256dh"), 500)
        auth = _clean(keys.get("auth"), 500)
        try:
            provider = _provider_auth(body.get("codigo"), body.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            if not endpoint.startswith("https://") or not p256dh or not auth:
                return _json(app, {"ok": False, "error": "Suscripción push inválida."}, 400)
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_push_suscripciones",
                headers=_db_headers("return=representation,resolution=merge-duplicates"),
                params={"on_conflict": "endpoint"},
                json={
                    "empresa_id": settings["empresa_id"],
                    "prestador_id": provider["id"],
                    "endpoint": endpoint,
                    "p256dh": p256dh,
                    "auth": auth,
                    "user_agent": _clean(request.headers.get("User-Agent"), 500),
                    "activa": True,
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            return _json(app, {"ok": True})
        except requests.RequestException as exc:
            app.logger.exception("PUSH SUBSCRIBE ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude activar las notificaciones."}, 502)
