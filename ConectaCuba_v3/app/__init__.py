import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db=SQLAlchemy(); login_manager=LoginManager(); login_manager.login_view="login"
OWNER_WHATSAPP=os.getenv("OWNER_WHATSAPP","79939584193")
MUNICIPIOS=["Santiago de Cuba","Palma Soriano","Contramaestre","San Luis","Songo-La Maya","Mella","Segundo Frente","Tercer Frente","Guamá"]
PRODUCTOS=["Plátano","Yuca","Boniato","Malanga","Arroz","Frijoles","Maíz","Tomate","Cebolla","Ajo","Ají","Calabaza","Pepino","Aguacate","Mango","Guayaba","Piña","Limón","Naranja","Carne de cerdo","Pollo","Huevos","Leche","Queso","Aceite","Azúcar","Harina","Otro"]

def create_app():
 app=Flask(__name__,instance_relative_config=True)
 app.config['SECRET_KEY']=os.getenv('SECRET_KEY','dev-secret-change-me')
 db_url=os.getenv('DATABASE_URL','sqlite:///conectacuba_v3.db')
 if db_url.startswith('postgres://'): db_url=db_url.replace('postgres://','postgresql+psycopg://',1)
 elif db_url.startswith('postgresql://'): db_url=db_url.replace('postgresql://','postgresql+psycopg://',1)
 app.config['SQLALCHEMY_DATABASE_URI']=db_url
 app.config['SQLALCHEMY_TRACK_MODIFICATIONS']=False
 app.config['ADMIN_PASSWORD']=os.getenv('ADMIN_PASSWORD','CAMBIAR123')
 app.config['OWNER_WHATSAPP']=OWNER_WHATSAPP
 db.init_app(app); login_manager.init_app(app)
 from .models import User,Setting
 @login_manager.user_loader
 def load_user(uid): return db.session.get(User,int(uid))
 from .routes import register_routes
 register_routes(app)
 with app.app_context():
  db.create_all()
  if not Setting.query.first(): db.session.add(Setting(commission=5.0)); db.session.commit()
 return app
