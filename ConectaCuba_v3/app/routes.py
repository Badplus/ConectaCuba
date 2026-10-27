import os
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from urllib.error import HTTPError, URLError
import hmac
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import secrets
import hashlib
from datetime import timedelta
from urllib.parse import quote
from functools import wraps
from datetime import datetime
from flask import render_template,request,redirect,url_for,flash,session,current_app,abort
from flask_login import login_user,logout_user,login_required,current_user
from sqlalchemy import or_,and_
from . import db,MUNICIPIOS,PRODUCTOS
from .models import User,Listing,Notification,Message,Setting,PasswordResetRequest,PushSubscription
from .push import send_push,push_configured,valid_subscription

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

def can_manage_photo(listing):
 return (current_user.is_authenticated and
         (current_user.is_admin or (current_user.approved and current_user.role == 'admin_bodega' and listing.owner_id == current_user.id)))

def product_photo_url(path):
 base=os.getenv('SUPABASE_URL','').rstrip('/')
 return base + '/storage/v1/object/public/fotos-productos/' + quote(path, safe='/') if base and path else ''

def storage_request(method, path, payload=None, content_type=None):
 base=os.getenv('SUPABASE_URL','').rstrip('/')
 key=os.getenv('SUPABASE_SERVICE_ROLE_KEY','')
 if not base or not key:
  raise RuntimeError('Falta configurar SUPABASE_URL o SUPABASE_SERVICE_ROLE_KEY en Render.')
 url=base+'/storage/v1/object/fotos-productos/'+quote(path,safe='/')
 headers={'apikey':key,'Authorization':'Bearer '+key}
 if content_type: headers['Content-Type']=content_type
 req=Request(url,data=payload,headers=headers,method=method)
 with urlopen(req,timeout=20) as response:
  return response.status

