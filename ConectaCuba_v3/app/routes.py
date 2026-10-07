from functools import wraps
from datetime import datetime
from flask import render_template,request,redirect,url_for,flash,session,current_app,abort,jsonify
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

def admin_required(fn):
 @wraps(fn)
 def w(*a,**k):
  if not session.get('admin_ok'): return redirect(url_for('admin_login'))
  return fn(*a,**k)
 return w

def product_name(x): return x.custom_product if x.product=='Otro' and x.custom_product else x.product
def norm(s): return ' '.join((s or '').strip().lower().split())

def matches_for(item):
 out=[]
 for o in Listing.query.filter(Listing.id!=item.id,Listing.active.is_(True),Listing.kind!=item.kind).all():
  if not o.owner.approved or norm(product_name(o))!=norm(product_name(item)): continue
  offer=item if item.kind=='oferta' else o
  demand=o if item.kind=='oferta' else item
  geo=3 if offer.municipality==demand.municipality else (2 if 'provincia' in [offer.coverage,demand.coverage] else 1 if 'cercanos' in [offer.coverage,demand.coverage] else 0)
  if geo==0: continue
  qty=2 if offer.quantity>=demand.quantity else 1 if offer.quantity>=demand.quantity*.5 else 0
  price=3 if offer.price<=demand.price else 0
  score=geo+qty+price
  out.append((o,score,'Coincidencia excelente' if score>=7 else 'Buena coincidencia' if score>=5 else 'Coincidencia posible'))
 return sorted(out,key=lambda x:x[1],reverse=True)

