import os
import json
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.errors import HttpError

# Cargar las credenciales desde una variable de entorno
# Asegúrate de que la variable de entorno 'GOOGLE_APPLICATION_CREDENTIALS' esté correctamente configurada
service_account_info = json.loads(os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))

# Crear las credenciales de la cuenta de servicio
credentials = service_account.Credentials.from_service_account_info(service_account_info)

# Crear el servicio de Google Calendar
service = build('calendar', 'v3', credentials=credentials)

# Datos del evento (sin asistentes)
event = {
    'summary': 'Reunión de trabajo',
    'location': 'Oficina de Ejemplo',
    'description': 'Discutir los detalles del proyecto.',
    'start': {
        'dateTime': '2025-08-07T09:00:00-07:00',  # Fecha y hora de inicio
        'timeZone': 'America/Los_Angeles',
    },
    'end': {
        'dateTime': '2025-08-07T10:00:00-07:00',  # Fecha y hora de finalización
        'timeZone': 'America/Los_Angeles',
    },
    'reminders': {
        'useDefault': True,
    },
}

# Intentar crear el evento en el calendario
try:
    event_result = service.events().insert(calendarId='primary', body=event).execute()
    print(f"Evento creado: {event_result.get('htmlLink')}")  # Imprime el enlace al evento creado

except HttpError as e:
    # Manejo específico para errores HTTP (como el 403)
    print(f"Error al crear el evento: HTTP {e.resp.status} - {e.error_details}")
    if e.resp.status == 403:
        print("Parece que tienes problemas de permisos. Verifica los permisos de la cuenta de servicio y la configuración de la API.")
    elif e.resp.status == 400:
        print("Hay un problema con los datos del evento (por ejemplo, formato incorrecto de fecha).")
    else:
        print("Error desconocido de la API de Google Calendar.")

except Exception as e:
    # Captura errores generales como problemas con las credenciales o el formato de los datos
    print(f"Error general: {str(e)}")

