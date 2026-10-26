import hmac
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from functools import wraps
from datetime import datetime
from flask import render_template,request,redirect,url_for,flash,session,current_app,abort
from flask_login import login_user,logout_user,login_required,current_user
from sqlalchemy import or_,and_
from . import db,MUNICIPIOS,PRODUCTOS
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
  if not session.get('admin_ok') and not (current_user.is_authenticated and current_user.is_admin): return redirect(url_for('login'))
  return fn(*a,**k)
 return w

def product_name(x): return x.custom_product if x.product=='Otro' and x.custom_product else x.product
def norm(s): return ' '.join((s or '').strip().lower().split())
def matches_for(item):
 out=[]
 for o in Listing.query.filter(Listing.id!=item.id,Listing.active.is_(True),Listing.kind!=item.kind).all():
  if not o.owner.approved or norm(product_name(o))!=norm(product_name(item)): continue
  offer=item if item.kind=='oferta' else o; demand=o if item.kind=='oferta' else item
  geo=3 if offer.municipality==demand.municipality else (2 if 'provincia' in [offer.coverage,demand.coverage] else 1 if 'cercanos' in [offer.coverage,demand.coverage] else 0)
  if geo==0: continue
  qty=2 if offer.quantity>=demand.quantity else 1 if offer.quantity>=demand.quantity*.5 else 0
  price=3 if offer.price<=demand.price else 0
  score=geo+qty+price
  out.append((o,score,'Coincidencia excelente' if score>=7 else 'Buena coincidencia' if score>=5 else 'Coincidencia posible'))
 return sorted(out,key=lambda x:x[1],reverse=True)

def turnstile_valid():
 secret=current_app.config.get('TURNSTILE_SECRET_KEY')
 site_key=current_app.config.get('TURNSTILE_SITE_KEY')
 if not secret or not site_key:
  current_app.logger.error('Turnstile no configurado completamente.')
  return False
 token=request.form.get('cf-turnstile-response','')
 if not token:
  return False
 try:
  payload=urlencode({'secret':secret,'response':token,'remoteip':request.remote_addr or ''}).encode()
  req=Request('https://challenges.cloudflare.com/turnstile/v0/siteverify',data=payload,method='POST')
  with urlopen(req,timeout=5) as response:
   return json.load(response).get('success') is True
 except Exception:
  current_app.logger.exception('Error al verificar Turnstile')
  return False

def register_routes(app):
 @app.context_processor
 def ctx():
  s=Setting.query.first(); unread=0
  if current_user.is_authenticated: unread=Notification.query.filter_by(user_id=current_user.id,read=False).count()
  return dict(MUNICIPIOS=MUNICIPIOS,PRODUCTOS=PRODUCTOS,OWNER_WHATSAPP=current_app.config['OWNER_WHATSAPP'],commission=s.commission if s else 0,unread=unread)

 @app.route('/')
 def index():
  q=request.args.get('q','').strip(); kind=request.args.get('kind',''); muni=request.args.get('municipality','')
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
   u=User(full_name=full_name,phone=phone,role=role,municipality=municipality,
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
   if u.is_admin:
    session['admin_ok']=True
    return redirect(url_for('admin'))
   if not u.approved: return redirect(url_for('pending'))
   return redirect(url_for('index' if u.role=='comprador' else 'dashboard'))
  return render_template('login.html')

 @app.route('/logout')
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
   try:
    quantity=float(d.get('quantity',''))
    price=float(d.get('price',''))
   except (ValueError, TypeError):
    abort(400, description='Cantidad o precio no válido.')
   if not (0 < quantity < 1e12 and 0 <= price < 1e12):
    abort(400, description='Cantidad o precio fuera de rango.')
   item=Listing(kind='oferta',product=d['product'],custom_product=d.get('custom_product') or None,quantity=quantity,unit=d['unit'],price=price,municipality=d['municipality'],coverage=d['coverage'],description=d.get('description',''),owner_id=current_user.id); db.session.add(item); db.session.commit()
   for other,score,level in matches_for(item)[:10]:
    db.session.add(Notification(user_id=current_user.id,listing_id=other.id,message=f'{level}: {product_name(other)} en {other.municipality}.')); db.session.add(Notification(user_id=other.owner_id,listing_id=item.id,message=f'{level}: nueva coincidencia de {product_name(item)} en {item.municipality}.'))
   db.session.commit(); flash('Producto publicado correctamente.','success'); return redirect(url_for('dashboard'))
  return render_template('publish.html')

 @app.route('/matches/<int:listing_id>')
 @login_required
 @approved_required
 def matches(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  if item.owner_id!=current_user.id: abort(403)
  return render_template('matches.html',listing=item,matches=matches_for(item))

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
   if body: db.session.add(Message(sender_id=current_user.id,receiver_id=other.id,body=body)); db.session.add(Notification(user_id=other.id,message=f'Nuevo mensaje de {current_user.full_name}.')); db.session.commit()
   return redirect(url_for('chat',user_id=user_id))
  msgs=Message.query.filter(or_(and_(Message.sender_id==current_user.id,Message.receiver_id==other.id),and_(Message.sender_id==other.id,Message.receiver_id==current_user.id))).order_by(Message.created_at.asc()).all(); return render_template('chat.html',other=other,msgs=msgs)

 @app.route('/admin/login',methods=['GET','POST'])
 def admin_login():
  if request.method=='POST':
   if not turnstile_valid(): abort(400, description='Verificación de seguridad fallida.')
   configured=current_app.config.get('ADMIN_PASSWORD','')
   if configured and hmac.compare_digest(request.form.get('password',''),configured):
    session['admin_ok']=True
    return redirect(url_for('admin'))
   flash('Credenciales incorrectas.','danger')
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