def register_routes(app):
 @app.context_processor
 def ctx():
  s=Setting.query.first()
  unread=0
  unread_chat=0
  if current_user.is_authenticated:
   unread=Notification.query.filter_by(user_id=current_user.id,read=False).count()
   unread_chat=Message.query.filter_by(receiver_id=current_user.id,read_at=None).count()
  return dict(MUNICIPIOS=MUNICIPIOS,PRODUCTOS=PRODUCTOS,OWNER_WHATSAPP=current_app.config['OWNER_WHATSAPP'],commission=s.commission if s else 0,unread=unread,unread_chat=unread_chat)

 @app.route('/status/unread')
 @login_required
 def unread_status():
  return jsonify({
   'notifications': Notification.query.filter_by(user_id=current_user.id,read=False).count(),
   'chats': Message.query.filter_by(receiver_id=current_user.id,read_at=None).count()
  })

 @app.route('/')
 def index():
  q=request.args.get('q','').strip(); kind=request.args.get('kind',''); muni=request.args.get('municipality','')
  qry=Listing.query.join(User).filter(Listing.active.is_(True),User.approved.is_(True))
  if q: qry=qry.filter(or_(Listing.product.ilike(f'%{q}%'),Listing.custom_product.ilike(f'%{q}%')))
  if kind in ['oferta','demanda']: qry=qry.filter(Listing.kind==kind)
  if muni in MUNICIPIOS: qry=qry.filter(Listing.municipality==muni)
  return render_template('index.html',listings=qry.order_by(Listing.created_at.desc()).all(),q=q,kind=kind,muni=muni)

 @app.route('/register',methods=['GET','POST'])
 def register():
  if request.method=='POST':
   d=request.form; phone=d.get('phone','').strip()
   if User.query.filter_by(phone=phone).first():
    flash('Ese teléfono ya está registrado.','danger'); return redirect(url_for('register'))
   u=User(full_name=d['full_name'].strip(),phone=phone,role=d['role'],municipality=d['municipality'],address=d['address'].strip(),approved=False)
   u.set_password(d['password']); db.session.add(u); db.session.commit(); login_user(u)
   flash('Registro completado. Solicita tu activación por WhatsApp.','success'); return redirect(url_for('pending'))
  return render_template('register.html')

 @app.route('/pending')
 @login_required
 def pending():
  if current_user.approved: return redirect(url_for('dashboard'))
  text=f'Hola, soy {current_user.full_name}. Me registré en ConectaCuba con el teléfono {current_user.phone}, como {current_user.role}, municipio {current_user.municipality}. Quiero solicitar la activación de mi cuenta.'
  return render_template('pending.html',text=text)

 @app.route('/login',methods=['GET','POST'])
 def login():
  if request.method=='POST':
   u=User.query.filter_by(phone=request.form['phone'].strip()).first()
   if not u or not u.check_password(request.form['password']):
    flash('Credenciales incorrectas.','danger'); return redirect(url_for('login'))
   login_user(u)
   if not u.approved: return redirect(url_for('pending'))
   return redirect(url_for('dashboard'))
  return render_template('login.html')

 @app.route('/logout')
 @login_required
 def logout(): logout_user(); return redirect(url_for('index'))

 @app.route('/dashboard')
 @login_required
 def dashboard():
  if not current_user.approved: return redirect(url_for('pending'))
  ls=Listing.query.filter_by(owner_id=current_user.id).order_by(Listing.created_at.desc()).all()
  notes=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(8).all()
  return render_template('dashboard.html',listings=ls,notes=notes)

 @app.route('/publish',methods=['GET','POST'])
 @login_required
 @approved_required
 def publish():
  if request.method=='POST':
   d=request.form
   item=Listing(kind=d['kind'],product=d['product'],custom_product=d.get('custom_product') or None,quantity=float(d['quantity']),unit=d['unit'],price=float(d['price']),municipality=d['municipality'],coverage=d['coverage'],description=d.get('description',''),owner_id=current_user.id)
   db.session.add(item); db.session.commit()
   for other,score,level in matches_for(item)[:10]:
    db.session.add(Notification(user_id=current_user.id,listing_id=other.id,message=f'{level}: {product_name(other)} en {other.municipality}.'))
    db.session.add(Notification(user_id=other.owner_id,listing_id=item.id,message=f'{level}: nueva coincidencia de {product_name(item)} en {item.municipality}.'))
   db.session.commit(); return redirect(url_for('matches',listing_id=item.id))
  return render_template('publish.html')

 @app.route('/matches/<int:listing_id>')
 @login_required
 @approved_required
 def matches(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item or item.owner_id!=current_user.id: abort(403)
  return render_template('matches.html',listing=item,matches=matches_for(item))

 @app.route('/listing/<int:listing_id>')
 def detail(listing_id):
  item=db.session.get(Listing,listing_id)
  if not item: abort(404)
  return render_template('detail.html',listing=item)

 @app.route('/notifications')
 @login_required
 def notifications():
  if not current_user.approved: return redirect(url_for('pending'))
  notes=Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
  for n in notes: n.read=True
  db.session.commit(); return render_template('notifications.html',notes=notes)

 @app.route('/chats')
 @login_required
 @approved_required
 def chats():
  msgs=Message.query.filter(or_(Message.sender_id==current_user.id,Message.receiver_id==current_user.id)).order_by(Message.created_at.desc()).all()
  seen=set(); conversations=[]
  for m in msgs:
   partner_id=m.receiver_id if m.sender_id==current_user.id else m.sender_id
   if partner_id in seen: continue
   partner=db.session.get(User,partner_id)
   if partner:
    unread_count=Message.query.filter_by(sender_id=partner_id,receiver_id=current_user.id,read_at=None).count()
    conversations.append({'user':partner,'last_message':m,'unread_count':unread_count})
    seen.add(partner_id)
  return render_template('chats.html',conversations=conversations)

 @app.route('/chat/<int:user_id>',methods=['GET','POST'])
 @login_required
 @approved_required
 def chat(user_id):
  other=db.session.get(User,user_id)
  if not other or not other.approved or other.id==current_user.id: abort(404)
  if request.method=='POST':
   body=request.form.get('body','').strip()
   if body:
    msg=Message(sender_id=current_user.id,receiver_id=other.id,body=body)
    db.session.add(msg)
    db.session.add(Notification(user_id=other.id,message=f'Nuevo mensaje de {current_user.full_name}.'))
    db.session.commit()
    if request.headers.get('X-Requested-With')=='XMLHttpRequest':
     return jsonify({'ok':True,'message':serialize_message(msg)})
   return redirect(url_for('chat',user_id=user_id))

  unread_messages=Message.query.filter_by(sender_id=other.id,receiver_id=current_user.id,read_at=None).all()
  if unread_messages:
   now=datetime.utcnow()
   for m in unread_messages: m.read_at=now
   db.session.commit()

  msgs=Message.query.filter(or_(and_(Message.sender_id==current_user.id,Message.receiver_id==other.id),and_(Message.sender_id==other.id,Message.receiver_id==current_user.id))).order_by(Message.created_at.asc()).all()
  return render_template('chat.html',other=other,msgs=msgs)

 def serialize_message(m):
  return {
   'id':m.id,
   'body':m.body,
   'sender_id':m.sender_id,
   'receiver_id':m.receiver_id,
   'mine':m.sender_id==current_user.id,
   'created_at':m.created_at.strftime('%d/%m/%Y %H:%M')
  }

 @app.route('/chat/<int:user_id>/messages')
 @login_required
 @approved_required
 def chat_messages(user_id):
  other=db.session.get(User,user_id)
  if not other or not other.approved or other.id==current_user.id: abort(404)
  after=request.args.get('after',type=int) or 0
  msgs=Message.query.filter(
   Message.id>after,
   or_(
    and_(Message.sender_id==current_user.id,Message.receiver_id==other.id),
    and_(Message.sender_id==other.id,Message.receiver_id==current_user.id)
   )
  ).order_by(Message.id.asc()).all()

  incoming_unread=[m for m in msgs if m.sender_id==other.id and m.receiver_id==current_user.id and m.read_at is None]
  if incoming_unread:
   now=datetime.utcnow()
   for m in incoming_unread: m.read_at=now
   db.session.commit()

  return jsonify({'messages':[serialize_message(m) for m in msgs]})

 @app.route('/admin/login',methods=['GET','POST'])
 def admin_login():
  if request.method=='POST' and request.form.get('password')==current_app.config['ADMIN_PASSWORD']:
   session['admin_ok']=True; return redirect(url_for('admin'))
  return render_template('admin_login.html')

 @app.route('/admin')
 @admin_required
 def admin():
  return render_template('admin.html',users=User.query.order_by(User.created_at.desc()).all(),setting=Setting.query.first())

 @app.route('/admin/user/<int:user_id>/toggle',methods=['POST'])
 @admin_required
 def admin_toggle(user_id):
  u=db.session.get(User,user_id); u.approved=not u.approved; u.approved_at=datetime.utcnow() if u.approved else None
  if u.approved: db.session.add(Notification(user_id=u.id,message='Tu cuenta ha sido ACTIVADA por el administrador.'))
  db.session.commit(); return redirect(url_for('admin'))

 @app.route('/admin/commission',methods=['POST'])
 @admin_required
 def admin_commission():
  s=Setting.query.first(); s.commission=max(0,min(100,float(request.form['commission']))); db.session.commit(); return redirect(url_for('admin'))
