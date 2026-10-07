from functools import wraps
from datetime import datetime
import hmac, json, secrets, time, urllib.parse, urllib.request

from flask import render_template, request, redirect, url_for, flash, session, current_app, abort, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy import or_, and_

from . import db, MUNICIPIOS, PRODUCTOS, PROVINCIAS
from .models import User, Listing, Notification, Message, Setting

UNITS = {"lb","kg","unidad","saco","caja","L"}
COVERAGES = {"municipio","cercanos","provincia","ambas_provincias"}
ROLES = {"productor","vendedor","comprador"}
KINDS = {"oferta","demanda"}

_LOGIN_ATTEMPTS = {}
LOGIN_WINDOW_SECONDS = 600
LOGIN_MAX_ATTEMPTS = 7

def approved_required(fn):
    @wraps(fn)
    def wrapped(*a, **k):
        if not current_user.approved:
            return redirect(url_for("pending"))
        return fn(*a, **k)
    return wrapped

def admin_required(fn):
    @wraps(fn)
    def wrapped(*a, **k):
        if current_user.is_authenticated and getattr(current_user, "is_admin", False):
            return fn(*a, **k)
        if session.get("admin_ok"):
            return fn(*a, **k)
        return redirect(url_for("admin_login"))
    return wrapped

def product_name(x):
    return x.custom_product if x.product == "Otro" and x.custom_product else x.product

def norm(s):
    return " ".join((s or "").strip().lower().split())

def clean_text(v, n):
    return (v or "").strip()[:n]

def valid_location(province, municipality):
    return province in PROVINCIAS and municipality in PROVINCIAS[province]

def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token

def verify_csrf():
    expected = session.get("_csrf_token", "")
    supplied = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))

def client_ip():
    forwarded = request.headers.get("CF-Connecting-IP") or request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() if forwarded else request.remote_addr) or "unknown"

def login_rate_limited(key):
    now = time.time()
    attempts = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[key] = attempts
    return len(attempts) >= LOGIN_MAX_ATTEMPTS

def record_login_failure(key):
    _LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())

def clear_login_failures(key):
    _LOGIN_ATTEMPTS.pop(key, None)

