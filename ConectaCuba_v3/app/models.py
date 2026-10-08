from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from . import db

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    province = db.Column(db.String(80), nullable=False, default="Santiago de Cuba")
    municipality = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    approved = db.Column(db.Boolean, default=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)

    def set_password(self, p):
        self.password_hash = generate_password_hash(p)

    def check_password(self, p):
        return check_password_hash(self.password_hash, p)


class Listing(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)

    # Campos heredados de la versión monoproducto.
    # Se mantienen para compatibilidad y se sincronizan con el primer producto.
    product = db.Column(db.String(120), nullable=False, index=True)
    custom_product = db.Column(db.String(120))
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    price = db.Column(db.Float, nullable=False)

    province = db.Column(db.String(80), nullable=False, default="Santiago de Cuba")
    municipality = db.Column(db.String(80), nullable=False)
    coverage = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    owner = db.relationship("User", backref="listings")

    items = db.relationship(
        "ListingItem",
        backref="listing",
        cascade="all, delete-orphan",
        lazy=True,
        order_by="ListingItem.id",
    )

    @property
    def display_kind(self):
        return "Venta" if self.kind == "oferta" else "Compra"

    @property
    def active_items(self):
        return [x for x in self.items if x.active and x.quantity > 0]

    @property
    def display_product(self):
        active = self.active_items
        if len(active) == 1:
            return active[0].display_product
        if len(active) > 1:
            return f"{len(active)} productos"
        return "Publicación cerrada"

    def sync_legacy_fields(self):
        candidates = self.active_items or list(self.items)
        if not candidates:
            return
        first = candidates[0]
        self.product = first.product
        self.custom_product = first.custom_product
        self.quantity = first.quantity
        self.unit = first.unit
        self.price = first.price


class ListingItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(
        db.Integer,
        db.ForeignKey("listing.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product = db.Column(db.String(120), nullable=False, index=True)
    custom_product = db.Column(db.String(120))
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    price = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def display_product(self):
        return self.custom_product if self.product == "Otro" and self.custom_product else self.product


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    listing_id = db.Column(db.Integer)
    context_listing_a_id = db.Column(db.Integer, nullable=True)
    context_listing_b_id = db.Column(db.Integer, nullable=True)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read_at = db.Column(db.DateTime, nullable=True)
    context_listing_a_id = db.Column(db.Integer, nullable=True)
    context_listing_b_id = db.Column(db.Integer, nullable=True)
    hidden_by_sender = db.Column(db.Boolean, nullable=False, default=False)
    hidden_by_receiver = db.Column(db.Boolean, nullable=False, default=False)
    deleted_for_all = db.Column(db.Boolean, nullable=False, default=False)


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    commission = db.Column(db.Float, default=5.0)
