from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from . import db


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)
    municipality = db.Column(db.String(80), nullable=False)
    address = db.Column(db.String(255), nullable=False)
    approved = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    approved_at = db.Column(db.DateTime)
    province = db.Column(db.String, nullable=False, server_default="Santiago de Cuba")
    is_admin = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def can_publish(self):
        return self.approved and self.role in {"vendedor", "admin_bodega"}

    @property
    def can_upload_photos(self):
        return self.approved and self.role == "admin_bodega"


class Listing(db.Model):
    __tablename__ = "listing"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    product = db.Column(db.String(120), nullable=False, index=True)
    custom_product = db.Column(db.String(120))
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(20), nullable=False)
    price = db.Column(db.Float, nullable=False)
    municipality = db.Column(db.String(80), nullable=False)
    coverage = db.Column(db.String(30), nullable=False)
    description = db.Column(db.Text)
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    province = db.Column(db.String, nullable=False, server_default="Santiago de Cuba")

    owner = db.relationship("User", backref="listings")
    items = db.relationship("ListingItem", back_populates="listing", cascade="all, delete-orphan")

    @property
    def display_product(self):
        return self.custom_product if self.product == "Otro" and self.custom_product else self.product


class ListingItem(db.Model):
    __tablename__ = "listing_item"

    id = db.Column(db.Integer, primary_key=True)
    listing_id = db.Column(db.Integer, db.ForeignKey("listing.id", ondelete="CASCADE"), nullable=False)
    product = db.Column(db.String, nullable=False)
    custom_product = db.Column(db.String)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String, nullable=False)
    price = db.Column(db.Float, nullable=False)
    active = db.Column(db.Boolean, nullable=False, server_default=db.text("true"))
    created_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, nullable=False, server_default=db.func.now())

    listing = db.relationship("Listing", back_populates="items")


class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    listing_id = db.Column(db.Integer)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    context_listing_a_id = db.Column(db.Integer)
    context_listing_b_id = db.Column(db.Integer)


class Message(db.Model):
    __tablename__ = "message"

    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False)
    receiver_id = db.Column(db.Integer, nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    read_at = db.Column(db.DateTime)
    context_listing_a_id = db.Column(db.Integer)
    context_listing_b_id = db.Column(db.Integer)
    hidden_by_sender = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))
    hidden_by_receiver = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))
    deleted_for_all = db.Column(db.Boolean, nullable=False, server_default=db.text("false"))


class Setting(db.Model):
    __tablename__ = "setting"

    id = db.Column(db.Integer, primary_key=True)
    commission = db.Column(db.Float, default=5.0)
