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


class GlucosePrediction(db.Model):
    """
    Registro de cada predicción de glucemia generada por el modelo.
    Se resuelve automáticamente cuando llega la lectura real 30/60 min después.
    Permite calcular MAE, bias sistemático y aplicar corrección adaptiva.
    """
    __tablename__ = "glucose_predictions"

    id           = db.Column(db.Integer, primary_key=True)
    predicted_at = db.Column(db.DateTime, nullable=False)   # cuándo se hizo la predicción

    # Estado en el momento de la predicción
    g_actual     = db.Column(db.Float)    # glucemia al momento de predecir
    iob          = db.Column(db.Float)    # IOB al momento
    cob          = db.Column(db.Float)    # COB al momento
    roc          = db.Column(db.Float)    # tendencia mg/dL/min
    isf_used     = db.Column(db.Float)    # ISF efectivo usado
    icr_used     = db.Column(db.Float)    # ICR usado
    ex_factor    = db.Column(db.Float)    # factor de ejercicio

    # Valores predichos
    g_pred_30    = db.Column(db.Float)    # predicción a +30min
    g_pred_60    = db.Column(db.Float)    # predicción a +60min

    # Valores reales (se llenan cuando llega la lectura)
    g_real_30    = db.Column(db.Float)
    g_real_60    = db.Column(db.Float)
    error_30     = db.Column(db.Float)    # g_real_30 − g_pred_30
    error_60     = db.Column(db.Float)    # g_real_60 − g_pred_60

    resolved_30  = db.Column(db.Boolean, default=False)
    resolved_60  = db.Column(db.Boolean, default=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Prediccion {self.predicted_at} pred30={self.g_pred_30} real={self.g_real_30}>"


class MealPreset(db.Model):
    """
    Comidas favoritas / presets para registro rápido.

    Almacena una comida tipo plantilla con sus ingredientes en JSON,
    de modo que el usuario puede cargarla en QuickLog con un solo click.
    """
    __tablename__ = "meal_presets"

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)
    # Ingredientes como JSON: [{"name":str, "carbs_g":float, "protein_g":float,
    #                           "fat_g":float, "calories":float, "grams":float}]
    components   = db.Column(db.Text, nullable=False, default="[]")
    # Macros totales (desnormalizados para mostrar en chips rápidamente)
    carbs_g      = db.Column(db.Float, default=0)
    fat_g        = db.Column(db.Float, default=0)
    protein_g    = db.Column(db.Float, default=0)
    calories     = db.Column(db.Float, default=0)
    # Estadísticas de uso
    use_count    = db.Column(db.Integer, default=0)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def components_list(self):
        import json
        try:
            return json.loads(self.components or "[]")
        except Exception:
            return []

    def __repr__(self):
        return f"<MealPreset {self.name} {self.carbs_g}g CH>"


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


# ══════════════════════════════════════════════════════════════════════════════
# Personal Metabolic Model (PMM) — tablas de aprendizaje adaptativo
# ══════════════════════════════════════════════════════════════════════════════

class PMMParameter(db.Model):
    """
    Parámetro metabólico aprendido con incertidumbre Bayesiana.

    Cada fila = un parámetro (ISF, ICR) en un contexto (bloque horario).
    mu/sigma forman la distribución posterior: param ~ N(mu, sigma²).

    context_block:
        -1 = global (sin distinción horaria)
         0, 4, 8, 12, 16, 20 = bloque de 4 horas (hora de inicio)
    """
    __tablename__ = "pmm_parameters"

    id            = db.Column(db.Integer, primary_key=True)
    param_name    = db.Column(db.String(30), nullable=False)   # 'ISF' | 'ICR'
    context_block = db.Column(db.Integer, nullable=False, default=-1)
    mu            = db.Column(db.Float, nullable=False)        # estimación actual
    sigma         = db.Column(db.Float, nullable=False)        # incertidumbre (std)
    n_obs         = db.Column(db.Integer, default=0)           # obs incorporadas
    last_updated  = db.Column(db.DateTime, default=datetime.utcnow)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        block_label = f"bloque {self.context_block}h" if self.context_block >= 0 else "global"
        return (f"<PMM {self.param_name} {block_label} "
                f"μ={self.mu:.1f} σ={self.sigma:.1f} n={self.n_obs}>")


class PMMObservation(db.Model):
    """
    Episodio de aprendizaje identificado y evaluado.

    Cada fila = un evento (corrección o comida+bolo) del que se pudo
    extraer una observación del parámetro correspondiente.
    Incluye la calidad de la observación y el resultado del update Bayesiano.
    """
    __tablename__ = "pmm_observations"

    id             = db.Column(db.Integer, primary_key=True)
    param_name     = db.Column(db.String(30), nullable=False)   # 'ISF' | 'ICR'
    source_type    = db.Column(db.String(30))  # 'correction_bolus' | 'meal_bolus'
    source_id      = db.Column(db.Integer)     # id en insulin_doses
    observed_at    = db.Column(db.DateTime, nullable=False)
    time_block     = db.Column(db.Integer)     # 0,4,8,12,16,20
    quality_score  = db.Column(db.Float)       # 0-1
    observed_value = db.Column(db.Float)       # ISF_obs o ICR_obs
    obs_sigma      = db.Column(db.Float)       # ruido estimado de la obs
    # Estado del parámetro antes/después del update
    mu_before      = db.Column(db.Float)
    sigma_before   = db.Column(db.Float)
    mu_after       = db.Column(db.Float)
    sigma_after    = db.Column(db.Float)
    used_in_update = db.Column(db.Boolean, default=False)
    skip_reason    = db.Column(db.String(100)) # si no se usó, por qué
    created_at     = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return (f"<PMMObs {self.param_name} val={self.observed_value:.1f} "
                f"q={self.quality_score:.2f} @ {self.observed_at}>")