def verify_turnstile():
    secret = current_app.config.get("TURNSTILE_SECRET_KEY", "")
    site_key = current_app.config.get("TURNSTILE_SITE_KEY", "")
    if not secret or not site_key:
        return True
    token = request.form.get("cf-turnstile-response", "")
    if not token:
        return False
    payload = urllib.parse.urlencode({
        "secret": secret,
        "response": token,
        "remoteip": client_ip(),
    }).encode()
    try:
        req = urllib.request.Request(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=payload,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
        return bool(result.get("success"))
    except Exception:
        return False

def matches_for(item):
    out = []
    for o in Listing.query.filter(
        Listing.id != item.id,
        Listing.active.is_(True),
        Listing.kind != item.kind,
    ).all():
        if not o.owner.approved or norm(product_name(o)) != norm(product_name(item)):
            continue

        offer = item if item.kind == "oferta" else o
        demand = o if item.kind == "oferta" else item

        if offer.province == demand.province and offer.municipality == demand.municipality:
            geo = 3
        elif offer.province == demand.province and (
            "provincia" in [offer.coverage, demand.coverage]
            or "ambas_provincias" in [offer.coverage, demand.coverage]
        ):
            geo = 2
        elif offer.province == demand.province and "cercanos" in [offer.coverage, demand.coverage]:
            geo = 1
        elif offer.province != demand.province and "ambas_provincias" in [offer.coverage, demand.coverage]:
            geo = 2
        else:
            geo = 0

        if geo == 0:
            continue

        qty = 2 if offer.quantity >= demand.quantity else 1 if offer.quantity >= demand.quantity * 0.5 else 0
        price = 3 if offer.price <= demand.price else 0
        score = geo + qty + price

        out.append((
            o,
            score,
            "Coincidencia excelente" if score >= 7 else
            "Buena coincidencia" if score >= 5 else
            "Coincidencia posible"
        ))
    return sorted(out, key=lambda x: x[1], reverse=True)

def context_pair_ids(a_id, b_id):
    if not a_id or not b_id:
        return (None, None)
    return tuple(sorted((int(a_id), int(b_id))))

def resolve_chat_context(other_user, a_id=None, b_id=None):
    a_id, b_id = context_pair_ids(a_id, b_id)
    if not a_id or not b_id:
        return None, None
    a = db.session.get(Listing, a_id)
    b = db.session.get(Listing, b_id)
    if not a or not b:
        abort(404)
    owners = {a.owner_id, b.owner_id}
    if owners != {current_user.id, other_user.id}:
        abort(403)
    return a, b

def message_context_filter(a_id, b_id):
    a_id, b_id = context_pair_ids(a_id, b_id)
    if a_id and b_id:
        return and_(
            Message.context_listing_a_id == a_id,
            Message.context_listing_b_id == b_id,
        )
    return and_(
        Message.context_listing_a_id.is_(None),
        Message.context_listing_b_id.is_(None),
    )

def register_routes(app):
    @app.context_processor
    def ctx():
        s = Setting.query.first()
        unread = unread_chat = 0
        if current_user.is_authenticated:
            unread = Notification.query.filter_by(user_id=current_user.id, read=False).count()
            unread_chat = Message.query.filter_by(receiver_id=current_user.id, read_at=None).count()
        return dict(
            MUNICIPIOS=MUNICIPIOS,
            PRODUCTOS=PRODUCTOS,
            PROVINCIAS=PROVINCIAS,
            OWNER_WHATSAPP=current_app.config["OWNER_WHATSAPP"],
            commission=s.commission if s else 0,
            unread=unread,
            unread_chat=unread_chat,
            csrf_token=csrf_token,
            turnstile_site_key=current_app.config.get("TURNSTILE_SITE_KEY", ""),
        )

    @app.before_request
    def csrf_protection():
        if request.method in {"POST","PUT","PATCH","DELETE"} and not verify_csrf():
            abort(400, "Solicitud inválida o expirada. Recarga la página e inténtalo otra vez.")

    @app.route("/status/unread")
    @login_required
    def unread_status():
        return jsonify({
            "notifications": Notification.query.filter_by(user_id=current_user.id, read=False).count(),
            "chats": Message.query.filter_by(receiver_id=current_user.id, read_at=None).count(),
        })

    @app.route("/")
    def index():
        q = clean_text(request.args.get("q", ""), 120)
        kind = request.args.get("kind", "")
        province = request.args.get("province", "")
        municipality = request.args.get("municipality", "")
        qry = Listing.query.join(User).filter(Listing.active.is_(True), User.approved.is_(True))
        if q:
            qry = qry.filter(or_(Listing.product.ilike(f"%{q}%"), Listing.custom_product.ilike(f"%{q}%")))
        if kind in KINDS:
            qry = qry.filter(Listing.kind == kind)
        if province in PROVINCIAS:
            qry = qry.filter(Listing.province == province)
            if municipality in PROVINCIAS[province]:
                qry = qry.filter(Listing.municipality == municipality)
        else:
            province = municipality = ""
        return render_template("index.html", listings=qry.order_by(Listing.created_at.desc()).all(), q=q, kind=kind, province=province, municipality=municipality)

    @app.route("/register", methods=["GET","POST"])
    def register():
        if request.method == "POST":
            if not verify_turnstile():
                flash("No pudimos verificar que eres una persona. Inténtalo de nuevo.", "danger")
                return redirect(url_for("register"))
            d = request.form
            full_name = clean_text(d.get("full_name"), 120)
            phone = clean_text(d.get("phone"), 30)
            password = d.get("password", "")
            role = d.get("role", "")
            province = d.get("province", "")
            municipality = d.get("municipality", "")
            address = clean_text(d.get("address"), 255)
            if len(full_name) < 3 or len(phone) < 6 or len(address) < 4:
                flash("Revisa nombre, teléfono y dirección.", "danger")
                return redirect(url_for("register"))
            if len(password) < 8:
                flash("La contraseña debe tener al menos 8 caracteres.", "danger")
                return redirect(url_for("register"))
            if role not in ROLES or not valid_location(province, municipality):
                flash("Rol, provincia o municipio no válido.", "danger")
                return redirect(url_for("register"))
            if User.query.filter_by(phone=phone).first():
                flash("Ese teléfono ya está registrado.", "danger")
                return redirect(url_for("register"))
            u = User(
                full_name=full_name,
                phone=phone,
                role=role,
                province=province,
                municipality=municipality,
                address=address,
                approved=False,
                is_admin=False,
            )
            u.set_password(password)
            db.session.add(u)
            db.session.commit()
            login_user(u)
            flash("Registro completado. Solicita tu activación por WhatsApp.", "success")
            return redirect(url_for("pending"))
        return render_template("register.html")

    @app.route("/pending")
    @login_required
    def pending():
        if current_user.approved:
            return redirect(url_for("dashboard"))
        text = f"Hola, soy {current_user.full_name}. Me registré en ConectaCuba con el teléfono {current_user.phone}, como {current_user.role}, en {current_user.municipality}, {current_user.province}. Quiero solicitar la activación de mi cuenta."
        return render_template("pending.html", text=text)

    @app.route("/login", methods=["GET","POST"])
    def login():
        if request.method == "POST":
            key = client_ip()
            if login_rate_limited(key):
                flash("Demasiados intentos. Espera unos minutos.", "danger")
                return redirect(url_for("login"))
            if not verify_turnstile():
                record_login_failure(key)
                flash("No pudimos verificar que eres una persona.", "danger")
                return redirect(url_for("login"))
            u = User.query.filter_by(phone=clean_text(request.form.get("phone"), 30)).first()
            if not u or not u.check_password(request.form.get("password", "")):
                record_login_failure(key)
                flash("Credenciales incorrectas.", "danger")
                return redirect(url_for("login"))
            clear_login_failures(key)
            login_user(u)
            session.permanent = True
            return redirect(url_for("pending" if not u.approved else "dashboard"))
        return render_template("login.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        session.clear()
        return redirect(url_for("index"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        if not current_user.approved:
            return redirect(url_for("pending"))
        ls = Listing.query.filter_by(owner_id=current_user.id).order_by(Listing.created_at.desc()).all()
        notes = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).limit(8).all()
        return render_template("dashboard.html", listings=ls, notes=notes)

    @app.route("/publish", methods=["GET","POST"])
    @login_required
    @approved_required
    def publish():
        if request.method == "POST":
            d = request.form
            kind = d.get("kind", "")
            product = d.get("product", "")
            custom_product = clean_text(d.get("custom_product"), 120)
            province = d.get("province", "")
            municipality = d.get("municipality", "")
            unit = d.get("unit", "")
            coverage = d.get("coverage", "")
            description = clean_text(d.get("description"), 1200)
            try:
                quantity = float(d.get("quantity", ""))
                price = float(d.get("price", ""))
            except (TypeError, ValueError):
                flash("Cantidad o precio no válidos.", "danger")
                return redirect(url_for("publish"))

            if kind not in KINDS or product not in PRODUCTOS or unit not in UNITS or coverage not in COVERAGES or not valid_location(province, municipality):
                flash("Hay datos no válidos en la publicación.", "danger")
                return redirect(url_for("publish"))
            if product == "Otro" and len(custom_product) < 2:
                flash("Especifica el producto cuando eliges “Otro”.", "danger")
                return redirect(url_for("publish"))
            if product != "Otro":
                custom_product = None
            if quantity <= 0 or price < 0:
                flash("Cantidad y precio deben ser válidos.", "danger")
                return redirect(url_for("publish"))

            item = Listing(
                kind=kind,
                product=product,
                custom_product=custom_product,
                quantity=quantity,
                unit=unit,
                price=price,
                province=province,
                municipality=municipality,
                coverage=coverage,
                description=description,
                owner_id=current_user.id,
            )
            db.session.add(item)
            db.session.commit()

            for other, score, level in matches_for(item)[:10]:
                a_id, b_id = context_pair_ids(item.id, other.id)

                db.session.add(Notification(
                    user_id=current_user.id,
                    listing_id=other.id,
                    context_listing_a_id=a_id,
                    context_listing_b_id=b_id,
                    message=f"{level} para tu {item.display_kind.lower()} de {product_name(item)}: {other.display_kind.lower()} en {other.municipality}, {other.province}.",
                ))

                db.session.add(Notification(
                    user_id=other.owner_id,
                    listing_id=item.id,
                    context_listing_a_id=a_id,
                    context_listing_b_id=b_id,
                    message=f"{level} para tu {other.display_kind.lower()} de {product_name(other)}: nueva {item.display_kind.lower()} en {item.municipality}, {item.province}.",
                ))

            db.session.commit()
            return redirect(url_for("matches", listing_id=item.id))
        return render_template("publish.html")

    @app.route("/matches/<int:listing_id>")
    @login_required
    @approved_required
    def matches(listing_id):
        item = db.session.get(Listing, listing_id)
        if not item or item.owner_id != current_user.id:
            abort(403)
        return render_template("matches.html", listing=item, matches=matches_for(item))

    @app.route("/listing/<int:listing_id>")
    def detail(listing_id):
        item = db.session.get(Listing, listing_id)
        if not item:
            abort(404)
        return render_template("detail.html", listing=item)

    @app.route("/notification/<int:notification_id>/open")
    @login_required
    @approved_required
    def open_notification(notification_id):
        n = db.session.get(Notification, notification_id)
        if not n or n.user_id != current_user.id:
            abort(404)
        n.read = True
        db.session.commit()

        if n.context_listing_a_id and n.context_listing_b_id:
            a = db.session.get(Listing, n.context_listing_a_id)
            b = db.session.get(Listing, n.context_listing_b_id)
            if a and b:
                owners = {a.owner_id, b.owner_id}
                if current_user.id in owners:
                    other_user_id = b.owner_id if a.owner_id == current_user.id else a.owner_id
                    return redirect(url_for(
                        "chat",
                        user_id=other_user_id,
                        a=n.context_listing_a_id,
                        b=n.context_listing_b_id,
                    ))

        if n.listing_id:
            return redirect(url_for("detail", listing_id=n.listing_id))

        return redirect(url_for("notifications"))

    @app.route("/notifications")
    @login_required
    def notifications():
        if not current_user.approved:
            return redirect(url_for("pending"))
        notes = Notification.query.filter_by(user_id=current_user.id).order_by(Notification.created_at.desc()).all()
        for n in notes:
            n.read = True
        db.session.commit()
        return render_template("notifications.html", notes=notes)

    @app.route("/chats")
    @login_required
    @approved_required
    def chats():
        msgs = Message.query.filter(or_(
            Message.sender_id == current_user.id,
            Message.receiver_id == current_user.id,
        )).order_by(Message.created_at.desc()).all()

        seen = set()
        conversations = []
        for m in msgs:
            partner_id = m.receiver_id if m.sender_id == current_user.id else m.sender_id
            context_key = (m.context_listing_a_id, m.context_listing_b_id)
            key = (partner_id, context_key)
            if key in seen:
                continue

            partner = db.session.get(User, partner_id)
            if not partner:
                continue

            unread_count = Message.query.filter_by(
                sender_id=partner_id,
                receiver_id=current_user.id,
                read_at=None,
                context_listing_a_id=m.context_listing_a_id,
                context_listing_b_id=m.context_listing_b_id,
            ).count()

            context_a = db.session.get(Listing, m.context_listing_a_id) if m.context_listing_a_id else None
            context_b = db.session.get(Listing, m.context_listing_b_id) if m.context_listing_b_id else None

            conversations.append({
                "user": partner,
                "last_message": m,
                "unread_count": unread_count,
                "context_a": context_a,
                "context_b": context_b,
            })
            seen.add(key)

        return render_template("chats.html", conversations=conversations)

    @app.route("/chat/<int:user_id>", methods=["GET","POST"])
    @login_required
    @approved_required
    def chat(user_id):
        other = db.session.get(User, user_id)
        if not other or not other.approved or other.id == current_user.id:
            abort(404)

        a_id = request.args.get("a", type=int)
        b_id = request.args.get("b", type=int)
        a_id, b_id = context_pair_ids(a_id, b_id)
        context_a, context_b = resolve_chat_context(other, a_id, b_id)

        if request.method == "POST":
            body = clean_text(request.form.get("body"), 2000)
            if body:
                msg = Message(
                    sender_id=current_user.id,
                    receiver_id=other.id,
                    body=body,
                    context_listing_a_id=a_id,
                    context_listing_b_id=b_id,
                )
                db.session.add(msg)

                if context_a and context_b:
                    product = product_name(context_a)
                    db.session.add(Notification(
                        user_id=other.id,
                        listing_id=context_a.id if context_a.owner_id == current_user.id else context_b.id,
                        context_listing_a_id=a_id,
                        context_listing_b_id=b_id,
                        message=f"Nuevo mensaje de {current_user.full_name} sobre la coincidencia de {product}.",
                    ))
                else:
                    db.session.add(Notification(
                        user_id=other.id,
                        message=f"Nuevo mensaje de {current_user.full_name}.",
                    ))

                db.session.commit()

                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"ok": True, "message": serialize_message(msg)})

            return redirect(url_for("chat", user_id=user_id, a=a_id, b=b_id))

        unread_messages = Message.query.filter(
            Message.sender_id == other.id,
            Message.receiver_id == current_user.id,
            Message.read_at.is_(None),
            message_context_filter(a_id, b_id),
        ).all()

        if unread_messages:
            now = datetime.utcnow()
            for m in unread_messages:
                m.read_at = now
            db.session.commit()

        msgs = Message.query.filter(
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == other.id),
                and_(Message.sender_id == other.id, Message.receiver_id == current_user.id),
            ),
            message_context_filter(a_id, b_id),
        ).order_by(Message.created_at.asc()).all()

        return render_template(
            "chat.html",
            other=other,
            msgs=msgs,
            context_a=context_a,
            context_b=context_b,
            context_a_id=a_id,
            context_b_id=b_id,
        )

    def serialize_message(m):
        return {
            "id": m.id,
            "body": m.body,
            "sender_id": m.sender_id,
            "receiver_id": m.receiver_id,
            "mine": m.sender_id == current_user.id,
            "created_at": m.created_at.strftime("%d/%m/%Y %H:%M"),
        }

    @app.route("/chat/<int:user_id>/messages")
    @login_required
    @approved_required
    def chat_messages(user_id):
        other = db.session.get(User, user_id)
        if not other or not other.approved or other.id == current_user.id:
            abort(404)

        a_id = request.args.get("a", type=int)
        b_id = request.args.get("b", type=int)
        a_id, b_id = context_pair_ids(a_id, b_id)
        resolve_chat_context(other, a_id, b_id)

        after = request.args.get("after", type=int) or 0

        msgs = Message.query.filter(
            Message.id > after,
            or_(
                and_(Message.sender_id == current_user.id, Message.receiver_id == other.id),
                and_(Message.sender_id == other.id, Message.receiver_id == current_user.id),
            ),
            message_context_filter(a_id, b_id),
        ).order_by(Message.id.asc()).all()

        incoming = [
            m for m in msgs
            if m.sender_id == other.id
            and m.receiver_id == current_user.id
            and m.read_at is None
        ]

        if incoming:
            now = datetime.utcnow()
            for m in incoming:
                m.read_at = now
            db.session.commit()

        return jsonify({"messages": [serialize_message(m) for m in msgs]})

    @app.route("/admin/login", methods=["GET","POST"])
    def admin_login():
        if current_user.is_authenticated and getattr(current_user, "is_admin", False):
            return redirect(url_for("admin"))

        if request.method == "POST":
            key = "admin:" + client_ip()
            if login_rate_limited(key):
                flash("Demasiados intentos. Espera unos minutos.", "danger")
                return redirect(url_for("admin_login"))
            if not verify_turnstile():
                record_login_failure(key)
                flash("No pudimos verificar que eres una persona.", "danger")
                return redirect(url_for("admin_login"))
            if request.form.get("password") == current_app.config["ADMIN_PASSWORD"]:
                clear_login_failures(key)
                session.clear()
                session["admin_ok"] = True
                csrf_token()
                return redirect(url_for("admin"))
            record_login_failure(key)
            flash("Contraseña incorrecta.", "danger")
        return render_template("admin_login.html")

    @app.route("/admin")
    @admin_required
    def admin():
        return render_template(
            "admin.html",
            users=User.query.order_by(User.created_at.desc()).all(),
            setting=Setting.query.first(),
        )

    @app.route("/admin/user/<int:user_id>/toggle", methods=["POST"])
    @admin_required
    def admin_toggle(user_id):
        u = db.session.get(User, user_id)
        if not u:
            abort(404)

        if u.is_admin:
            flash("No se puede desactivar una cuenta de administrador desde este botón.", "danger")
            return redirect(url_for("admin"))

        u.approved = not u.approved
        u.approved_at = datetime.utcnow() if u.approved else None

        if u.approved:
            db.session.add(Notification(
                user_id=u.id,
                message="Tu cuenta ha sido ACTIVADA por el administrador.",
            ))

        db.session.commit()
        return redirect(url_for("admin"))

    @app.route("/admin/commission", methods=["POST"])
    @admin_required
    def admin_commission():
        try:
            value = float(request.form["commission"])
        except (TypeError, ValueError, KeyError):
            abort(400)

        s = Setting.query.first()
        s.commission = max(0, min(100, value))
        db.session.commit()
        return redirect(url_for("admin"))
