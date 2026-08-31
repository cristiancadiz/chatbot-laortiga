import os
import re
import html
import json
import hmac
import hashlib
import gc
import uuid
import time
from datetime import datetime
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError
from difflib import SequenceMatcher

import requests
import pytz
from dotenv import load_dotenv
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client as TwilioClient
from openai import OpenAI
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from werkzeug.middleware.proxy_fix import ProxyFix


APP_VERSION = "2026-08-31-V51-LAORTIGA-EXTERNAL-SHIPPING-AWARE"
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me-in-render")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# ============================================================
# CONFIGURACION GENERAL
# ============================================================

TIMEZONE = os.getenv("TIMEZONE", "America/Santiago")
NEGOCIO_NOMBRE = os.getenv("NEGOCIO_NOMBRE", "La Ortiga")
APP_BASE_URL = (
    os.getenv("APP_BASE_URL")
    or os.getenv("RENDER_EXTERNAL_URL")
    or "https://chatbot-laortiga-hddw.onrender.com"
).rstrip("/
