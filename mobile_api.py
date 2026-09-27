"""API y archivos públicos de la PWA Llama a Jaime Servicios.

El módulo no conoce credenciales ni importa el núcleo histórico. Recibe las
dependencias necesarias al registrarse desde app.py.
"""

from __future__ import annotations

import hashlib
import hmac
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

SERVICE_TYPES = {
    "hogar",
    "limpieza",
    "flete",
    "jardineria",
    "belleza",
    "reciclaje",
    "otro",
}

SERVICE_LABELS = {
    "hogar": "Servicios para el hogar",
    "limpieza": "Limpieza",
    "flete": "Fletes y traslados",
    "jardineria": "Jardinería",
    "belleza": "Belleza y bienestar",
    "reciclaje": "Reciclaje",
    "otro": "Otros servicios",
}

BEAUTY_TYPES = {
    "barberia",
    "peluqueria",
    "manicure_pedicure",
    "maquillaje",
    "depilacion",
    "masaje_relajacion",
    "peinado_eventos",
    "otro_belleza",
}

PHOTO_BUCKET = "llama-jaime-solicitudes"
DOCUMENT_BUCKET = "documentos-prestadores"
PROFILE_BUCKET = "prestadores-perfil"
PROFILE_MAX_BYTES = 5 * 1024 * 1024
PROFILE_MIME_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
DOCUMENT_TYPES = {
    "cedula_frontal", "cedula_reverso", "antecedentes", "cv",
    "titulo", "certificado", "licencia_conducir", "otro",
}
DOCUMENT_SINGLE_TYPES = {
    "cedula_frontal", "cedula_reverso", "antecedentes", "cv", "licencia_conducir",
}
DOCUMENT_MIME_TYPES = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
PHOTO_MAX_FILES = 5
PHOTO_MAX_BYTES = 8 * 1024 * 1024
PHOTO_MIME_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

TERMS_VERSION = "2026-09-27-v1"
REPORT_CATEGORIES = {"seguridad", "estafa", "trato", "cobro", "servicio", "contenido", "otro"}
RISKY_SERVICE_PATTERNS = (
    (r"\b(fuga|escape)\s+de\s+gas\b", "Una fuga de gas requiere atención de emergencia, no una publicación en la app."),
    (r"\b(arma|armas|explosivo|explosivos|municion|municiones)\b", "La app no admite servicios relacionados con armas o explosivos."),
    (r"\b(droga|drogas|cocaina|marihuana|trafico)\b", "La app no admite solicitudes relacionadas con actividades ilegales."),
    (r"\b(servicio sexual|servicios sexuales|escort)\b", "La app no admite servicios sexuales."),
    (r"\b(cirugia|inyeccion|procedimiento medico|tratamiento medico)\b", "La app no admite procedimientos médicos."),
    (r"\b(alta tension|asbesto|amianto)\b", "Ese trabajo requiere especialistas y protocolos que este MVP todavía no verifica."),
)


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


def _plain_assistant_text(value, limit=4000):
    """Normaliza respuestas para un chat que muestra texto plano, no Markdown."""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.*?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def _valid_email(value):
    value = _clean(value, 180).lower()
    return not value or bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def _valid_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return 8 <= len(digits) <= 15


