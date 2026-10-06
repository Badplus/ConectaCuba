# ConectaCuba v3

Versión preparada para PyCharm y para publicación posterior.

## Activación
1. Usuario se registra.
2. Cuenta queda PENDIENTE.
3. Pulsa “Solicitar activación por WhatsApp”.
4. Escribe al dueño de la plataforma.
5. El dueño confirma pago/acuerdo.
6. El dueño entra a /admin y activa la cuenta.
7. El usuario ya puede publicar, chatear, contactar y usar coincidencias.

No se utiliza SMS.

## PyCharm
- pip install -r requirements.txt
- python run.py
- http://127.0.0.1:5000

## Panel admin
- http://127.0.0.1:5000/admin/login
- clave local: CAMBIAR123

## Producción
- Gunicorn: gunicorn wsgi:app
- PostgreSQL mediante DATABASE_URL
- Configurar SECRET_KEY, ADMIN_PASSWORD y OWNER_WHATSAPP como variables de entorno.