def register_routes(app):
 @app.context_processor
 def ctx():
  s=Setting.query.first(); unread=0
  if current_user.is_authenticated: unread=Notification.query.filter_by(user_id=current_user.id,read=False).count()
  return dict(can_manage_photo=can_manage_photo,product_photo_url=product_photo_url,MUNICIPIOS=MUNICIPIOS,PRODUCTOS=PRODUCTOS,OWNER_WHATSAPP=current_app.config['OWNER_WHATSAPP'],commission=s.commission if s else 0,unread=unread)

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

 @app.route('/account/password', methods=['GET','POST'])
 @login_required
 def change_password():
  if request.method=='POST':
   old=request.form.get('current_password','')
   new=request.form.get('new_password','')
   if not current_user.check_password(old):
    flash('Contraseña actual incorrecta.','danger')
   elif len(new)<12 or len(new)>128:
    flash('La contraseña nueva debe tener entre 12 y 128 caracteres.','danger')
   else:
    current_user.set_password(new)
    db.session.commit()
    flash('Contraseña actualizada.','success')
    return redirect(url_for('index'))
  return render_template('change_password.html')


 def pin_digest(reset_id, pin):
  secret=current_app.secret_key.encode('utf-8')
  return hmac.new(secret,f'{reset_id}:{pin}'.encode('utf-8'),hashlib.sha256).hexdigest()

 @app.route('/forgot-password',methods=['GET','POST'])
 def forgot_password():
  if request.method=='POST':
   phone=request.form.get('phone','').strip()
   user=User.query.filter_by(phone=phone).first()
   if user:
    now=datetime.utcnow()
    recent=PasswordResetRequest.query.filter_by(user_id=user.id).filter(
     PasswordResetRequest.requested_at>now-timedelta(minutes=15)).first()
    if not recent:
     db.session.add(PasswordResetRequest(user_id=user.id))
     db.session.commit()
   flash('Solicitud recibida. Si el teléfono está registrado, el administrador podrá revisarla. Solicita el PIN al administrador por tu medio habitual.','success')
   return redirect(url_for('forgot_password'))
  return render_template('forgot_password.html')

 @app.route('/admin/password-resets')
 @admin_required
 def admin_password_resets():
  requests=PasswordResetRequest.query.filter_by(approved_at=None,used_at=None).filter(
   PasswordResetRequest.requested_at>=datetime.utcnow()-timedelta(days=1)).order_by(
   PasswordResetRequest.requested_at.desc()).limit(100).all()
  return render_template('admin_password_resets.html',requests=requests)

 @app.route('/admin/password-resets/<int:reset_id>/approve',methods=['POST'])
 @admin_required
 def approve_password_reset(reset_id):
  record=db.session.get(PasswordResetRequest,reset_id)
  if not record or record.approved_at or record.used_at:
   abort(404)
  if record.requested_at<datetime.utcnow()-timedelta(days=1):
   abort(400,description='La solicitud ha caducado.')
  # El PIN se muestra una sola vez al administrador y nunca se almacena en claro.
  pin=f'{secrets.randbelow(100000):05d}'
  record.token_hash=pin_digest(record.id,pin)
  record.approved_at=datetime.utcnow()
  record.expires_at=datetime.utcnow()+timedelta(minutes=10)
  record.pin_attempts=0
  db.session.commit()
  response=current_app.make_response(render_template('admin_reset_link.html',pin=pin,phone=record.user.phone))
  response.headers['Cache-Control']='no-store'
  response.headers['Referrer-Policy']='no-referrer'
  return response

 @app.route('/verify-recovery-pin',methods=['GET','POST'])
 def verify_recovery_pin():
  if request.method=='POST':
   phone=request.form.get('phone','').strip()
   pin=request.form.get('pin','').strip()
   user=User.query.filter_by(phone=phone).first()
   record=(PasswordResetRequest.query.filter_by(user_id=user.id).filter(
    PasswordResetRequest.approved_at.isnot(None),PasswordResetRequest.used_at.is_(None)
   ).order_by(PasswordResetRequest.approved_at.desc()).first() if user else None)
   if not record or not record.token_hash or not record.expires_at or record.expires_at<=datetime.utcnow() or record.pin_attempts>=5:
    flash('PIN incorrecto, caducado o sin autorización. Solicita uno nuevo al administrador.','danger')
    return redirect(url_for('verify_recovery_pin'))
   if len(pin)!=5 or not pin.isascii() or not pin.isdigit() or not hmac.compare_digest(record.token_hash,pin_digest(record.id,pin)):
    record.pin_attempts+=1
    if record.pin_attempts>=5:
     record.used_at=datetime.utcnow()
     record.token_hash=None
    db.session.commit()
    flash('PIN incorrecto o caducado.','danger')
    return redirect(url_for('verify_recovery_pin'))
   session['recovery_reset_id']=record.id
   return redirect(url_for('reset_password_with_pin'))
  response=current_app.make_response(render_template('verify_recovery_pin.html'))
  response.headers['Cache-Control']='no-store'
  return response

 @app.route('/new-password',methods=['GET','POST'])
 def reset_password_with_pin():
  reset_id=session.get('recovery_reset_id')
  record=db.session.get(PasswordResetRequest,reset_id) if isinstance(reset_id,int) else None
  if (not record or not record.token_hash or not record.approved_at or record.used_at
      or not record.expires_at or record.expires_at<=datetime.utcnow() or record.pin_attempts>=5):
   session.pop('recovery_reset_id',None)
   flash('La autorización ha caducado. Solicita otro PIN.','danger')
   return redirect(url_for('verify_recovery_pin'))
  if request.method=='POST':
   password=request.form.get('new_password','')
   confirm=request.form.get('confirm_password','')
   if password!=confirm:
    flash('Las contraseñas no coinciden.','danger')
   elif len(password)<12 or len(password)>128:
    flash('La contraseña debe tener entre 12 y 128 caracteres.','danger')
   else:
    record.user.set_password(password)
    record.used_at=datetime.utcnow()
    record.token_hash=None
    db.session.commit()
    session.clear()
    flash('Contraseña actualizada. Ya puedes entrar.','success')
    return redirect(url_for('login'))
  response=current_app.make_response(render_template('reset_password.html'))
  response.headers['Cache-Control']='no-store'
  return response

 @app.route('/reset-password/<int:reset_id>/<token>',methods=['GET','POST'])
 def reset_password(reset_id,token):
  # Los enlaces antiguos dejan de estar disponibles al pasar a PIN.
  abort(410,description='La recuperación ahora se realiza mediante PIN.')

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
 def logout():
  session.pop('admin_ok',None)
  logout_user()
  return redirect(url_for('index'))

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
   db.session.commit()
   for other,score,level in matches_for(item)[:10]:
    send_push(other.owner_id,'Nueva coincidencia en Mercado BETO Plus',f'{level}: {product_name(item)}',url_for('detail',listing_id=item.id))
   flash('Producto publicado correctamente.','success'); return redirect(url_for('dashboard'))
  return render_template('publish.html')

 def can_edit_listing(item):
  return current_user.is_authenticated and (current_user.is_admin or (current_user.approved and current_user.role in SELLER_ROLES and item.owner_id==current_user.id))

 @app.route('/listing/<int:listing_id>/edit',methods=['GET','POST'])
 @login_required
 def edit_listing(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  if not can_edit_listing(item): abort(403)
  if request.method=='POST':
   d=request.form
   if d.get('product') not in PRODUCTOS or d.get('municipality') not in MUNICIPIOS or d.get('unit') not in ('lb','kg','unidad','saco','caja','L') or d.get('coverage') not in ('municipio','cercanos','provincia'):
    abort(400,description='Datos de la publicación no válidos.')
   try:
    quantity=float(d.get('quantity',''))
    price=float(d.get('price',''))
   except (ValueError,TypeError): abort(400,description='Cantidad o precio no válido.')
   import math
   if not (math.isfinite(quantity) and math.isfinite(price) and 0<quantity<1e12 and 0<=price<1e12):
    abort(400,description='Cantidad o precio fuera de rango.')
   custom=(d.get('custom_product') or '').strip()
   if d['product']=='Otro' and (not custom or len(custom)>120):
    abort(400,description='Especifica el nombre del producto.')
   description=(d.get('description') or '').strip()
   if len(description)>2000: abort(400)
   item.product=d['product']; item.custom_product=custom if d['product']=='Otro' else None
   item.quantity=quantity; item.price=price; item.unit=d['unit']; item.municipality=d['municipality']; item.coverage=d['coverage']; item.description=description
   db.session.commit()
   flash('Publicación actualizada correctamente.','success')
   return redirect(url_for('admin_products_photos') if current_user.is_admin else url_for('dashboard'))
  return render_template('edit_listing.html',listing=item)

 @app.route('/listing/<int:listing_id>/moderate',methods=['POST'])
 @login_required
 def moderate_listing(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  if not can_edit_listing(item): abort(403)
  action=request.form.get('action')
  if action not in ('deactivate','reactivate'): abort(400)
  item.active=(action=='reactivate')
  db.session.commit()
  flash('Publicación reactivada.' if item.active else 'Publicación retirada del mercado.','success')
  return redirect(url_for('admin_products_photos') if current_user.is_admin else url_for('dashboard'))

 @app.route('/matches/<int:listing_id>')
 @login_required
 @approved_required
 def matches(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  if item.owner_id!=current_user.id: abort(403)
  return render_template('matches.html',listing=item,matches=matches_for(item))

 @app.route('/admin/product-photos')
 @admin_required
 def admin_products_photos():
  products=Listing.query.order_by(Listing.created_at.desc()).all()
  return render_template('admin_product_photos.html',products=products)

 @app.route('/listing/<int:listing_id>/photo',methods=['GET','POST'])
 @login_required
 def upload_product_photo(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  if not can_manage_photo(item): abort(403)
  if request.method=='POST':
   uploaded=request.files.get('photo')
   if not uploaded or not uploaded.filename:
    flash('Selecciona una fotografía.','danger')
    return redirect(url_for('upload_product_photo',listing_id=item.id))
   content=uploaded.stream.read(3*1024*1024+1)
   if len(content)>3*1024*1024 or not content:
    flash('La imagen debe ocupar como máximo 3 MB.','danger')
    return redirect(url_for('upload_product_photo',listing_id=item.id))
   try:
    with Image.open(BytesIO(content)) as img:
     img.verify()
    with Image.open(BytesIO(content)) as img:
     fmt=img.format
     if img.width>6000 or img.height>6000 or img.width*img.height>20000000:
      raise ValueError('Imagen demasiado grande')
   except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
    flash('Fotografía inválida o demasiado grande.','danger')
    return redirect(url_for('upload_product_photo',listing_id=item.id))
   types={'JPEG':('jpg','image/jpeg'),'PNG':('png','image/png'),'WEBP':('webp','image/webp')}
   if fmt not in types:
    flash('Solo se permiten imágenes JPG, PNG o WebP.','danger')
    return redirect(url_for('upload_product_photo',listing_id=item.id))
   ext,mime=types[fmt]
   new_path=f'producto-{item.id}/{secrets.token_hex(16)}.{ext}'
   try:
    storage_request('POST',new_path,content,mime)
   except (HTTPError,URLError,RuntimeError,TimeoutError) as exc:
    current_app.logger.warning('Fallo al subir foto de producto %s: %s',item.id,type(exc).__name__)
    flash('No se pudo guardar la fotografía. Revisa la configuración de almacenamiento.','danger')
    return redirect(url_for('upload_product_photo',listing_id=item.id))
   old_path=item.photo_path
   item.photo_path=new_path
   try:
    db.session.commit()
   except Exception:
    db.session.rollback()
    try: storage_request('DELETE',new_path)
    except Exception: pass
    raise
   if old_path:
    try: storage_request('DELETE',old_path)
    except Exception: current_app.logger.warning('No se pudo eliminar la foto anterior del producto %s',item.id)
   flash('Fotografía guardada correctamente.','success')
   return redirect(url_for('detail',listing_id=item.id))
  return render_template('upload_product_photo.html',listing=item)

 @app.route('/listing/<int:listing_id>')
 def detail(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item or not item.active or not item.owner.approved: abort(404)
  return render_template('detail.html',listing=item)

 @app.route('/sw.js')
 def service_worker():
  response=current_app.send_static_file('sw.js')
  response.headers['Cache-Control']='no-cache'
  response.headers['Service-Worker-Allowed']='/'
  return response

 @app.route('/api/push/public-key')
 @login_required
 def push_public_key():
  return current_app.response_class(response=json.dumps({'publicKey':os.getenv('VAPID_PUBLIC_KEY','')}),mimetype='application/json')

 @app.route('/api/push/subscribe',methods=['POST'])
 @login_required
 @approved_required
 def push_subscribe():
  if not push_configured(): abort(503)
  data=request.get_json(silent=True)
  if not valid_subscription(data): abort(400)
  endpoint=data['endpoint']
  existing=PushSubscription.query.filter_by(endpoint=endpoint).first()
  if existing and existing.user_id!=current_user.id: abort(409)
  if not existing:
   existing=PushSubscription(user_id=current_user.id,endpoint=endpoint)
   db.session.add(existing)
  existing.p256dh=data['keys']['p256dh']
  existing.auth=data['keys']['auth']
  db.session.commit()
  return current_app.response_class(response=json.dumps({'ok':True}),mimetype='application/json')

 @app.route('/api/push/unsubscribe',methods=['POST'])
 @login_required
 def push_unsubscribe():
  data=request.get_json(silent=True) or {}
  endpoint=data.get('endpoint')
  if not isinstance(endpoint,str): abort(400)
  PushSubscription.query.filter_by(user_id=current_user.id,endpoint=endpoint).delete()
  db.session.commit()
  return current_app.response_class(response=json.dumps({'ok':True}),mimetype='application/json')

 @app.route('/api/notifications/status')
 @login_required
 def notifications_status():
  unread=Notification.query.filter_by(user_id=current_user.id,read=False).count()
  response=current_app.response_class(response=json.dumps({'unread':unread}),mimetype='application/json')
  response.headers['Cache-Control']='no-store'
  return response

 @app.route('/api/notifications/list')
 @login_required
 def notifications_list_api():
  notes=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(100).all()
  payload=[{'id':n.id,'message':n.message,'date':n.created_at.strftime('%d/%m/%Y %H:%M') if n.created_at else '', 'context':bool(n.context_listing_a_id and n.context_listing_b_id),'listing':bool(n.listing_id),'chat':n.message.startswith('Nuevo mensaje de ')} for n in notes]
  response=current_app.response_class(response=json.dumps({'notifications':payload}),mimetype='application/json')
  response.headers['Cache-Control']='no-store'
  return response

 @app.route('/api/chat/<int:user_id>/messages')
 @login_required
 @approved_required
 def chat_messages_api(user_id):
  other=db.session.get(User,user_id)
  if not other or not other.approved or other.id==current_user.id: abort(404)
  msgs=Message.query.filter(or_(and_(Message.sender_id==current_user.id,Message.receiver_id==other.id),and_(Message.sender_id==other.id,Message.receiver_id==current_user.id))).order_by(Message.created_at.asc()).all()
  payload=[{'id':m.id,'mine':m.sender_id==current_user.id,'body':m.body,'date':m.created_at.strftime('%d/%m/%Y %H:%M') if m.created_at else ''} for m in msgs]
  response=current_app.response_class(response=json.dumps({'messages':payload}),mimetype='application/json')
  response.headers['Cache-Control']='no-store'
  return response

 @app.route('/notifications')
 @login_required
 def notifications():
  if not current_user.approved: return redirect(url_for('pending'))
  notes=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
  for n in notes: n.read=True
  db.session.commit(); return render_template('notifications.html',notes=notes)

 @app.route('/notifications/<int:notification_id>/open')
 @login_required
 def open_notification(notification_id):
  n=db.session.get(Notification,notification_id)
  if not n or n.user_id!=current_user.id: abort(404)
  n.read=True
  db.session.commit()
  if n.message.startswith('Nuevo mensaje de '):
   return redirect(url_for('messages_inbox'))
  if n.listing_id:
   item=db.session.get(Listing,n.listing_id)
   if item and item.active and item.owner.approved:
    return redirect(url_for('detail',listing_id=item.id))
  flash('La publicación relacionada ya no está disponible.' if n.listing_id else 'Notificación leída.','info')
  return redirect(url_for('notifications'))

 @app.route('/messages')
 @login_required
 @approved_required
 def messages_inbox():
  sent=db.session.query(Message.receiver_id).filter(Message.sender_id==current_user.id)
  received=db.session.query(Message.sender_id).filter(Message.receiver_id==current_user.id)
  user_ids={row[0] for row in sent.union(received).all()}
  contacts=User.query.filter(User.id.in_(user_ids),User.approved==True).order_by(User.full_name).all() if user_ids else []
  return render_template('messages_inbox.html',contacts=contacts)

 @app.route('/chat/<int:user_id>',methods=['GET','POST'])
 @login_required
 @approved_required
 def chat(user_id):
  other=db.session.get(User,user_id)
  if not other or not other.approved or other.id==current_user.id: abort(404)
  if request.method=='POST':
   body=request.form.get('body','').strip()
   if body:
    db.session.add(Message(sender_id=current_user.id,receiver_id=other.id,body=body))
    db.session.add(Notification(user_id=other.id,message=f'Nuevo mensaje de {current_user.full_name}.'))
    db.session.commit()
    send_push(other.id,'Nuevo mensaje en Mercado BETO Plus',f'{current_user.full_name} te ha escrito.',url_for('chat',user_id=current_user.id))
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
  db.session.commit()
  if u.approved: send_push(u.id,'Cuenta activada','Tu cuenta de Mercado BETO Plus ha sido activada.',url_for('dashboard'))
  return redirect(url_for('admin'))

 @app.route('/admin/commission',methods=['POST'])
 @admin_required
 def admin_commission():
  s=Setting.query.first()
  if not s: abort(404)
  try: value=float(request.form['commission'])
  except (ValueError, KeyError): abort(400)
  if not (0 <= value <= 100): abort(400)
  s.commission=value; db.session.commit(); return redirect(url_for('admin'))