def _normalize_phone(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 9 and digits.startswith("9"):
        return "56" + digits
    if len(digits) == 11 and digits.startswith("56"):
        return digits
    return digits[:15]


def _normalize_rut(value):
    compact = re.sub(r"[^0-9kK]", "", str(value or "")).upper()
    if len(compact) < 8 or len(compact) > 9 or not compact[:-1].isdigit():
        return ""
    return f"{int(compact[:-1])}-{compact[-1]}"


def _valid_rut(value):
    normalized = _normalize_rut(value)
    if not normalized:
        return False
    body, verifier = normalized.split("-", 1)
    total = 0
    factor = 2
    for digit in reversed(body):
        total += int(digit) * factor
        factor = 2 if factor == 7 else factor + 1
    expected_number = 11 - (total % 11)
    expected = "0" if expected_number == 11 else "K" if expected_number == 10 else str(expected_number)
    return hmac.compare_digest(expected, verifier)


def _valid_pin(value):
    return bool(re.fullmatch(r"\d{6}", str(value or "")))


def _pin_hash(pin, salt):
    return hashlib.pbkdf2_hmac("sha256", str(pin).encode("utf-8"), bytes.fromhex(salt), 210000).hex()


def _risky_service_reason(*values):
    text = _norm(" ".join(str(value or "") for value in values))
    for pattern, reason in RISKY_SERVICE_PATTERNS:
        if re.search(pattern, text):
            return reason
    return ""


def _valid_image_signature(content, mime_type):
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _valid_document_signature(content, mime_type):
    if mime_type == "application/pdf":
        return content.startswith(b"%PDF-")
    return _valid_image_signature(content, mime_type)


def _location_values(latitude, longitude, accuracy=None):
    try:
        latitude = round(float(latitude), 6)
        longitude = round(float(longitude), 6)
        accuracy = round(float(accuracy), 2) if accuracy not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    if accuracy is not None and not (0 <= accuracy <= 100000):
        accuracy = None
    return latitude, longitude, accuracy


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

    def _admin_auth():
        """Valida el JWT de Supabase Auth y confirma que pertenezca a un admin activo."""
        authorization = str(request.headers.get("Authorization") or "").strip()
        if not authorization.lower().startswith("bearer "):
            return None, ("Sesión de administrador requerida.", 401)

        access_token = authorization.split(None, 1)[1].strip()
        if not access_token:
            return None, ("Sesión de administrador requerida.", 401)

        base_headers = _db_headers()
        auth_headers = {
            "apikey": base_headers.get("apikey") or base_headers.get("Apikey") or "",
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        try:
            user_response = requests.get(
                f"{settings['supabase_url']}/auth/v1/user",
                headers=auth_headers,
                timeout=settings["supabase_timeout"],
            )
            if user_response.status_code != 200:
                return None, ("Sesión de administrador inválida o vencida.", 401)

            user = user_response.json() if user_response.content else {}
            auth_user_id = _clean(user.get("id"), 80)
            if not auth_user_id:
                return None, ("No pude identificar al administrador.", 401)

            admin_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_administradores",
                headers=_db_headers(),
                params={
                    "select": "id,empresa_id,auth_user_id,nombre,email,rol,activo,ultimo_acceso_at",
                    "auth_user_id": f"eq.{auth_user_id}",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "activo": "eq.true",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            admin_response.raise_for_status()
            rows = admin_response.json() if admin_response.content else []
            if not rows:
                return None, ("No tienes permisos de administrador.", 403)

            admin = rows[0]
            if admin.get("rol") not in {"superadmin", "admin", "moderador"}:
                return None, ("Rol administrativo no autorizado.", 403)

            # El fallo de este registro no debe invalidar una sesión correcta.
            try:
                requests.patch(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_administradores",
                    headers=_db_headers("return=minimal"),
                    params={"id": f"eq.{admin['id']}"},
                    json={"ultimo_acceso_at": datetime.now(timezone.utc).isoformat()},
                    timeout=settings["supabase_timeout"],
                ).raise_for_status()
            except Exception as exc:
                app.logger.warning("ADMIN LAST ACCESS ERROR: %r", exc)

            return admin, None

        except requests.RequestException as exc:
            app.logger.exception("ADMIN AUTH ERROR: %r", exc)
            return None, ("No pude validar la sesión administrativa.", 502)

    def _admin_required():
        admin, error = _admin_auth()
        if error:
            message, status = error
            return None, _json(app, {"ok": False, "error": message}, status)
        return admin, None

    def _safe_provider_admin(row):
        """Ficha administrativa sin hashes, salts ni tokens de acceso."""
        if not isinstance(row, dict):
            return {}
        blocked = {"access_token_hash", "pin_hash", "pin_salt"}
        return {key: value for key, value in row.items() if key not in blocked}

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
                "estado_cuenta": "eq.activa",
                "limit": "1",
            },
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        rows = response.json() if response.content else []
        return rows[0] if rows else None

    def _audit(event, actor_type=None, actor_id=None, request_id=None, metadata=None):
        try:
            requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_auditoria_seguridad",
                headers=_db_headers("return=minimal"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "evento": _clean(event, 80),
                    "actor_tipo": _clean(actor_type, 30) or None,
                    "actor_id": actor_id or None,
                    "solicitud_id": request_id or None,
                    "metadata": metadata if isinstance(metadata, dict) else {},
                },
                timeout=settings["supabase_timeout"],
            ).raise_for_status()
        except Exception as exc:
            app.logger.warning("SECURITY AUDIT ERROR event=%s: %r", event, exc)

    def _request_by_code(public_id):
        public_id = _clean(public_id, 40).upper()
        if not re.fullmatch(r"NX-\d{6}-[A-F0-9]{8}", public_id):
            return None
        response = requests.get(
            f"{settings['supabase_url']}/rest/v1/nexi_app_solicitudes",
            headers=_db_headers(),
            params={"select": "*", "public_id": f"eq.{public_id}", "limit": "1"},
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        rows = response.json() if response.content else []
        return rows[0] if rows else None

    def _client_auth(public_id, token):
        token = _clean(token, 100)
        request_row = _request_by_code(public_id)
        if not request_row or len(token) < 20:
            return None
        expected = str(request_row.get("access_token_hash") or "")
        actual = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return request_row if expected and expected == actual else None

    def _photo_rows(request_id):
        response = requests.get(
            f"{settings['supabase_url']}/rest/v1/nexi_app_fotos_solicitud",
            headers=_db_headers(),
            params={
                "select": "id,object_path,mime_type,created_at",
                "solicitud_id": f"eq.{request_id}",
                "order": "created_at.asc",
                "limit": str(PHOTO_MAX_FILES),
            },
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        return response.json() if response.content else []

    def _signed_photo_url(object_path, expires_in=3600):
        response = requests.post(
            f"{settings['supabase_url']}/storage/v1/object/sign/{PHOTO_BUCKET}/{object_path}",
            headers=_db_headers(),
            json={"expiresIn": expires_in},
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        signed = data.get("signedURL") or data.get("signedUrl") or ""
        if signed.startswith("http"):
            return signed
        if signed.startswith("/storage/v1/"):
            return f"{settings['supabase_url']}{signed}"
        if signed:
            return f"{settings['supabase_url']}/storage/v1{signed if signed.startswith('/') else '/' + signed}"
        return ""

    def _safe_photos(request_id):
        output = []
        for row in _photo_rows(request_id):
            url = _signed_photo_url(row.get("object_path"))
            if url:
                output.append({"id": row.get("id"), "url": url, "mime_type": row.get("mime_type")})
        return output

    def _send_custom_push(table, filter_key, filter_value, title, body, url, tag):
        if not settings.get("vapid_public_key") or not settings.get("vapid_private_key"):
            return 0
        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            app.logger.warning("PUSH DESACTIVADO: falta pywebpush")
            return 0
        response = requests.get(
            f"{settings['supabase_url']}/rest/v1/{table}",
            headers=_db_headers(),
            params={
                "select": "id,endpoint,p256dh,auth",
                filter_key: f"eq.{filter_value}",
                "activa": "eq.true",
                "limit": "10",
            },
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        payload = json.dumps({
            "title": title,
            "body": body,
            "url": url,
            "tag": tag,
        }, ensure_ascii=False)
        sent = 0
        for subscription in (response.json() if response.content else []):
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription["endpoint"],
                        "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
                    },
                    data=payload,
                    vapid_private_key=settings["vapid_private_key"],
                    vapid_claims={"sub": settings["vapid_contact"]},
                    ttl=3600,
                )
                sent += 1
            except WebPushException as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                app.logger.warning("CUSTOM PUSH ERROR table=%s status=%s", table, status)
                if status in {404, 410}:
                    requests.patch(
                        f"{settings['supabase_url']}/rest/v1/{table}",
                        headers=_db_headers("return=minimal"),
                        params={"id": f"eq.{subscription['id']}"},
                        json={"activa": False},
                        timeout=settings["supabase_timeout"],
                    )
        return sent

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
            "title": "Nueva oportunidad · Llama a Jaime",
            "body": f"{SERVICE_LABELS.get(request_row.get('tipo'), 'Servicio')} disponible en {request_row.get('comuna') or 'tu zona'}.",
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
        specialties = {_norm(x).replace(" ", "_") for x in (provider.get("especialidades") or [])}
        request_commune = _norm(request_row.get("comuna"))
        request_materials = {_norm(x) for x in (request_row.get("materiales") or []) if _norm(x)}
        provider_phone = _normalize_phone(provider.get("telefono_normalizado") or provider.get("telefono"))
        request_phone = _normalize_phone(request_row.get("telefono_normalizado") or request_row.get("telefono"))
        if provider_phone and request_phone and provider_phone == request_phone:
            return False
        if _norm(request_row.get("tipo")) not in roles:
            return False
        if request_commune and request_commune not in communes:
            return False
        if request_row.get("tipo") == "reciclaje" and request_materials and materials and not (request_materials & materials):
            return False
        if request_row.get("tipo") == "belleza" and request_row.get("subtipo") not in specialties:
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
                    "select": "id,roles,comunas,materiales,especialidades,vehiculo,telefono,telefono_normalizado,estado_cuenta",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "activo": "eq.true",
                    "estado_cuenta": "eq.activa",
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
                    "select": "id,public_id,tipo,subtipo,comuna,materiales,telefono,telefono_normalizado,estado,created_at",
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

    def _notify_pending_matches(request_row):
        """Vuelve a avisar a los prestadores disponibles tras un rechazo de precio."""
        try:
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_matches",
                headers=_db_headers(),
                params={
                    "select": "prestador_id",
                    "solicitud_id": f"eq.{request_row['id']}",
                    "estado": "eq.pendiente",
                    "limit": "500",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            for match in (response.json() if response.content else []):
                _send_custom_push(
                    "nexi_app_push_suscripciones",
                    "prestador_id",
                    match["prestador_id"],
                    "Oportunidad nuevamente disponible",
                    f"{SERVICE_LABELS.get(request_row.get('tipo'), 'Servicio')} en {request_row.get('comuna') or 'tu zona'}.",
                    "/app/?view=provider",
                    f"reabierta-{request_row['id']}",
                )
        except Exception as exc:
            app.logger.exception("REMATCH PUSH ERROR: %r", exc)

    @app.get("/app")
    @app.get("/app/")
    def mobile_index():
        response = send_from_directory(app_dir, "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/admin")
    @app.get("/admin/")
    def mobile_admin_page():
        response = send_from_directory(app_dir, "admin.html")
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
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
            "name": "Llama a Jaime Servicios",
            "version": settings["app_version"],
            "services": [
                {"id": key, "name": SERVICE_LABELS[key]}
                for key in ("hogar", "limpieza", "flete", "jardineria", "belleza", "reciclaje", "otro")
            ],
            "ai": bool(settings["ai_enabled"]),
            "dispatch": True,
            "push": bool(settings.get("vapid_public_key") and settings.get("vapid_private_key")),
            "chat": True,
            "quotes": True,
            "photos": True,
            "location": True,
            "security": {
                "provider_login": True,
                "self_assignment_blocked": True,
                "complaints": True,
                "terms_version": TERMS_VERSION,
                "location_mode": "voluntary_private_point_in_time",
            },
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
        if service not in ({""} | SERVICE_TYPES):
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
            "Eres Jaime, asistente de Llama a Jaime Servicios en Chile. "
            "Responde en español claro, cercano y breve. Ayudas a las personas a definir y publicar "
            "solicitudes de servicios para el hogar, limpieza, fletes, jardinería, belleza y bienestar, "
            "reciclaje u otras "
            "necesidades cotidianas. No inventes precios, disponibilidad, certificaciones, "
            "destinos ni estados. Explica que el valor final lo propone y confirma un prestador. "
            "Nunca pidas claves, datos bancarios ni documentos sensibles. Si existe una urgencia "
            "o riesgo físico, recomienda contactar servicios de emergencia. Cuando ya estén los "
            "datos esenciales, invita a pulsar 'Crear solicitud'. Responde siempre como texto plano: "
            "no uses Markdown, asteriscos, negritas, encabezados con #, tablas ni bloques de código. "
            "Si necesitas enumerar información, usa frases cortas o guiones simples."
        )
        context = (
            f"Servicio seleccionado: {service or 'sin seleccionar'}\n"
            + ("Conversación reciente:\n" + "\n".join(history_lines) + "\n" if history_lines else "")
            + f"Mensaje actual: {message}"
        )
        try:
            reply, usage = ai_generate(settings["ai_model"], instructions, context)
            reply = _plain_assistant_text(reply)
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
        subtype = _clean(body.get("subtipo"), 40).lower()
        name = _clean(body.get("nombre"), 120)
        phone = _clean(body.get("telefono"), 40)
        email = _clean(body.get("email"), 180).lower()
        commune = _clean(body.get("comuna"), 120)
        origin = _clean(body.get("direccion_origen"), 500)
        destination = _clean(body.get("direccion_destino"), 500)
        details = _clean(body.get("detalles"), 2000)
        schedule = _clean(body.get("fecha_preferida"), 120)
        materials = _list_clean(body.get("materiales"), item_limit=120, max_items=30)
        accepts_terms = body.get("acepta_terminos") is True
        accepts_privacy = body.get("acepta_privacidad") is True
        location = None
        if body.get("latitud") not in (None, "") or body.get("longitud") not in (None, ""):
            location = _location_values(body.get("latitud"), body.get("longitud"), body.get("precision_m"))
            if not location:
                return _json(app, {"ok": False, "error": "La ubicación compartida no es válida."}, 400)

        if service_type not in SERVICE_TYPES:
            return _json(app, {"ok": False, "error": "Selecciona una categoría de servicio."}, 400)
        if service_type == "belleza" and subtype not in BEAUTY_TYPES:
            return _json(app, {"ok": False, "error": "Selecciona el servicio de belleza que necesitas."}, 400)
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
        if not accepts_terms or not accepts_privacy:
            return _json(app, {"ok": False, "error": "Debes aceptar los términos y el tratamiento privado de tus datos."}, 400)
        risky_reason = _risky_service_reason(details, destination, subtype)
        if risky_reason:
            return _json(app, {"ok": False, "error": risky_reason}, 400)

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
            "subtipo": subtype if service_type == "belleza" else None,
            "nombre": name,
            "telefono": phone,
            "telefono_normalizado": _normalize_phone(phone),
            "email": email or None,
            "comuna": commune,
            "direccion_origen": origin,
            "direccion_destino": destination or None,
            "detalles": details,
            "materiales": materials,
            "fecha_preferida": schedule or None,
            "estado": "publicada",
            "canal": "app",
            "acepta_terminos": True,
            "acepta_privacidad": True,
            "consentimiento_at": datetime.now(timezone.utc).isoformat(),
            "terminos_version": TERMS_VERSION,
        }
        if location:
            payload.update({
                "cliente_latitud": location[0],
                "cliente_longitud": location[1],
                "cliente_precision_m": location[2],
                "cliente_ubicacion_at": datetime.now(timezone.utc).isoformat(),
            })
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
                Thread(target=_audit, args=("solicitud_creada", "cliente", None, created["id"], {"tipo": service_type}), daemon=True).start()
                Thread(target=_dispatch_request, args=(created,), daemon=True).start()
            return _json(app, {
                "ok": True,
                "solicitud": {
                    "codigo": public_id,
                    "token": access_token,
                    "tipo": service_type,
                    "subtipo": subtype if service_type == "belleza" else None,
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
                    "select": "public_id,tipo,subtipo,comuna,estado,fecha_preferida,created_at,updated_at",
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

    @app.post(f"{api_base}/solicitudes/<public_id>/fotos")
    def mobile_upload_request_photos(public_id):
        if not _allow("request-photos", limit=20):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de fotografías."}, 429)
        if request.content_length and request.content_length > (PHOTO_MAX_FILES * PHOTO_MAX_BYTES + 1024 * 1024):
            return _json(app, {"ok": False, "error": "La carga de fotografías es demasiado grande."}, 413)
        try:
            request_row = _client_auth(public_id, request.form.get("token"))
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)
            incoming = request.files.getlist("fotos")
            if not incoming:
                return _json(app, {"ok": False, "error": "Selecciona al menos una fotografía."}, 400)
            existing = _photo_rows(request_row["id"])
            available = PHOTO_MAX_FILES - len(existing)
            if available <= 0 or len(incoming) > available:
                return _json(app, {"ok": False, "error": f"Puedes guardar hasta {PHOTO_MAX_FILES} fotografías por solicitud."}, 400)

            prepared = []
            for photo in incoming:
                mime_type = _clean(photo.mimetype, 80).lower()
                extension = PHOTO_MIME_TYPES.get(mime_type)
                if not extension:
                    return _json(app, {"ok": False, "error": "Usa fotografías JPG, PNG o WebP."}, 400)
                content = photo.read(PHOTO_MAX_BYTES + 1)
                if not content or len(content) > PHOTO_MAX_BYTES:
                    return _json(app, {"ok": False, "error": "Cada fotografía debe pesar menos de 8 MB."}, 400)
                if not _valid_image_signature(content, mime_type):
                    return _json(app, {"ok": False, "error": "Uno de los archivos no es una imagen válida."}, 400)
                prepared.append((mime_type, extension, content))

            uploaded = []
            for mime_type, extension, content in prepared:
                object_path = f"{settings['empresa_id']}/{request_row['id']}/{uuid.uuid4().hex}.{extension}"
                storage_headers = {
                    **_db_headers(),
                    "Content-Type": mime_type,
                    "x-upsert": "false",
                }
                storage_response = requests.post(
                    f"{settings['supabase_url']}/storage/v1/object/{PHOTO_BUCKET}/{object_path}",
                    headers=storage_headers,
                    data=content,
                    timeout=max(30, settings["supabase_timeout"]),
                )
                storage_response.raise_for_status()
                metadata_response = requests.post(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_fotos_solicitud",
                    headers=_db_headers("return=representation"),
                    json={
                        "empresa_id": settings["empresa_id"],
                        "solicitud_id": request_row["id"],
                        "object_path": object_path,
                        "mime_type": mime_type,
                        "size_bytes": len(content),
                    },
                    timeout=settings["supabase_timeout"],
                )
                metadata_response.raise_for_status()
                rows = metadata_response.json() if metadata_response.content else []
                url = _signed_photo_url(object_path)
                uploaded.append({
                    "id": rows[0].get("id") if rows else None,
                    "url": url,
                    "mime_type": mime_type,
                })
            return _json(app, {"ok": True, "fotos": uploaded}, 201)
        except requests.RequestException as exc:
            app.logger.exception("REQUEST PHOTOS ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude guardar las fotografías."}, 502)

    @app.post(f"{api_base}/solicitudes/<public_id>/ubicacion")
    def mobile_share_location(public_id):
        if not _allow("share-location", limit=60):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de actualizaciones."}, 429)
        body = request.get_json(silent=True) or {}
        actor = _clean(body.get("actor"), 20).lower()
        location = _location_values(body.get("latitud"), body.get("longitud"), body.get("precision_m"))
        if not location:
            return _json(app, {"ok": False, "error": "No pude validar la ubicación."}, 400)
        now = datetime.now(timezone.utc).isoformat()
        try:
            if actor == "cliente":
                request_row = _client_auth(public_id, body.get("token"))
                if not request_row:
                    return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)
                response = requests.patch(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_solicitudes",
                    headers=_db_headers("return=minimal"),
                    params={"id": f"eq.{request_row['id']}"},
                    json={
                        "cliente_latitud": location[0],
                        "cliente_longitud": location[1],
                        "cliente_precision_m": location[2],
                        "cliente_ubicacion_at": now,
                    },
                    timeout=settings["supabase_timeout"],
                )
                response.raise_for_status()
                return _json(app, {"ok": True, "ubicacion": {"latitud": location[0], "longitud": location[1], "precision_m": location[2], "updated_at": now}})

            if actor == "prestador":
                provider = _provider_auth(body.get("codigo"), body.get("token"))
                request_row = _request_by_code(public_id) if provider else None
                if not request_row or str(request_row.get("prestador_id") or "") != str(provider.get("id") or ""):
                    return _json(app, {"ok": False, "error": "No tienes esta solicitud asignada."}, 401)
                response = requests.post(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_ubicaciones_prestador",
                    headers=_db_headers("return=representation,resolution=merge-duplicates"),
                    params={"on_conflict": "solicitud_id,prestador_id"},
                    json={
                        "empresa_id": settings["empresa_id"],
                        "solicitud_id": request_row["id"],
                        "prestador_id": provider["id"],
                        "latitud": location[0],
                        "longitud": location[1],
                        "precision_m": location[2],
                        "updated_at": now,
                    },
                    timeout=settings["supabase_timeout"],
                )
                response.raise_for_status()
                return _json(app, {"ok": True, "ubicacion": {"latitud": location[0], "longitud": location[1], "precision_m": location[2], "updated_at": now}})

            return _json(app, {"ok": False, "error": "Tipo de usuario inválido."}, 400)
        except requests.RequestException as exc:
            app.logger.exception("SHARE LOCATION ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude guardar la ubicación."}, 502)

    @app.post(f"{api_base}/prestadores")
    def mobile_create_provider():
        if not _allow("provider-register", limit=5):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de registros."}, 429)
        body = request.get_json(silent=True) or {}
        name = _clean(body.get("nombre"), 120)
        phone = _clean(body.get("telefono"), 40)
        email = _clean(body.get("email"), 180).lower()
        rut = _normalize_rut(body.get("rut"))
        pin = str(body.get("pin") or "")
        accepts_terms = body.get("acepta_terminos") is True
        accepts_privacy = body.get("acepta_privacidad") is True
        roles = [_norm(x) for x in _list_clean(body.get("roles"), 30, 8)]
        roles = list(dict.fromkeys(x for x in roles if x in SERVICE_TYPES))
        communes = _list_clean(body.get("comunas"), 120, 80)
        materials = _list_clean(body.get("materiales"), 120, 50)
        specialties = [
            _norm(x).replace(" ", "_")
            for x in _list_clean(body.get("especialidades"), 60, 20)
        ]
        specialties = list(dict.fromkeys(x for x in specialties if x in BEAUTY_TYPES))
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
        if not _valid_rut(rut):
            return _json(app, {"ok": False, "error": "Ingresa un RUT chileno válido."}, 400)
        if not _valid_pin(pin):
            return _json(app, {"ok": False, "error": "Crea una clave de acceso de exactamente 6 números."}, 400)
        if not accepts_terms or not accepts_privacy:
            return _json(app, {"ok": False, "error": "Debes aceptar los términos y la política de privacidad."}, 400)
        if "reciclaje" in roles and not materials:
            return _json(app, {"ok": False, "error": "Selecciona los materiales que recibes."}, 400)
        if "belleza" in roles and not specialties:
            return _json(app, {"ok": False, "error": "Selecciona al menos una especialidad de belleza."}, 400)
        if "flete" in roles and not vehicle:
            return _json(app, {"ok": False, "error": "Indica el vehículo que utilizas para fletes."}, 400)

        code = f"PR-{datetime.now(timezone.utc):%y%m%d}-{uuid.uuid4().hex[:8].upper()}"
        token = uuid.uuid4().hex
        pin_salt = os.urandom(16).hex()
        payload = {
            "public_id": code,
            "access_token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "empresa_id": settings["empresa_id"],
            "nombre": name,
            "telefono": phone,
            "telefono_normalizado": _normalize_phone(phone),
            "email": email or None,
            "rut_normalizado": rut,
            "pin_salt": pin_salt,
            "pin_hash": _pin_hash(pin, pin_salt),
            "roles": roles,
            "comunas": communes,
            "materiales": materials,
            "especialidades": specialties,
            "vehiculo": vehicle or None,
            "radio_km": radius,
            "disponible": True,
            "activo": True,
            "estado_cuenta": "activa",
            "estado_verificacion": "pendiente",
            "acepta_terminos": True,
            "acepta_privacidad": True,
            "consentimiento_at": datetime.now(timezone.utc).isoformat(),
            "terminos_version": TERMS_VERSION,
        }
        try:
            duplicate_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params={
                    "select": "id",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "rut_normalizado": f"eq.{rut}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            duplicate_response.raise_for_status()
            duplicate_rows = duplicate_response.json() if duplicate_response.content else []
            if duplicate_rows:
                return _json(app, {"ok": False, "error": "Ya existe una cuenta con este RUT. Usa la opción Ingresar."}, 409)
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers("return=representation"),
                json=payload,
                timeout=settings["supabase_timeout"],
            )
            if not response.ok:
                app.logger.error("PROVIDER DB ERROR %s: %s", response.status_code, response.text[:800])
                return _json(app, {"ok": False, "error": "No pude completar el registro. Revisa la migración de Llama a Jaime Servicios."}, 502)
            rows = response.json() if response.content else []
            created_provider = rows[0] if rows else None
            if created_provider:
                Thread(target=_audit, args=("prestador_registrado", "prestador", created_provider["id"], None, {"verificacion": "pendiente"}), daemon=True).start()
                Thread(target=_match_provider, args=(created_provider,), daemon=True).start()
            return _json(app, {
                "ok": True,
                "prestador": {"codigo": code, "token": token, "nombre": name, "roles": roles, "especialidades": specialties, "disponible": True, "estado_verificacion": "pendiente"},
            }, 201)
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER NETWORK ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude conectar con la base de datos."}, 502)

    @app.post(f"{api_base}/prestadores/login")
    def mobile_provider_login():
        if not _allow("provider-login", limit=10):
            return _json(app, {"ok": False, "error": "Demasiados intentos. Espera antes de volver a ingresar."}, 429)
        body = request.get_json(silent=True) or {}
        rut = _normalize_rut(body.get("rut"))
        pin = str(body.get("pin") or "")
        if not _valid_rut(rut) or not _valid_pin(pin):
            return _json(app, {"ok": False, "error": "RUT o clave incorrectos."}, 401)
        try:
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params={
                    "select": "*",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "rut_normalizado": f"eq.{rut}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            provider = rows[0] if rows else None
            salt = str((provider or {}).get("pin_salt") or "")
            stored_hash = str((provider or {}).get("pin_hash") or "")
            if not provider or not salt or not stored_hash or not hmac.compare_digest(_pin_hash(pin, salt), stored_hash):
                return _json(app, {"ok": False, "error": "RUT o clave incorrectos."}, 401)
            if not provider.get("activo") or provider.get("estado_cuenta") != "activa":
                return _json(app, {"ok": False, "error": "Esta cuenta está suspendida o cerrada. Contacta a soporte."}, 403)

            token = uuid.uuid4().hex
            login_at = datetime.now(timezone.utc).isoformat()
            update_response = requests.patch(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers("return=minimal"),
                params={"id": f"eq.{provider['id']}"},
                json={
                    "access_token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    "ultimo_login_at": login_at,
                },
                timeout=settings["supabase_timeout"],
            )
            update_response.raise_for_status()
            Thread(target=_audit, args=("prestador_login", "prestador", provider["id"]), daemon=True).start()
            return _json(app, {
                "ok": True,
                "prestador": {
                    "codigo": provider["public_id"],
                    "token": token,
                    "nombre": provider.get("nombre"),
                    "roles": provider.get("roles") or [],
                    "especialidades": provider.get("especialidades") or [],
                    "disponible": bool(provider.get("disponible")),
                    "estado_verificacion": provider.get("estado_verificacion") or "pendiente",
                },
            })
        except (ValueError, requests.RequestException) as exc:
            app.logger.exception("PROVIDER LOGIN ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude iniciar la sesión."}, 502)

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
                    "public_id", "nombre", "email", "roles", "comunas", "materiales", "especialidades",
                    "vehiculo", "radio_km", "disponible", "estado_cuenta", "estado_verificacion", "verificado_at", "created_at"
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
            if "especialidades" in body:
                update["especialidades"] = [
                    item for item in (
                        _norm(x).replace(" ", "_")
                        for x in _list_clean(body.get("especialidades"), 60, 20)
                    ) if item in BEAUTY_TYPES
                ]
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

    def _provider_document_rows(provider_id):
        response = requests.get(
            f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
            headers=_db_headers(),
            params={
                "select": "id,tipo_documento,nombre_documento,archivo_nombre,archivo_mime,archivo_bytes,estado_revision,fecha_emision,fecha_vencimiento,observacion_prestador,observacion_admin,revisado_at,created_at,updated_at",
                "empresa_id": f"eq.{settings['empresa_id']}",
                "prestador_id": f"eq.{provider_id}",
                "order": "created_at.desc",
                "limit": "200",
            },
            timeout=settings["supabase_timeout"],
        )
        response.raise_for_status()
        return response.json() if response.content else []

    def _refresh_documentation_status(provider_id):
        rows = _provider_document_rows(provider_id)
        latest = {}
        for row in rows:
            latest.setdefault(row.get("tipo_documento"), row)
        required = {"cedula_frontal", "cedula_reverso", "antecedentes"}
        present = {kind for kind in required if kind in latest}
        states = [latest[kind].get("estado_revision") for kind in present]
        if len(present) < len(required):
            status = "incompleta"
        elif any(state == "rechazado" for state in states):
            status = "observada"
        elif any(state == "en_revision" for state in states):
            status = "en_revision"
        elif all(state == "aprobado" for state in states):
            status = "aprobada"
        else:
            status = "pendiente"
        identity_ok = all(latest.get(kind, {}).get("estado_revision") == "aprobado" for kind in {"cedula_frontal", "cedula_reverso"})
        antecedents_ok = latest.get("antecedentes", {}).get("estado_revision") == "aprobado"
        requests.patch(
            f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
            headers=_db_headers("return=minimal"),
            params={"id": f"eq.{provider_id}", "empresa_id": f"eq.{settings['empresa_id']}"},
            json={
                "documentacion_estado": status,
                "documentacion_actualizada_at": datetime.now(timezone.utc).isoformat(),
                "identidad_documental_verificada": identity_ok,
                "antecedentes_verificados": antecedents_ok,
                "antecedentes_verificados_at": datetime.now(timezone.utc).isoformat() if antecedents_ok else None,
            },
            timeout=settings["supabase_timeout"],
        ).raise_for_status()
        return status

    @app.get(f"{api_base}/prestadores/foto-perfil")
    def mobile_provider_profile_photo():
        try:
            provider = _provider_auth(request.args.get("codigo"), request.args.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            path = provider.get("foto_perfil_path")
            signed_url = None
            if path:
                sign = requests.post(
                    f"{settings['supabase_url']}/storage/v1/object/sign/{PROFILE_BUCKET}/{path}",
                    headers=_db_headers(),
                    json={"expiresIn": 900},
                    timeout=settings["supabase_timeout"],
                )
                sign.raise_for_status()
                payload = sign.json() if sign.content else {}
                signed = payload.get("signedURL") or payload.get("signedUrl")
                if signed:
                    signed_url = signed if signed.startswith("http") else f"{settings['supabase_url']}/storage/v1{signed}"
            return _json(app, {"ok": True, "foto": {
                "url": signed_url,
                "nombre": provider.get("foto_perfil_nombre"),
                "mime": provider.get("foto_perfil_mime"),
                "bytes": provider.get("foto_perfil_bytes"),
                "actualizada_at": provider.get("foto_perfil_actualizada_at"),
            } if path else None})
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER PROFILE PHOTO ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude consultar la foto de perfil."}, 502)

    @app.post(f"{api_base}/prestadores/foto-perfil")
    def mobile_provider_upload_profile_photo():
        if not _allow("provider-profile-photo", limit=20):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de cargas."}, 429)
        if request.content_length and request.content_length > PROFILE_MAX_BYTES + 512 * 1024:
            return _json(app, {"ok": False, "error": "La foto supera el máximo permitido de 5 MB."}, 413)
        try:
            provider = _provider_auth(request.form.get("codigo"), request.form.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            incoming = request.files.get("archivo")
            if not incoming:
                return _json(app, {"ok": False, "error": "Selecciona una foto."}, 400)
            mime_type = _clean(incoming.mimetype, 80).lower()
            extension = PROFILE_MIME_TYPES.get(mime_type)
            if not extension:
                return _json(app, {"ok": False, "error": "Usa una imagen JPG, PNG o WebP."}, 400)
            content = incoming.read(PROFILE_MAX_BYTES + 1)
            if not content or len(content) > PROFILE_MAX_BYTES:
                return _json(app, {"ok": False, "error": "La foto debe pesar como máximo 5 MB."}, 400)
            if not _valid_document_signature(content, mime_type):
                return _json(app, {"ok": False, "error": "El contenido de la imagen no coincide con su formato."}, 400)

            original_name = _clean(incoming.filename, 240) or f"perfil.{extension}"
            object_path = f"{settings['empresa_id']}/{provider['id']}/perfil/{uuid.uuid4().hex}.{extension}"
            upload = requests.post(
                f"{settings['supabase_url']}/storage/v1/object/{PROFILE_BUCKET}/{object_path}",
                headers={**_db_headers(), "Content-Type": mime_type, "x-upsert": "false"},
                data=content,
                timeout=max(30, settings["supabase_timeout"]),
            )
            upload.raise_for_status()

            old_path = provider.get("foto_perfil_path")
            now = datetime.now(timezone.utc).isoformat()
            try:
                patch = requests.patch(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                    headers=_db_headers("return=representation"),
                    params={"id": f"eq.{provider['id']}", "empresa_id": f"eq.{settings['empresa_id']}"},
                    json={
                        "foto_perfil_path": object_path,
                        "foto_perfil_nombre": original_name,
                        "foto_perfil_mime": mime_type,
                        "foto_perfil_bytes": len(content),
                        "foto_perfil_actualizada_at": now,
                    },
                    timeout=settings["supabase_timeout"],
                )
                patch.raise_for_status()
            except Exception:
                requests.delete(
                    f"{settings['supabase_url']}/storage/v1/object/{PROFILE_BUCKET}/{object_path}",
                    headers=_db_headers(),
                    timeout=settings["supabase_timeout"],
                )
                raise

            if old_path and old_path != object_path:
                try:
                    requests.delete(
                        f"{settings['supabase_url']}/storage/v1/object/{PROFILE_BUCKET}/{old_path}",
                        headers=_db_headers(),
                        timeout=settings["supabase_timeout"],
                    ).raise_for_status()
                except requests.RequestException:
                    app.logger.warning("No pude eliminar la foto de perfil anterior: %s", old_path)

            return _json(app, {"ok": True, "foto": {
                "nombre": original_name, "mime": mime_type, "bytes": len(content), "actualizada_at": now
            }}, 201)
        except requests.RequestException as exc:
            app.logger.exception("UPLOAD PROFILE PHOTO ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude guardar la foto de perfil."}, 502)

    @app.delete(f"{api_base}/prestadores/foto-perfil")
    def mobile_provider_delete_profile_photo():
        body = request.get_json(silent=True) or {}
        try:
            provider = _provider_auth(body.get("codigo"), body.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            old_path = provider.get("foto_perfil_path")
            if not old_path:
                return _json(app, {"ok": True})

            patch = requests.patch(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers("return=minimal"),
                params={"id": f"eq.{provider['id']}", "empresa_id": f"eq.{settings['empresa_id']}"},
                json={
                    "foto_perfil_path": None,
                    "foto_perfil_nombre": None,
                    "foto_perfil_mime": None,
                    "foto_perfil_bytes": None,
                    "foto_perfil_actualizada_at": None,
                },
                timeout=settings["supabase_timeout"],
            )
            patch.raise_for_status()
            try:
                requests.delete(
                    f"{settings['supabase_url']}/storage/v1/object/{PROFILE_BUCKET}/{old_path}",
                    headers=_db_headers(),
                    timeout=settings["supabase_timeout"],
                ).raise_for_status()
            except requests.RequestException:
                app.logger.warning("No pude eliminar físicamente la foto de perfil: %s", old_path)
            return _json(app, {"ok": True})
        except requests.RequestException as exc:
            app.logger.exception("DELETE PROFILE PHOTO ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude eliminar la foto de perfil."}, 502)

    @app.get(f"{api_base}/prestadores/documentos")
    def mobile_provider_documents():
        try:
            provider = _provider_auth(request.args.get("codigo"), request.args.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            rows = _provider_document_rows(provider["id"])
            return _json(app, {
                "ok": True,
                "documentacion_estado": provider.get("documentacion_estado") or "incompleta",
                "documentos": rows,
                "tipos": sorted(DOCUMENT_TYPES),
            })
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER DOCUMENTS ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude consultar tus documentos."}, 502)

    @app.post(f"{api_base}/prestadores/documentos")
    def mobile_provider_upload_document():
        if not _allow("provider-documents", limit=30):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite temporal de cargas."}, 429)
        if request.content_length and request.content_length > DOCUMENT_MAX_BYTES + 1024 * 1024:
            return _json(app, {"ok": False, "error": "El archivo supera el máximo permitido de 10 MB."}, 413)
        try:
            provider = _provider_auth(request.form.get("codigo"), request.form.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            document_type = _clean(request.form.get("tipo_documento"), 40).lower()
            if document_type not in DOCUMENT_TYPES:
                return _json(app, {"ok": False, "error": "Tipo de documento inválido."}, 400)
            incoming = request.files.get("archivo")
            if not incoming:
                return _json(app, {"ok": False, "error": "Selecciona un archivo."}, 400)
            mime_type = _clean(incoming.mimetype, 80).lower()
            extension = DOCUMENT_MIME_TYPES.get(mime_type)
            if not extension:
                return _json(app, {"ok": False, "error": "Usa PDF, JPG, PNG o WebP."}, 400)
            content = incoming.read(DOCUMENT_MAX_BYTES + 1)
            if not content or len(content) > DOCUMENT_MAX_BYTES:
                return _json(app, {"ok": False, "error": "El archivo debe pesar como máximo 10 MB."}, 400)
            if not _valid_document_signature(content, mime_type):
                return _json(app, {"ok": False, "error": "El contenido del archivo no coincide con su formato."}, 400)

            original_name = _clean(incoming.filename, 240) or f"{document_type}.{extension}"
            object_path = f"{settings['empresa_id']}/{provider['id']}/{document_type}/{uuid.uuid4().hex}.{extension}"
            storage_response = requests.post(
                f"{settings['supabase_url']}/storage/v1/object/{DOCUMENT_BUCKET}/{object_path}",
                headers={**_db_headers(), "Content-Type": mime_type, "x-upsert": "false"},
                data=content,
                timeout=max(30, settings["supabase_timeout"]),
            )
            storage_response.raise_for_status()

            # Los documentos de una sola vigencia reemplazan el registro anterior.
            old_rows = []
            if document_type in DOCUMENT_SINGLE_TYPES:
                old_response = requests.get(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                    headers=_db_headers(),
                    params={
                        "select": "id,archivo_path",
                        "empresa_id": f"eq.{settings['empresa_id']}",
                        "prestador_id": f"eq.{provider['id']}",
                        "tipo_documento": f"eq.{document_type}",
                    },
                    timeout=settings["supabase_timeout"],
                )
                old_response.raise_for_status()
                old_rows = old_response.json() if old_response.content else []
                if old_rows:
                    requests.delete(
                        f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                        headers=_db_headers("return=minimal"),
                        params={"id": f"in.({','.join(row['id'] for row in old_rows)})"},
                        timeout=settings["supabase_timeout"],
                    ).raise_for_status()

            metadata_response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                headers=_db_headers("return=representation"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "prestador_id": provider["id"],
                    "tipo_documento": document_type,
                    "nombre_documento": _clean(request.form.get("nombre_documento"), 180) or None,
                    "archivo_path": object_path,
                    "archivo_nombre": original_name,
                    "archivo_mime": mime_type,
                    "archivo_bytes": len(content),
                    "estado_revision": "pendiente",
                    "fecha_emision": _clean(request.form.get("fecha_emision"), 10) or None,
                    "fecha_vencimiento": _clean(request.form.get("fecha_vencimiento"), 10) or None,
                    "observacion_prestador": _clean(request.form.get("observacion_prestador"), 1000) or None,
                },
                timeout=settings["supabase_timeout"],
            )
            metadata_response.raise_for_status()
            rows = metadata_response.json() if metadata_response.content else []

            # Elimina del Storage los archivos reemplazados sólo después de guardar el nuevo registro.
            for old in old_rows:
                old_path = old.get("archivo_path")
                if old_path:
                    try:
                        requests.delete(
                            f"{settings['supabase_url']}/storage/v1/object/{DOCUMENT_BUCKET}/{old_path}",
                            headers=_db_headers(),
                            timeout=settings["supabase_timeout"],
                        ).raise_for_status()
                    except Exception as exc:
                        app.logger.warning("OLD PROVIDER DOCUMENT DELETE ERROR: %r", exc)

            status = _refresh_documentation_status(provider["id"])
            Thread(target=_audit, args=("prestador_documento_subido", "prestador", provider["id"], None, {"tipo_documento": document_type}), daemon=True).start()
            return _json(app, {"ok": True, "documento": rows[0] if rows else None, "documentacion_estado": status}, 201)
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER DOCUMENT UPLOAD ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude guardar el documento."}, 502)

    @app.delete(f"{api_base}/prestadores/documentos/<document_id>")
    def mobile_provider_delete_document(document_id):
        body = request.get_json(silent=True) or {}
        try:
            provider = _provider_auth(request.args.get("codigo") or body.get("codigo"), request.args.get("token") or body.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            try:
                document_uuid = str(uuid.UUID(document_id))
            except (ValueError, TypeError):
                return _json(app, {"ok": False, "error": "Documento inválido."}, 400)
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                headers=_db_headers(),
                params={
                    "select": "id,archivo_path,tipo_documento,estado_revision",
                    "id": f"eq.{document_uuid}",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "prestador_id": f"eq.{provider['id']}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            if not rows:
                return _json(app, {"ok": False, "error": "Documento no encontrado."}, 404)
            document = rows[0]
            if document.get("estado_revision") == "aprobado":
                return _json(app, {"ok": False, "error": "Un documento aprobado no puede eliminarse directamente. Contacta a soporte para reemplazarlo."}, 409)
            requests.delete(
                f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                headers=_db_headers("return=minimal"),
                params={"id": f"eq.{document_uuid}"},
                timeout=settings["supabase_timeout"],
            ).raise_for_status()
            try:
                requests.delete(
                    f"{settings['supabase_url']}/storage/v1/object/{DOCUMENT_BUCKET}/{document['archivo_path']}",
                    headers=_db_headers(),
                    timeout=settings["supabase_timeout"],
                ).raise_for_status()
            except Exception as exc:
                app.logger.warning("PROVIDER DOCUMENT STORAGE DELETE ERROR: %r", exc)
            status = _refresh_documentation_status(provider["id"])
            Thread(target=_audit, args=("prestador_documento_eliminado", "prestador", provider["id"], None, {"tipo_documento": document.get("tipo_documento")}), daemon=True).start()
            return _json(app, {"ok": True, "documentacion_estado": status})
        except requests.RequestException as exc:
            app.logger.exception("PROVIDER DOCUMENT DELETE ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude eliminar el documento."}, 502)

    @app.get(f"{api_base}/oportunidades")
    def mobile_opportunities():
        try:
            provider = _provider_auth(request.args.get("codigo"), request.args.get("token"))
            if not provider:
                return _json(app, {"ok": False, "error": "Acceso de prestador inválido."}, 401)
            if provider.get("estado_cuenta") != "activa":
                return _json(app, {
                    "ok": True,
                    "disponible": False,
                    "estado_cuenta": provider.get("estado_cuenta"),
                    "estado_verificacion": provider.get("estado_verificacion"),
                    "habilitado": False,
                    "mensaje": "Tu cuenta no está activa.",
                    "oportunidades": [],
                })
            if provider.get("estado_verificacion") != "verificado":
                mensajes = {
                    "pendiente": "Tu identidad está pendiente de revisión. Te avisaremos aquí cuando sea verificada.",
                    "rechazado": "Tu verificación fue rechazada. Contacta a soporte para revisar tus antecedentes.",
                }
                return _json(app, {
                    "ok": True,
                    "disponible": False,
                    "estado_cuenta": provider.get("estado_cuenta"),
                    "estado_verificacion": provider.get("estado_verificacion"),
                    "habilitado": False,
                    "mensaje": mensajes.get(provider.get("estado_verificacion"), "Tu cuenta aún no está habilitada."),
                    "oportunidades": [],
                })
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
                        "select": "id,public_id,tipo,subtipo,comuna,direccion_origen,direccion_destino,detalles,materiales,fecha_preferida,estado,telefono,nombre,created_at",
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
                else:
                    item["fotos"] = _safe_photos(item["id"])
                output.append({"match_id": match["id"], "match_estado": match["estado"], "solicitud": item})
            return _json(app, {"ok": True, "disponible": provider.get("disponible"), "estado_cuenta": provider.get("estado_cuenta"), "estado_verificacion": provider.get("estado_verificacion"), "habilitado": True, "oportunidades": output})
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
            if provider.get("estado_cuenta") != "activa":
                return _json(app, {"ok": False, "error": "Tu cuenta no está activa."}, 403)
            if provider.get("estado_verificacion") != "verificado":
                return _json(app, {"ok": False, "error": "Debes tener tu identidad verificada para tomar solicitudes."}, 403)
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

    def _conversation_access(public_id, values):
        actor = _clean(values.get("actor"), 20).lower()
        if actor == "cliente":
            request_row = _client_auth(public_id, values.get("token"))
            return actor, request_row, None
        if actor == "prestador":
            provider = _provider_auth(values.get("codigo"), values.get("token"))
            request_row = _request_by_code(public_id) if provider else None
            if not request_row or str(request_row.get("prestador_id") or "") != str(provider.get("id") or ""):
                return actor, None, None
            return actor, request_row, provider
        return actor, None, None

    @app.route(f"{api_base}/solicitudes/<public_id>/conversacion", methods=["GET", "POST"])
    def mobile_conversation(public_id):
        body = request.get_json(silent=True) or {}
        values = request.args if request.method == "GET" else body
        try:
            actor, request_row, provider = _conversation_access(public_id, values)
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso a la conversación inválido."}, 401)
            provider_id = request_row.get("prestador_id")
            if request.method == "POST":
                content = _clean(body.get("mensaje"), 2000)
                if not provider_id:
                    return _json(app, {"ok": False, "error": "Aún no hay un prestador asignado."}, 409)
                if not content:
                    return _json(app, {"ok": False, "error": "Escribe un mensaje."}, 400)
                response = requests.post(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_mensajes_servicio",
                    headers=_db_headers("return=representation"),
                    json={
                        "empresa_id": settings["empresa_id"],
                        "solicitud_id": request_row["id"],
                        "prestador_id": provider_id,
                        "remitente_tipo": actor,
                        "contenido": content,
                    },
                    timeout=settings["supabase_timeout"],
                )
                response.raise_for_status()
                rows = response.json() if response.content else []
                if actor == "cliente":
                    _send_custom_push(
                        "nexi_app_push_suscripciones", "prestador_id", provider_id,
                        "Nuevo mensaje del cliente", content[:120],
                        "/app/?view=provider", f"mensaje-{request_row['id']}",
                    )
                else:
                    _send_custom_push(
                        "nexi_app_push_clientes", "solicitud_id", request_row["id"],
                        "Jaime tiene un nuevo mensaje", content[:120],
                        "/app/?view=status", f"mensaje-{request_row['id']}",
                    )
                return _json(app, {"ok": True, "mensaje": rows[0] if rows else {"contenido": content}}, 201)

            messages_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_mensajes_servicio",
                headers=_db_headers(),
                params={
                    "select": "id,remitente_tipo,contenido,cotizacion_id,created_at",
                    "solicitud_id": f"eq.{request_row['id']}",
                    "prestador_id": f"eq.{provider_id}",
                    "order": "created_at.asc",
                    "limit": "200",
                } if provider_id else {"select": "id", "limit": "0"},
                timeout=settings["supabase_timeout"],
            )
            messages_response.raise_for_status()
            quote = None
            if provider_id:
                quote_response = requests.get(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_cotizaciones_servicio",
                    headers=_db_headers(),
                    params={
                        "select": "id,monto_clp,detalle,estado,created_at",
                        "solicitud_id": f"eq.{request_row['id']}",
                        "prestador_id": f"eq.{provider_id}",
                        "estado": "in.(pendiente,aceptada)",
                        "order": "created_at.desc",
                        "limit": "1",
                    },
                    timeout=settings["supabase_timeout"],
                )
                quote_response.raise_for_status()
                quote_rows = quote_response.json() if quote_response.content else []
                quote = quote_rows[0] if quote_rows else None

            provider_name = None
            provider_location = None
            if provider_id:
                provider_response = requests.get(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                    headers=_db_headers(),
                    params={"select": "nombre", "id": f"eq.{provider_id}", "limit": "1"},
                    timeout=settings["supabase_timeout"],
                )
                provider_response.raise_for_status()
                provider_rows = provider_response.json() if provider_response.content else []
                provider_name = provider_rows[0].get("nombre") if provider_rows else None
                location_response = requests.get(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_ubicaciones_prestador",
                    headers=_db_headers(),
                    params={
                        "select": "latitud,longitud,precision_m,updated_at",
                        "solicitud_id": f"eq.{request_row['id']}",
                        "prestador_id": f"eq.{provider_id}",
                        "limit": "1",
                    },
                    timeout=settings["supabase_timeout"],
                )
                location_response.raise_for_status()
                location_rows = location_response.json() if location_response.content else []
                provider_location = location_rows[0] if location_rows else None

            client_location = None
            if request_row.get("cliente_latitud") is not None and request_row.get("cliente_longitud") is not None:
                client_location = {
                    "latitud": request_row.get("cliente_latitud"),
                    "longitud": request_row.get("cliente_longitud"),
                    "precision_m": request_row.get("cliente_precision_m"),
                    "updated_at": request_row.get("cliente_ubicacion_at"),
                }

            safe_request = {key: request_row.get(key) for key in (
                "public_id", "tipo", "comuna", "detalles", "fecha_preferida", "estado"
            )}
            return _json(app, {
                "ok": True,
                "actor": actor,
                "solicitud": safe_request,
                "prestador": {"nombre": provider_name or "Prestador"} if provider_id else None,
                "mensajes": messages_response.json() if messages_response.content and provider_id else [],
                "cotizacion": quote,
                "fotos": _safe_photos(request_row["id"]),
                "ubicaciones": {"cliente": client_location, "prestador": provider_location},
            })
        except requests.RequestException as exc:
            app.logger.exception("CONVERSATION ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude abrir la conversación."}, 502)

    @app.post(f"{api_base}/solicitudes/<public_id>/reclamos")
    def mobile_create_complaint(public_id):
        if not _allow("complaint", limit=5, window_seconds=86400):
            return _json(app, {"ok": False, "error": "Alcanzaste el límite diario de reportes."}, 429)
        body = request.get_json(silent=True) or {}
        category = _clean(body.get("categoria"), 30).lower()
        description = _clean(body.get("descripcion"), 2000)
        try:
            actor, request_row, provider = _conversation_access(public_id, body)
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso inválido para reportar esta solicitud."}, 401)
            provider_id = request_row.get("prestador_id")
            if not provider_id:
                return _json(app, {"ok": False, "error": "Todavía no existe un prestador asignado para reportar."}, 409)
            if category not in REPORT_CATEGORIES:
                return _json(app, {"ok": False, "error": "Selecciona un motivo válido."}, 400)
            if len(description) < 10:
                return _json(app, {"ok": False, "error": "Describe el problema con al menos 10 caracteres."}, 400)
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_reclamos",
                headers=_db_headers("return=representation"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "solicitud_id": request_row["id"],
                    "prestador_id": provider_id,
                    "reportante_tipo": actor,
                    "categoria": category,
                    "descripcion": description,
                    "estado": "abierto",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            complaint = rows[0] if rows else {"estado": "abierto"}
            actor_id = provider.get("id") if provider else None
            Thread(target=_audit, args=("reclamo_creado", actor, actor_id, request_row["id"], {"categoria": category}), daemon=True).start()
            return _json(app, {"ok": True, "reclamo": {"id": complaint.get("id"), "estado": complaint.get("estado", "abierto")}}, 201)
        except requests.RequestException as exc:
            app.logger.exception("COMPLAINT ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude registrar el reporte."}, 502)

    @app.post(f"{api_base}/solicitudes/<public_id>/cotizaciones")
    def mobile_create_quote(public_id):
        body = request.get_json(silent=True) or {}
        try:
            provider = _provider_auth(body.get("codigo"), body.get("token"))
            request_row = _request_by_code(public_id) if provider else None
            if not request_row or str(request_row.get("prestador_id") or "") != str(provider.get("id") or ""):
                return _json(app, {"ok": False, "error": "No tienes esta solicitud asignada."}, 401)
            digits = re.sub(r"\D", "", str(body.get("monto_clp") or ""))
            amount = int(digits) if digits else 0
            detail = _clean(body.get("detalle"), 500)
            if amount < 1000 or amount > 100000000:
                return _json(app, {"ok": False, "error": "Ingresa un precio válido en pesos chilenos."}, 400)
            accepted_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_cotizaciones_servicio",
                headers=_db_headers(),
                params={
                    "select": "id",
                    "solicitud_id": f"eq.{request_row['id']}",
                    "estado": "eq.aceptada",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            accepted_response.raise_for_status()
            if accepted_response.content and accepted_response.json():
                return _json(app, {"ok": False, "error": "El precio de este servicio ya fue confirmado."}, 409)
            requests.patch(
                f"{settings['supabase_url']}/rest/v1/nexi_app_cotizaciones_servicio",
                headers=_db_headers("return=minimal"),
                params={"solicitud_id": f"eq.{request_row['id']}", "estado": "eq.pendiente"},
                json={"estado": "reemplazada"},
                timeout=settings["supabase_timeout"],
            ).raise_for_status()
            quote_response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_cotizaciones_servicio",
                headers=_db_headers("return=representation"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "solicitud_id": request_row["id"],
                    "prestador_id": provider["id"],
                    "monto_clp": amount,
                    "detalle": detail or None,
                    "estado": "pendiente",
                },
                timeout=settings["supabase_timeout"],
            )
            quote_response.raise_for_status()
            quote_rows = quote_response.json() if quote_response.content else []
            quote = quote_rows[0] if quote_rows else None
            message_text = f"Cotización enviada: ${amount:,.0f} CLP".replace(",", ".")
            if detail:
                message_text += f" · {detail}"
            requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_mensajes_servicio",
                headers=_db_headers("return=minimal"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "solicitud_id": request_row["id"],
                    "prestador_id": provider["id"],
                    "remitente_tipo": "sistema",
                    "contenido": message_text,
                    "cotizacion_id": quote.get("id") if quote else None,
                },
                timeout=settings["supabase_timeout"],
            ).raise_for_status()
            _send_custom_push(
                "nexi_app_push_clientes", "solicitud_id", request_row["id"],
                "Recibiste una cotización", message_text,
                "/app/?view=status", f"cotizacion-{request_row['id']}",
            )
            return _json(app, {"ok": True, "cotizacion": quote}, 201)
        except requests.RequestException as exc:
            app.logger.exception("QUOTE CREATE ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude enviar la cotización."}, 502)

    @app.post(f"{api_base}/solicitudes/<public_id>/cotizaciones/<quote_id>/responder")
    def mobile_answer_quote(public_id, quote_id):
        body = request.get_json(silent=True) or {}
        action = _clean(body.get("accion"), 20).lower()
        try:
            request_row = _client_auth(public_id, body.get("token"))
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)
            if action not in {"aceptar", "rechazar"}:
                return _json(app, {"ok": False, "error": "Respuesta inválida."}, 400)
            quote_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_cotizaciones_servicio",
                headers=_db_headers(),
                params={
                    "select": "id,prestador_id,monto_clp",
                    "id": f"eq.{_clean(quote_id, 80)}",
                    "solicitud_id": f"eq.{request_row['id']}",
                    "estado": "eq.pendiente",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            quote_response.raise_for_status()
            quote_rows = quote_response.json() if quote_response.content else []
            if not quote_rows:
                return _json(app, {"ok": False, "error": "La cotización ya no está disponible."}, 409)
            quote = quote_rows[0]
            rpc_response = requests.post(
                f"{settings['supabase_url']}/rest/v1/rpc/nexi_app_responder_cotizacion",
                headers=_db_headers(),
                json={
                    "p_solicitud_id": request_row["id"],
                    "p_cotizacion_id": quote["id"],
                    "p_accion": action,
                },
                timeout=settings["supabase_timeout"],
            )
            rpc_response.raise_for_status()
            result = rpc_response.json() if rpc_response.content else {}
            if isinstance(result, list):
                result = result[0] if result else {}
            if not result.get("ok"):
                return _json(app, {"ok": False, "error": result.get("error") or "No pude responder."}, 409)
            system_text = "El cliente aceptó la cotización." if action == "aceptar" else "El cliente rechazó la cotización y la solicitud volvió a estar disponible."
            requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_mensajes_servicio",
                headers=_db_headers("return=minimal"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "solicitud_id": request_row["id"],
                    "prestador_id": quote["prestador_id"],
                    "remitente_tipo": "sistema",
                    "contenido": system_text,
                    "cotizacion_id": quote["id"],
                },
                timeout=settings["supabase_timeout"],
            ).raise_for_status()
            _send_custom_push(
                "nexi_app_push_suscripciones", "prestador_id", quote["prestador_id"],
                "Respuesta a tu cotización", system_text,
                "/app/?view=provider", f"precio-{request_row['id']}",
            )
            if action == "rechazar":
                Thread(target=_notify_pending_matches, args=(request_row,), daemon=True).start()
            return _json(app, {"ok": True, "resultado": result})
        except requests.RequestException as exc:
            app.logger.exception("QUOTE ANSWER ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude responder la cotización."}, 502)


    # ============================================================
    # ADMIN API PRIVADA
    # Requiere JWT Supabase Auth + registro activo en
    # nexi_app_administradores. No se enlaza desde la app pública.
    # ============================================================

    @app.post(f"{api_base}/admin/login")
    def mobile_admin_login():
        if not _allow("admin-login", 10, 900):
            return _json(app, {"ok": False, "error": "Demasiados intentos. Espera unos minutos."}, 429)

        body = request.get_json(silent=True) or {}
        email = _clean(body.get("email"), 320).lower()
        password = str(body.get("password") or "")

        if not email or "@" not in email or not password:
            return _json(app, {"ok": False, "error": "Correo y contraseña son obligatorios."}, 400)

        try:
            base_headers = _db_headers()
            auth_response = requests.post(
                f"{settings['supabase_url']}/auth/v1/token",
                headers={
                    "apikey": base_headers.get("apikey") or base_headers.get("Apikey") or "",
                    "Content-Type": "application/json",
                },
                params={"grant_type": "password"},
                json={"email": email, "password": password},
                timeout=settings["supabase_timeout"],
            )

            if auth_response.status_code != 200:
                return _json(app, {"ok": False, "error": "Credenciales incorrectas."}, 401)

            auth_data = auth_response.json() if auth_response.content else {}
            user = auth_data.get("user") or {}
            auth_user_id = _clean(user.get("id"), 80)
            access_token = str(auth_data.get("access_token") or "")
            refresh_token = str(auth_data.get("refresh_token") or "")
            expires_in = auth_data.get("expires_in")

            if not auth_user_id or not access_token:
                return _json(app, {"ok": False, "error": "No pude iniciar la sesión."}, 401)

            admin_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_administradores",
                headers=_db_headers(),
                params={
                    "select": "id,empresa_id,nombre,email,rol,activo",
                    "auth_user_id": f"eq.{auth_user_id}",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "activo": "eq.true",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            admin_response.raise_for_status()
            admins = admin_response.json() if admin_response.content else []

            if not admins:
                return _json(app, {"ok": False, "error": "Esta cuenta no tiene acceso al panel."}, 403)

            admin = admins[0]
            if admin.get("rol") not in {"superadmin", "admin", "moderador"}:
                return _json(app, {"ok": False, "error": "Rol administrativo no autorizado."}, 403)

            return _json(app, {
                "ok": True,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_in": expires_in,
                "admin": {
                    "id": admin["id"],
                    "nombre": admin["nombre"],
                    "email": admin["email"],
                    "rol": admin["rol"],
                },
            })

        except requests.RequestException as exc:
            app.logger.exception("ADMIN LOGIN ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude conectar con el servicio de autenticación."}, 502)


    @app.get(f"{api_base}/admin/me")
    def mobile_admin_me():
        admin, error_response = _admin_required()
        if error_response:
            return error_response
        return _json(app, {
            "ok": True,
            "admin": {
                "id": admin["id"],
                "nombre": admin["nombre"],
                "email": admin["email"],
                "rol": admin["rol"],
                "empresa_id": admin["empresa_id"],
            },
        })

    @app.get(f"{api_base}/admin/prestadores")
    def mobile_admin_providers():
        admin, error_response = _admin_required()
        if error_response:
            return error_response

        estado_verificacion = _clean(request.args.get("verificacion"), 20).lower()
        estado_cuenta = _clean(request.args.get("cuenta"), 20).lower()
        search = _clean(request.args.get("q"), 100)
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 200))
        except (TypeError, ValueError):
            limit = 100

        params = {
            "select": (
                "id,public_id,empresa_id,nombre,telefono,email,roles,comunas,"
                "especialidades,materiales,vehiculo,radio_km,disponible,activo,"
                "created_at,updated_at,rut_normalizado,telefono_normalizado,"
                "estado_cuenta,estado_verificacion,suspendido_at,motivo_suspension,"
                "ultimo_login_at,verificado_at,verificado_por,observacion_admin,"
                "ultima_revision_at"
            ),
            "empresa_id": f"eq.{admin['empresa_id']}",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if estado_verificacion in {"pendiente", "verificado", "rechazado"}:
            params["estado_verificacion"] = f"eq.{estado_verificacion}"
        if estado_cuenta in {"activa", "suspendida", "cerrada"}:
            params["estado_cuenta"] = f"eq.{estado_cuenta}"
        if search:
            safe_search = search.replace("%", "").replace(",", " ")
            params["or"] = f"(nombre.ilike.*{safe_search}*,email.ilike.*{safe_search}*,telefono.ilike.*{safe_search}*)"

        try:
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params=params,
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            return _json(app, {
                "ok": True,
                "prestadores": [_safe_provider_admin(row) for row in rows],
                "cantidad": len(rows),
            })
        except requests.RequestException as exc:
            app.logger.exception("ADMIN PROVIDERS ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude cargar los prestadores."}, 502)

    @app.get(f"{api_base}/admin/prestadores/<provider_id>")
    def mobile_admin_provider_detail(provider_id):
        admin, error_response = _admin_required()
        if error_response:
            return error_response

        try:
            provider_uuid = str(uuid.UUID(provider_id))
        except (ValueError, TypeError):
            return _json(app, {"ok": False, "error": "Prestador inválido."}, 400)

        try:
            provider_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params={
                    "select": "*",
                    "id": f"eq.{provider_uuid}",
                    "empresa_id": f"eq.{admin['empresa_id']}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            provider_response.raise_for_status()
            providers = provider_response.json() if provider_response.content else []
            if not providers:
                return _json(app, {"ok": False, "error": "Prestador no encontrado."}, 404)

            history_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_revision_prestadores",
                headers=_db_headers(),
                params={
                    "select": "id,accion,estado_anterior,estado_nuevo,observacion,metadata,created_at,administrador_id",
                    "prestador_id": f"eq.{provider_uuid}",
                    "empresa_id": f"eq.{admin['empresa_id']}",
                    "order": "created_at.desc",
                    "limit": "100",
                },
                timeout=settings["supabase_timeout"],
            )
            history_response.raise_for_status()

            claims_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_reclamos",
                headers=_db_headers(),
                params={
                    "select": "id,solicitud_id,reportante_tipo,categoria,descripcion,estado,resolucion,created_at,updated_at",
                    "prestador_id": f"eq.{provider_uuid}",
                    "empresa_id": f"eq.{admin['empresa_id']}",
                    "order": "created_at.desc",
                    "limit": "100",
                },
                timeout=settings["supabase_timeout"],
            )
            claims_response.raise_for_status()

            return _json(app, {
                "ok": True,
                "prestador": _safe_provider_admin(providers[0]),
                "historial": history_response.json() if history_response.content else [],
                "reclamos": claims_response.json() if claims_response.content else [],
            })
        except requests.RequestException as exc:
            app.logger.exception("ADMIN PROVIDER DETAIL ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude cargar la ficha del prestador."}, 502)


    @app.get(f"{api_base}/admin/prestadores/<provider_id>/documentos")
    def mobile_admin_provider_documents(provider_id):
        admin, error_response = _admin_required()
        if error_response:
            return error_response
        try:
            provider_uuid = str(uuid.UUID(provider_id))
        except (ValueError, TypeError):
            return _json(app, {"ok": False, "error": "Prestador inválido."}, 400)
        try:
            provider_response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params={"select":"id","id":f"eq.{provider_uuid}","empresa_id":f"eq.{admin['empresa_id']}","limit":"1"},
                timeout=settings["supabase_timeout"],
            )
            provider_response.raise_for_status()
            if not (provider_response.json() if provider_response.content else []):
                return _json(app, {"ok": False, "error": "Prestador no encontrado."}, 404)
            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                headers=_db_headers(),
                params={
                    "select":"id,tipo_documento,nombre_documento,archivo_path,archivo_nombre,archivo_mime,archivo_bytes,estado_revision,fecha_emision,fecha_vencimiento,observacion_prestador,observacion_admin,revisado_por,revisado_at,created_at,updated_at",
                    "empresa_id":f"eq.{admin['empresa_id']}",
                    "prestador_id":f"eq.{provider_uuid}",
                    "order":"created_at.desc",
                    "limit":"200",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            docs=response.json() if response.content else []
            # Nunca se devuelve archivo_path al navegador.
            for doc in docs:
                doc.pop("archivo_path", None)
            return _json(app, {"ok":True,"documentos":docs})
        except requests.RequestException as exc:
            app.logger.exception("ADMIN PROVIDER DOCUMENTS ERROR: %r", exc)
            return _json(app, {"ok":False,"error":"No pude cargar los documentos."},502)

    @app.get(f"{api_base}/admin/documentos/<document_id>/ver")
    def mobile_admin_view_document(document_id):
        admin, error_response = _admin_required()
        if error_response:
            return error_response
        try:
            document_uuid=str(uuid.UUID(document_id))
        except (ValueError,TypeError):
            return _json(app,{"ok":False,"error":"Documento inválido."},400)
        try:
            response=requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                headers=_db_headers(),
                params={"select":"id,archivo_path,archivo_nombre,archivo_mime","id":f"eq.{document_uuid}","empresa_id":f"eq.{admin['empresa_id']}","limit":"1"},
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows=response.json() if response.content else []
            if not rows:
                return _json(app,{"ok":False,"error":"Documento no encontrado."},404)
            doc=rows[0]
            sign=requests.post(
                f"{settings['supabase_url']}/storage/v1/object/sign/{DOCUMENT_BUCKET}/{doc['archivo_path']}",
                headers=_db_headers(),
                json={"expiresIn":300},
                timeout=settings["supabase_timeout"],
            )
            sign.raise_for_status()
            data=sign.json() if sign.content else {}
            signed=data.get("signedURL") or data.get("signedUrl") or ""
            if signed.startswith("/storage/v1/"):
                signed=f"{settings['supabase_url']}{signed}"
            elif signed and not signed.startswith("http"):
                signed=f"{settings['supabase_url']}/storage/v1{signed if signed.startswith('/') else '/' + signed}"
            if not signed:
                return _json(app,{"ok":False,"error":"No pude generar el acceso temporal."},502)
            return _json(app,{"ok":True,"url":signed,"expires_in":300,"nombre":doc.get("archivo_nombre"),"mime":doc.get("archivo_mime")})
        except requests.RequestException as exc:
            app.logger.exception("ADMIN DOCUMENT VIEW ERROR: %r",exc)
            return _json(app,{"ok":False,"error":"No pude abrir el documento."},502)

    @app.post(f"{api_base}/admin/documentos/<document_id>/revision")
    def mobile_admin_review_document(document_id):
        admin, error_response = _admin_required()
        if error_response:
            return error_response
        if not _allow("admin-document-review",180,3600):
            return _json(app,{"ok":False,"error":"Demasiadas revisiones. Intenta más tarde."},429)
        try:
            document_uuid=str(uuid.UUID(document_id))
        except (ValueError,TypeError):
            return _json(app,{"ok":False,"error":"Documento inválido."},400)
        body=request.get_json(silent=True) or {}
        state=_clean(body.get("estado"),20).lower()
        observation=_clean(body.get("observacion"),2000) or None
        if state not in {"pendiente","en_revision","aprobado","rechazado"}:
            return _json(app,{"ok":False,"error":"Estado de revisión inválido."},400)
        if state=="rechazado" and not observation:
            return _json(app,{"ok":False,"error":"Indica el motivo del rechazo para que el prestador pueda corregirlo."},400)
        try:
            response=requests.post(
                f"{settings['supabase_url']}/rest/v1/rpc/nexi_app_admin_revisar_documento",
                headers=_db_headers(),
                json={"p_admin_auth_user_id":admin["auth_user_id"],"p_documento_id":document_uuid,"p_estado":state,"p_observacion":observation},
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            result=response.json() if response.content else {}
            if isinstance(result,list):
                result=result[0] if result else {}
            if not result.get("ok"):
                return _json(app,{"ok":False,"error":result.get("error") or "No pude revisar el documento."},409)
            return _json(app,{"ok":True,"resultado":result})
        except requests.RequestException as exc:
            app.logger.exception("ADMIN DOCUMENT REVIEW ERROR: %r",exc)
            return _json(app,{"ok":False,"error":"No pude guardar la revisión."},502)

    @app.post(f"{api_base}/admin/prestadores/<provider_id>/accion")
    def mobile_admin_provider_action(provider_id):
        admin, error_response = _admin_required()
        if error_response:
            return error_response

        if not _allow("admin-provider-action", 120, 3600):
            return _json(app, {"ok": False, "error": "Demasiadas acciones administrativas. Intenta más tarde."}, 429)

        try:
            provider_uuid = str(uuid.UUID(provider_id))
        except (ValueError, TypeError):
            return _json(app, {"ok": False, "error": "Prestador inválido."}, 400)

        body = request.get_json(silent=True) or {}
        action = _clean(body.get("accion"), 20).lower()
        observation = _clean(body.get("observacion"), 2000) or None

        if action not in {"verificar", "rechazar", "suspender", "reactivar"}:
            return _json(app, {"ok": False, "error": "Acción administrativa inválida."}, 400)
        if action == "suspender" and not observation:
            return _json(app, {"ok": False, "error": "Debes indicar el motivo de la suspensión."}, 400)

        # Verificación final: exige los tres documentos básicos aprobados.
        if action == "verificar":
            try:
                docs_response = requests.get(
                    f"{settings['supabase_url']}/rest/v1/nexi_app_documentos_prestador",
                    headers=_db_headers(),
                    params={
                        "select": "tipo_documento,estado_revision,created_at",
                        "empresa_id": f"eq.{admin['empresa_id']}",
                        "prestador_id": f"eq.{provider_uuid}",
                        "tipo_documento": "in.(cedula_frontal,cedula_reverso,antecedentes)",
                        "order": "created_at.desc",
                    },
                    timeout=settings["supabase_timeout"],
                )
                docs_response.raise_for_status()
                rows = docs_response.json() if docs_response.content else []
                latest = {}
                for row in rows:
                    doc_type = row.get("tipo_documento")
                    if doc_type in {"cedula_frontal", "cedula_reverso", "antecedentes"} and doc_type not in latest:
                        latest[doc_type] = row.get("estado_revision")
                required = ("cedula_frontal", "cedula_reverso", "antecedentes")
                missing = [x for x in required if latest.get(x) != "aprobado"]
                if missing:
                    labels = {"cedula_frontal":"cédula frontal","cedula_reverso":"cédula reverso","antecedentes":"certificado de antecedentes"}
                    return _json(app, {
                        "ok": False,
                        "error": "No puedes verificar todavía. Deben estar aprobados: " + ", ".join(labels[x] for x in missing) + ".",
                        "documentacion_completa": False,
                        "pendientes": missing,
                    }, 409)
            except requests.RequestException as exc:
                app.logger.exception("ADMIN VERIFY DOCUMENT CHECK ERROR: %r", exc)
                return _json(app, {"ok": False, "error": "No pude validar la documentación antes de verificar."}, 502)

        # Moderador puede revisar/verificar/rechazar, pero no suspender/reactivar.
        if admin["rol"] == "moderador" and action in {"suspender", "reactivar"}:
            return _json(app, {"ok": False, "error": "Tu rol no permite suspender o reactivar cuentas."}, 403)

        try:
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/rpc/nexi_app_admin_accion_prestador",
                headers=_db_headers(),
                json={
                    "p_admin_auth_user_id": admin["auth_user_id"],
                    "p_prestador_id": provider_uuid,
                    "p_accion": action,
                    "p_observacion": observation,
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            result = response.json() if response.content else {}
            if not result.get("ok"):
                return _json(app, {"ok": False, "error": result.get("error") or "No pude aplicar la acción."}, 409)

            return _json(app, {"ok": True, "resultado": result})
        except requests.RequestException as exc:
            app.logger.exception("ADMIN PROVIDER ACTION ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude aplicar la acción administrativa."}, 502)


    @app.get(f"{api_base}/solicitudes/<public_id>/prestador")
    def mobile_public_provider_profile(public_id):
        """Ficha segura del prestador asignado, visible solo para el cliente dueño de la solicitud."""
        public_id = _clean(public_id, 40).upper()
        access_token = _clean(request.args.get("token"), 120)
        if not re.fullmatch(r"NX-\d{6}-[A-F0-9]{8}", public_id) or len(access_token) < 20:
            return _json(app, {"ok": False, "error": "Código o acceso inválido."}, 400)
        try:
            request_row = _client_auth(public_id, access_token)
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)
            provider_id = request_row.get("prestador_id")
            if not provider_id:
                return _json(app, {"ok": True, "prestador": None, "mensaje": "Aún no hay un prestador asignado."})

            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_prestadores",
                headers=_db_headers(),
                params={
                    "select": "id,nombre,roles,comunas,especialidades,radio_km,estado_verificacion,verificado_at,created_at,foto_perfil_path",
                    "id": f"eq.{provider_id}",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "activo": "eq.true",
                    "estado_cuenta": "eq.activa",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            if not rows:
                return _json(app, {"ok": False, "error": "Prestador no disponible."}, 404)

            provider = rows[0]

            # La foto vive en un bucket privado. Solo entregamos al cliente
            # autenticado una URL firmada temporal; nunca exponemos el path.
            profile_photo_url = None
            profile_photo_path = provider.get("foto_perfil_path")
            if profile_photo_path:
                photo_response = requests.post(
                    f"{settings['supabase_url']}/storage/v1/object/sign/{PROFILE_BUCKET}/{profile_photo_path}",
                    headers=_db_headers(),
                    json={"expiresIn": 900},
                    timeout=settings["supabase_timeout"],
                )
                photo_response.raise_for_status()
                photo_payload = photo_response.json() if photo_response.content else {}
                signed = photo_payload.get("signedURL") or photo_payload.get("signedUrl")
                if signed:
                    profile_photo_url = signed if signed.startswith("http") else f"{settings['supabase_url']}/storage/v1{signed}"

            # Nunca exponer RUT, teléfono, correo, documentos, tokens ni datos administrativos.
            reputation = {"promedio": 0, "evaluaciones": 0, "cinco_estrellas": 0}
            reputation_response = requests.post(
                f"{settings['supabase_url']}/rest/v1/rpc/nexi_app_resumen_reputacion",
                headers=_db_headers(),
                json={"p_prestador_id": provider.get("id")},
                timeout=settings["supabase_timeout"],
            )
            reputation_response.raise_for_status()
            reputation_data = reputation_response.json() if reputation_response.content else {}
            if isinstance(reputation_data, list):
                reputation_data = reputation_data[0] if reputation_data else {}
            if isinstance(reputation_data, dict):
                reputation = {
                    "promedio": float(reputation_data.get("promedio") or 0),
                    "evaluaciones": int(reputation_data.get("evaluaciones") or 0),
                    "cinco_estrellas": int(reputation_data.get("cinco_estrellas") or 0),
                }

            profile = {
                "id": provider.get("id"),
                "nombre": provider.get("nombre"),
                "roles": provider.get("roles") or [],
                "especialidades": provider.get("especialidades") or [],
                "comunas": provider.get("comunas") or [],
                "radio_km": provider.get("radio_km"),
                "verificado": provider.get("estado_verificacion") == "verificado",
                "verificado_at": provider.get("verificado_at"),
                "miembro_desde": provider.get("created_at"),
                "foto_url": profile_photo_url,
                "reputacion": reputation,
            }
            return _json(app, {"ok": True, "prestador": profile})
        except requests.RequestException as exc:
            app.logger.exception("PUBLIC PROVIDER PROFILE ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude cargar la ficha del prestador."}, 502)


    @app.get(f"{api_base}/solicitudes/<public_id>/evaluacion")
    def mobile_get_provider_review(public_id):
        """Consulta si el cliente ya evaluó al prestador asignado."""
        access_token = _clean(request.args.get("token"), 120)
        try:
            request_row = _client_auth(public_id, access_token)
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)
            provider_id = request_row.get("prestador_id")
            if not provider_id:
                return _json(app, {"ok": True, "evaluacion": None, "puede_evaluar": False})

            response = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_evaluaciones_prestador",
                headers=_db_headers(),
                params={
                    "select": "id,calificacion,comentario,created_at",
                    "empresa_id": f"eq.{settings['empresa_id']}",
                    "solicitud_id": f"eq.{request_row['id']}",
                    "prestador_id": f"eq.{provider_id}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            return _json(app, {
                "ok": True,
                "evaluacion": rows[0] if rows else None,
                "puede_evaluar": not bool(rows),
            })
        except requests.RequestException as exc:
            app.logger.exception("GET PROVIDER REVIEW ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude consultar la evaluación."}, 502)


    @app.post(f"{api_base}/solicitudes/<public_id>/evaluacion")
    def mobile_create_provider_review(public_id):
        """Crea una única evaluación del prestador realmente asignado a la solicitud."""
        if not _allow("provider-review", 30, 3600):
            return _json(app, {"ok": False, "error": "Demasiados intentos. Intenta más tarde."}, 429)

        body = request.get_json(silent=True) or {}
        access_token = _clean(body.get("token"), 120)
        comment = _clean(body.get("comentario"), 1000) or None
        try:
            rating = int(body.get("calificacion"))
        except (TypeError, ValueError):
            rating = 0
        if rating < 1 or rating > 5:
            return _json(app, {"ok": False, "error": "La calificación debe ser entre 1 y 5 estrellas."}, 400)

        try:
            request_row = _client_auth(public_id, access_token)
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)

            provider_id = request_row.get("prestador_id")
            if not provider_id:
                return _json(app, {"ok": False, "error": "Esta solicitud todavía no tiene un prestador asignado."}, 409)

            # Evita evaluar a un prestador distinto: el ID siempre sale de la solicitud autenticada.
            existing = requests.get(
                f"{settings['supabase_url']}/rest/v1/nexi_app_evaluaciones_prestador",
                headers=_db_headers(),
                params={
                    "select": "id",
                    "solicitud_id": f"eq.{request_row['id']}",
                    "limit": "1",
                },
                timeout=settings["supabase_timeout"],
            )
            existing.raise_for_status()
            if existing.json():
                return _json(app, {"ok": False, "error": "Esta solicitud ya fue evaluada."}, 409)

            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_evaluaciones_prestador",
                headers=_db_headers("return=representation"),
                json={
                    "empresa_id": settings["empresa_id"],
                    "solicitud_id": request_row["id"],
                    "prestador_id": provider_id,
                    "calificacion": rating,
                    "comentario": comment,
                    "visible": True,
                    "moderada": False,
                },
                timeout=settings["supabase_timeout"],
            )
            response.raise_for_status()
            rows = response.json() if response.content else []
            evaluation = rows[0] if rows else {
                "calificacion": rating,
                "comentario": comment,
            }
            _audit(
                "evaluacion_prestador_creada",
                actor_tipo="cliente",
                actor_id=request_row.get("id"),
                solicitud_id=request_row.get("id"),
                metadata={"prestador_id": provider_id, "calificacion": rating},
            )
            return _json(app, {"ok": True, "evaluacion": evaluation}, 201)
        except requests.RequestException as exc:
            # La restricción UNIQUE(solicitud_id) también protege contra envíos simultáneos.
            if getattr(exc, "response", None) is not None and exc.response.status_code == 409:
                return _json(app, {"ok": False, "error": "Esta solicitud ya fue evaluada."}, 409)
            app.logger.exception("CREATE PROVIDER REVIEW ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude guardar la evaluación."}, 502)


    @app.post(f"{api_base}/solicitudes/<public_id>/push/subscribe")
    def mobile_client_push_subscribe(public_id):
        body = request.get_json(silent=True) or {}
        subscription = body.get("subscription") if isinstance(body.get("subscription"), dict) else {}
        keys = subscription.get("keys") if isinstance(subscription.get("keys"), dict) else {}
        endpoint = _clean(subscription.get("endpoint"), 2000)
        p256dh = _clean(keys.get("p256dh"), 500)
        auth = _clean(keys.get("auth"), 500)
        try:
            request_row = _client_auth(public_id, body.get("token"))
            if not request_row:
                return _json(app, {"ok": False, "error": "Acceso de cliente inválido."}, 401)
            if not endpoint.startswith("https://") or not p256dh or not auth:
                return _json(app, {"ok": False, "error": "Suscripción push inválida."}, 400)
            response = requests.post(
                f"{settings['supabase_url']}/rest/v1/nexi_app_push_clientes",
                headers=_db_headers("return=representation,resolution=merge-duplicates"),
                params={"on_conflict": "solicitud_id,endpoint"},
                json={
                    "empresa_id": settings["empresa_id"],
                    "solicitud_id": request_row["id"],
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
            app.logger.exception("CLIENT PUSH SUBSCRIBE ERROR: %r", exc)
            return _json(app, {"ok": False, "error": "No pude activar las notificaciones."}, 502)
