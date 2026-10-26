import math
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from functools import wraps
from datetime import datetime
from flask import render_template,request,redirect,url_for,flash,session,current_app,abort
from flask_login import login_user,logout_user,login_required,current_user
from sqlalchemy import or_,and_
from . import db,MUNICIPIOS,PRODUCTOS,PROVINCIAS
from .models import User,Listing,Notification,Message,Setting

def approved_required(fn):
 @wraps(fn)
 def w(*a,**k):
  if not current_user.approved: return redirect(url_for('pending'))
  return fn(*a,**k)
 return w

# La base de datos de prueba se limpió; los roles antiguos ya no se admiten.
SELLER_ROLES = {'vendedor', 'admin_bodega'}
REGISTRATION_ROLES = {'comprador', 'vendedor', 'admin_bodega'}

def seller_required(fn):
 @wraps(fn)
 def w(*a, **k):
  if current_user.role not in SELLER_ROLES:
   abort(403)
  return fn(*a, **k)
 return w

def admin_required(fn):
 @wraps(fn)
 def w(*a,**k):
  if not session.get('admin_ok'): return redirect(url_for('admin_login'))
  return fn(*a,**k)
 return w

def province_for(municipality):
 for province, municipalities in PROVINCIAS.items():
  if municipality in municipalities:
   return province
 abort(400, description='Municipio no válido.')

def product_name(x):
 return x.custom_product if x.product == 'Otro' and x.custom_product else x.product

def turnstile_valid():
 secret=current_app.config.get('TURNSTILE_SECRET_KEY')
 site_key=current_app.config.get('TURNSTILE_SITE_KEY')
 if not secret and not site_key:
  return True
 if not secret or not site_key:
  current_app.logger.error('Turnstile incompleto: faltan claves de configuración.')
  return False
 token=request.form.get('cf-turnstile-response','')
 if not token:
  return False
 payload=urlencode({'secret':secret,'response':token,'remoteip':request.remote_addr or ''}).encode('utf-8')
 try:
  req=Request('https://challenges.cloudflare.com/turnstile/v0/siteverify',data=payload,method='POST')
  with urlopen(req,timeout=5) as response:
   result=json.load(response)
  return result.get('success') is True
 except Exception:
  current_app.logger.exception('No se pudo verificar Turnstile')
  return False

