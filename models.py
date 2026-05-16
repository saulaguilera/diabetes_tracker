from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class GlucoseReading(db.Model):
    __tablename__ = "glucose_readings"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    value_mgdl = db.Column(db.Float, nullable=False)
    # manual, cgm_historic, cgm_scan, cgm_strip
    source = db.Column(db.String(20), nullable=False, default="manual")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Glucosa {self.value_mgdl} mg/dL @ {self.timestamp}>"

    @property
    def estado(self):
        if self.value_mgdl < 70:
            return "hipoglucemia"
        elif self.value_mgdl <= 180:
            return "en_rango"
        else:
            return "hiperglucemia"


class Meal(db.Model):
    __tablename__ = "meals"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    name = db.Column(db.String(200), nullable=False)
    carbs_g = db.Column(db.Float, nullable=False, default=0)
    fat_g = db.Column(db.Float, default=0)
    protein_g = db.Column(db.Float, default=0)
    calories = db.Column(db.Float, default=0)
    notes = db.Column(db.Text)
    categoria = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    components = db.relationship(
        "MealComponent", backref="meal", lazy=True,
        cascade="all, delete-orphan", order_by="MealComponent.id"
    )

    def __repr__(self):
        return f"<Comida {self.name} {self.carbs_g}g CH>"


class MealComponent(db.Model):
    """Ingrediente individual dentro de una comida."""
    __tablename__ = "meal_components"

    id           = db.Column(db.Integer, primary_key=True)
    meal_id      = db.Column(db.Integer, db.ForeignKey("meals.id", ondelete="CASCADE"), nullable=False)
    name         = db.Column(db.String(200), nullable=False)
    food_item_id = db.Column(db.Integer, db.ForeignKey("food_items.id"), nullable=True)
    carbs_g         = db.Column(db.Float, default=0)
    protein_g       = db.Column(db.Float, default=0)
    fat_g           = db.Column(db.Float, default=0)
    calories        = db.Column(db.Float, default=0)
    fiber_g         = db.Column(db.Float, default=0)      # fibra dietética (g)
    glycemic_index  = db.Column(db.Integer)               # ÍG 0-100 (nullable)
    grams           = db.Column(db.Float)   # porción en gramos (opcional)

    def __repr__(self):
        return f"<Componente {self.name} {self.carbs_g}g CH>"


class InsulinDose(db.Model):
    __tablename__ = "insulin_doses"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    # bolus, basal
    type = db.Column(db.String(20), nullable=False)
    units = db.Column(db.Float, nullable=False)
    brand = db.Column(db.String(100))
    notes = db.Column(db.Text)
    # purpose: 'comida' | 'correccion' | 'mixto' (solo para bolus)
    purpose = db.Column(db.String(20))
    # minutos antes de la comida en que se inyectó (0 = simultáneo, 15 = pre-bolo 15min)
    pre_meal_min = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Insulina {self.type} {self.units}U @ {self.timestamp}>"


class Activity(db.Model):
    __tablename__ = "activities"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    activity_type = db.Column(db.String(100), nullable=False)
    duration_min = db.Column(db.Integer)
    # baja, media, alta
    intensity = db.Column(db.String(20))
    # aerobico | anaerobico | mixto  (None = no especificado → inferido por nombre)
    exercise_type = db.Column(db.String(20))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Actividad {self.activity_type} {self.duration_min}min>"


class FoodItem(db.Model):
    """Alimentos frecuentes con sus valores nutricionales."""
    __tablename__ = "food_items"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    # Valores por porción habitual
    serving_desc = db.Column(db.String(100))        # ej. "1 tortilla", "1 taza"
    serving_g = db.Column(db.Float)                 # gramos de la porción
    carbs_per_serving = db.Column(db.Float, nullable=False, default=0)
    fat_per_serving = db.Column(db.Float, default=0)
    protein_per_serving = db.Column(db.Float, default=0)
    calories_per_serving = db.Column(db.Float, default=0)
    fiber_per_serving = db.Column(db.Float, default=0)    # fibra (g) por porción
    glycemic_index = db.Column(db.Integer)                # ÍG 0-100 (nullable)
    category = db.Column(db.String(50))             # ej. cereal, fruta, proteína
    notes = db.Column(db.Text)
    times_used = db.Column(db.Integer, default=0)   # para ordenar por frecuencia
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Alimento {self.name} {self.carbs_per_serving}g CH>"


class UserSettings(db.Model):
    """Configuración personal del usuario (ISF manual, ratio I:CH, objetivo, etc.)."""
    __tablename__ = "user_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.String(500))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Setting {self.key}={self.value}>"


class CGMImport(db.Model):
    """Registro de archivos CSV importados desde Freestyle Libre."""
    __tablename__ = "cgm_imports"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)
    records_count = db.Column(db.Integer, default=0)
    date_from = db.Column(db.DateTime)
    date_to = db.Column(db.DateTime)

    def __repr__(self):
        return f"<Import {self.filename} {self.records_count} registros>"
