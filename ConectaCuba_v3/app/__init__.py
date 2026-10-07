import os
from datetime import timedelta
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from werkzeug.middleware.proxy_fix import ProxyFix

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"

OWNER_WHATSAPP = os.getenv("OWNER_WHATSAPP", "79939584193")
PROVINCIAS = {
    "Santiago de Cuba": ["Santiago de Cuba","Palma Soriano","Contramaestre","San Luis","Songo-La Maya","Mella","Segundo Frente","Tercer Frente","Guamá"],
    "Holguín": ["Holguín","Gibara","Rafael Freyre","Banes","Antilla","Báguanos","Calixto García","Cacocum","Urbano Noris","Cueto","Mayarí","Frank País","Sagua de Tánamo","Moa"],
}
MUNICIPIOS = [m for ms in PROVINCIAS.values() for m in ms]
PRODUCTOS = ["Plátano","Yuca","Boniato","Malanga","Arroz","Frijoles","Maíz","Tomate","Cebolla","Ajo","Ají","Calabaza","Pepino","Aguacate","Mango","Guayaba","Piña","Limón","Naranja","Carne de cerdo","Pollo","Huevos","Leche","Queso","Aceite","Azúcar","Harina","Otro"]

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

    db_url = os.getenv("DATABASE_URL", "sqlite:///conectacuba_v3.db")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = db_url
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["ADMIN_PASSWORD"] = os.getenv("ADMIN_PASSWORD", "CAMBIAR123")
    app.config["OWNER_WHATSAPP"] = OWNER_WHATSAPP
    app.config["TURNSTILE_SITE_KEY"] = os.getenv("TURNSTILE_SITE_KEY", "")
    app.config["TURNSTILE_SECRET_KEY"] = os.getenv("TURNSTILE_SECRET_KEY", "")
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

    db.init_app(app)
    login_manager.init_app(app)

    from .models import User, Setting

    @login_manager.user_loader
    def load_user(uid):
        return db.session.get(User, int(uid))

    from .routes import register_routes
    register_routes(app)

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    with app.app_context():
        db.create_all()
        if not Setting.query.first():
            db.session.add(Setting(commission=5.0))
            db.session.commit()

    return app