def register_routes(app):
 @app.context_processor
 def ctx():
  s=Setting.query.first(); unread=0
  if current_user.is_authenticated: unread=Notification.query.filter_by(user_id=current_user.id,read=False).count()
  return dict(MUNICIPIOS=MUNICIPIOS,PRODUCTOS=PRODUCTOS,OWNER_WHATSAPP=current_app.config['OWNER_WHATSAPP'],commission=s.commission if s else 0,unread=unread)

 @app.route('/')
 def index():
  q=request.args.get('q','').strip(); kind='oferta'; muni=request.args.get('municipality','')
  qry=Listing.query.join(User).filter(Listing.active.is_(True),User.approved.is_(True))
  if q: qry=qry.filter(or_(Listing.product.ilike(f'%{q}%'),Listing.custom_product.ilike(f'%{q}%')))
  qry=qry.filter(Listing.kind=='oferta')
  if muni in MUNICIPIOS: qry=qry.filter(Listing.municipality==muni)
  return render_template('index.html',listings=qry.order_by(Listing.created_at.desc()).all(),q=q,kind=kind,muni=muni)

 @app.route('/register',methods=['GET','POST'])
 def register():
  if request.method=='POST':
   if not turnstile_valid(): abort(400, description='Verificación de seguridad fallida.')
   d=request.form
   phone=d.get('phone','').strip()
   role=d.get('role','').strip()
   full_name=d.get('full_name','').strip()
   municipality=d.get('municipality','').strip()
   address=d.get('address','').strip()
   password=d.get('password','')
   if role not in REGISTRATION_ROLES:
    abort(400, description='Tipo de usuario no válido.')
   if not phone or not full_name or not password or not address or municipality not in MUNICIPIOS:
    flash('Completa correctamente todos los campos.','danger')
    return redirect(url_for('register'))
   if User.query.filter_by(phone=phone).first():
    flash('Ese teléfono ya está registrado.','danger')
    return redirect(url_for('register'))
   auto_approved = role == 'comprador'
   u=User(full_name=full_name,phone=phone,role=role,municipality=municipality,province=province_for(municipality),
          address=address,approved=auto_approved,
          approved_at=datetime.utcnow() if auto_approved else None)
   u.set_password(password)
   db.session.add(u)
   db.session.commit()
   login_user(u)
   if auto_approved:
    flash('¡Bienvenido a Mercado BETO Plus! Tu cuenta de comprador está activa.','success')
    return redirect(url_for('index'))
   flash('Registro completado. Solicita tu activación por WhatsApp.','success')
   return redirect(url_for('pending'))
  return render_template('register.html')

 @app.route('/pending')
 @login_required
 def pending():
  if current_user.approved: return redirect(url_for('index' if current_user.role=='comprador' else 'dashboard'))
  text=f'Hola, soy {current_user.full_name}. Me registré en Mercado BETO Plus con el teléfono {current_user.phone}, como {current_user.role}, municipio {current_user.municipality}. Quiero solicitar la activación de mi cuenta.'
  return render_template('pending.html',text=text)

 @app.route('/login',methods=['GET','POST'])
 def login():
  if request.method=='POST':
   if not turnstile_valid(): abort(400, description='Verificación de seguridad fallida.')
   u=User.query.filter_by(phone=request.form['phone'].strip()).first()
   if not u or not u.check_password(request.form['password']): flash('Credenciales incorrectas.','danger'); return redirect(url_for('login'))
   login_user(u)
   if not u.approved: return redirect(url_for('pending'))
   return redirect(url_for('index' if u.role=='comprador' else 'dashboard'))
  return render_template('login.html')

 @app.route('/logout',methods=['POST'])
 @login_required
 def logout(): logout_user(); return redirect(url_for('index'))

 @app.route('/dashboard')
 @login_required
 def dashboard():
  if not current_user.approved: return redirect(url_for('pending'))
  if current_user.role not in SELLER_ROLES: return redirect(url_for('index'))
  ls=Listing.query.filter_by(owner_id=current_user.id).order_by(Listing.created_at.desc()).all(); notes=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(8).all(); return render_template('dashboard.html',listings=ls,notes=notes)

 @app.route('/publish',methods=['GET','POST'])
 @login_required
 @approved_required
 @seller_required
 def publish():
  if request.method=='POST':
   d=request.form
   if d.get('kind', 'oferta') != 'oferta': abort(400, description='Solo se permiten publicaciones de venta.')
   if d.get('product') not in PRODUCTOS or d.get('municipality') not in MUNICIPIOS:
    abort(400, description='Producto o municipio no válido.')
   if d.get('unit') not in {'lb','kg','unidad','saco','caja','L'} or d.get('coverage') not in {'municipio','cercanos','provincia'}:
    abort(400, description='Unidad o zona de venta no válida.')
   if d.get('product') == 'Otro' and not d.get('custom_product','').strip():
    abort(400, description='Especifica el nombre del producto.')
   try:
    quantity=float(d.get('quantity',''))
    price=float(d.get('price',''))
   except (ValueError, TypeError):
    abort(400, description='Cantidad o precio no válido.')
   if not (math.isfinite(quantity) and math.isfinite(price) and 0 < quantity < 1e12 and 0 <= price < 1e12):
    abort(400, description='Cantidad o precio fuera de rango.')
   item=Listing(kind='oferta',product=d['product'],custom_product=d.get('custom_product') or None,quantity=quantity,unit=d['unit'],price=price,municipality=d['municipality'],province=province_for(d['municipality']),coverage=d['coverage'],description=d.get('description',''),owner_id=current_user.id); db.session.add(item); db.session.commit()
   flash('Producto publicado correctamente.','success'); return redirect(url_for('dashboard'))
  return render_template('publish.html')

 @app.route('/matches/<int:listing_id>')
 @login_required
 @approved_required
 def matches(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  if item.owner_id!=current_user.id: abort(403)
  flash('Las coincidencias entre ofertas y demandas ya no se utilizan.','info')
  return redirect(url_for('dashboard'))

 @app.route('/listing/<int:listing_id>')
 def detail(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item or not item.active or not item.owner.approved: abort(404)
  return render_template('detail.html',listing=item)

 @app.route('/notifications')
 @login_required
 def notifications():
  if not current_user.approved: return redirect(url_for('pending'))
  notes=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
  for n in notes: n.read=True
  db.session.commit(); return render_template('notifications.html',notes=notes)

 @app.route('/chat/<int:user_id>',methods=['GET','POST'])
 @login_required
 @approved_required
 def chat(user_id):
  other=db.session.get(User,user_id)
  if not other or not other.approved or other.id==current_user.id: abort(404)
  if request.method=='POST':
   body=request.form.get('body','').strip()
   if len(body)>5000: abort(400, description='El mensaje supera el límite permitido.')
   if body: db.session.add(Message(sender_id=current_user.id,receiver_id=other.id,body=body)); db.session.add(Notification(user_id=other.id,message=f'Nuevo mensaje de {current_user.full_name}.')); db.session.commit()
   return redirect(url_for('chat',user_id=user_id))
  msgs=Message.query.filter(or_(and_(Message.sender_id==current_user.id,Message.receiver_id==other.id),and_(Message.sender_id==other.id,Message.receiver_id==current_user.id))).order_by(Message.created_at.asc()).all(); return render_template('chat.html',other=other,msgs=msgs)

 @app.route('/admin/login',methods=['GET','POST'])
 def admin_login():
  if request.method=='POST' and not turnstile_valid(): abort(400, description='Verificación de seguridad fallida.')
  if request.method=='POST' and request.form.get('password')==current_app.config['ADMIN_PASSWORD']: session['admin_ok']=True; return redirect(url_for('admin'))
  return render_template('admin_login.html')

 @app.route('/admin')
 @admin_required
 def admin(): return render_template('admin.html',users=User.query.order_by(User.created_at.desc()).all(),setting=Setting.query.first())

 @app.route('/admin/user/<int:user_id>/toggle',methods=['POST'])
 @admin_required
 def admin_toggle(user_id):
  u=db.session.get(User,user_id)
  if not u: abort(404)
  u.approved=not u.approved; u.approved_at=datetime.utcnow() if u.approved else None
  if u.approved: db.session.add(Notification(user_id=u.id,message='Tu cuenta ha sido ACTIVADA por el administrador.'))
  db.session.commit(); return redirect(url_for('admin'))

 @app.route('/admin/commission',methods=['POST'])
 @admin_required
 def admin_commission():
  s=Setting.query.first()
  if not s: abort(404)
  try: value=float(request.form['commission'])
  except (ValueError, KeyError): abort(400)
  if not (0 <= value <= 100): abort(400)
  s.commission=value; db.session.commit(); return redirect(url_for('admin'))
