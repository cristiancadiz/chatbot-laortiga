import os
import re
import html
from datetime import datetime, timedelta
from threading import Lock

import pytz
from dotenv import load_dotenv
from flask import Flask, request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from openai import OpenAI
from twilio.twiml.messaging_response import MessagingResponse
from werkzeug.middleware.proxy_fix import ProxyFix


APP_VERSION = "2026-09-04-V34-DIEGO-GOOGLE-SCOPES-UNIFICADOS"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

ESTILISTA_NOMBRE = os.getenv("ESTILISTA_NOMBRE", "Diego")
NEGOCIO_NOMBRE = os.getenv("NEGOCIO_NOMBRE", "Estilista Diego")
TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
DIRECCION_ATENCION = os.getenv("DIRECCION_ATENCION", "3 Poniente 382, Viña del Mar")
TELEFONO_EJECUTIVO = os.getenv("TELEFONO_EJECUTIVO", "+56966461436")

HORA_APERTURA = int(os.getenv("HORA_APERTURA", "10"))
HORA_CIERRE = int(os.getenv("HORA_CIERRE", "19"))
DURACION_RESERVA = int(os.getenv("DURACION_RESERVA", "60"))

# 0=lunes ... 5=sábado. Domingo cerrado.
DIAS_ATENCION = {0, 1, 2, 3, 4, 5}


def zona_local():
    return pytz.timezone(TIMEZONE)


def ahora_local():
    return datetime.now(zona_local())


def normalizar_texto(texto):
    texto = (texto or "").strip().lower()
    reemplazos = {
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u",
        "ü": "u", "ñ": "n",
    }
    for a, b in reemplazos.items():
        texto = texto.replace(a, b)
    return texto
