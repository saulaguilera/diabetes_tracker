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
    ml              = db.Column(db.Float)   # volumen en mL (líquidos; nullable)
    density_g_ml    = db.Column(db.Float)   # densidad usada para ml→g (informativo)

    def __repr__(self):
        if self.ml:
            return f"<Componente {self.name} {self.ml}ml ({self.carbs_g}g CH)>"
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
    sigma_30     = db.Column(db.Float)    # incertidumbre σ a +30min (mg/dL) — para calibración
    sigma_60     = db.Column(db.Float)    # incertidumbre σ a +60min — para calibración
    # Identificador de versión del modelo que generó la predicción.
    # Permite separar métricas por versión en el backtest.
    model_version= db.Column(db.String(40))

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

class TuningExperiment(db.Model):
    """
    Persistencia de un experimento de tuning (un único config evaluado).

    Cada experiment es:
      - Una SSMParameters específica (params_json + fingerprint determinístico)
      - Resultados (metrics_json) calculados via replay sobre histórico
      - Sub-scores + composite promotion_score
      - Identificación: name, git_commit, ventana de evaluación

    Append-only para auditoría completa de qué tuning ya se probó.
    """
    __tablename__ = "tuning_experiments"

    id              = db.Column(db.Integer, primary_key=True)
    name            = db.Column(db.String(120), index=True)
    param_hash      = db.Column(db.String(20), nullable=False, index=True)
    params_json     = db.Column(db.Text, nullable=False)
    days_window     = db.Column(db.Integer, nullable=False)
    n_records       = db.Column(db.Integer)
    git_commit      = db.Column(db.String(40))

    # Sub-scores
    score_calibration = db.Column(db.Float)
    score_innovation  = db.Column(db.Float)
    score_clinical    = db.Column(db.Float)
    score_stability   = db.Column(db.Float)
    score_accuracy    = db.Column(db.Float)
    score_composite   = db.Column(db.Float, index=True)

    # Métricas crudas (para Pareto y comparación detallada)
    metrics_json    = db.Column(db.Text)         # JSON compactado

    # Diagnóstico textual
    verdict         = db.Column(db.String(60))   # "white"|"biased"|"etc"
    note            = db.Column(db.Text)
    error           = db.Column(db.Text)         # si falló

    duration_ms     = db.Column(db.Integer)

    # ── Reproducibility hardening ───
    data_checksum   = db.Column(db.String(40))    # hash de eventos consumidos
    random_seed     = db.Column(db.Integer)       # seed numpy usado
    replay_checksum = db.Column(db.String(40))    # hash del output (para verify)

    # ── Lineage ───
    parent_name     = db.Column(db.String(120), index=True)   # experiment del que deriva

    # ── Automatic failure attribution ───
    diagnoses_json  = db.Column(db.Text)          # JSON de hipótesis ranked
    gates_passed    = db.Column(db.Integer)       # de los 8
    gates_json      = db.Column(db.Text)          # detalle por gate

    created_at      = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.Index("ix_tuning_name_hash", "name", "param_hash"),
        db.Index("ix_tuning_score", "score_composite"),
        db.Index("ix_tuning_parent", "parent_name"),
    )


class PredictionAudit(db.Model):
    """
    Log inmutable de auditoría científica de cada predicción.

    Una row por (predicted_at, horizon_min, model_version). Persiste TODO lo
    necesario para análisis de calibración, innovation diagnostics, coverage
    validation y rolling stability — sin tocar las predicciones primary
    (glucose_predictions) que usa la UI.

    Las columnas realized_* y inside_* se llenan post-hoc cuando llega la
    lectura CGM real en t + horizon_min ± tolerance.

    Diseño
    ------
    Append-only. Nunca se modifica una row excepto para resolverla.
    Pensado para consumirse vía bench/ sin afectar performance del hot path.
    """
    __tablename__ = "prediction_audit"

    id              = db.Column(db.Integer, primary_key=True)
    predicted_at    = db.Column(db.DateTime, nullable=False, index=True)
    horizon_min     = db.Column(db.Integer,  nullable=False)        # 30 | 60
    model_version   = db.Column(db.String(40), nullable=False, index=True)

    # Predicción probabilística
    mu              = db.Column(db.Float, nullable=False)            # μ posterior
    sigma           = db.Column(db.Float)                            # σ (combina state + R)
    ic50_low        = db.Column(db.Float)                            # μ − 0.674σ
    ic50_high       = db.Column(db.Float)                            # μ + 0.674σ
    ic90_low        = db.Column(db.Float)                            # μ − 1.645σ
    ic90_high       = db.Column(db.Float)                            # μ + 1.645σ
    p_hypo          = db.Column(db.Float)                            # P(G<70)
    p_hyper         = db.Column(db.Float)                            # P(G>180)
    confidence      = db.Column(db.Float)                            # composite C ∈ [0,1]

    # Estado del filtro (SOLO SSM — NULL para MC/GP)
    cov_trace       = db.Column(db.Float)                            # tr(P)
    cov_condition   = db.Column(db.Float)                            # κ(P) = λ_max/λ_min
    cov_min_eig     = db.Column(db.Float)
    cov_max_eig     = db.Column(db.Float)
    psd_ok          = db.Column(db.Boolean)                          # cov positive-definite?
    log_evidence    = db.Column(db.Float)                            # acumulado en el run

    # Innovation del último update del filtro (SOLO SSM)
    last_innov      = db.Column(db.Float)                            # z − h(x_pred)
    last_innov_z    = db.Column(db.Float)                            # innov / sigma_innov
    n_filter_updates = db.Column(db.Integer)

    # Resolución (se llena cuando llega CGM en t+horizon)
    realized_glucose = db.Column(db.Float)
    realized_at      = db.Column(db.DateTime)
    innovation       = db.Column(db.Float)                           # realized − mu
    innovation_z     = db.Column(db.Float)                           # (realized − mu) / sigma
    inside_ic50      = db.Column(db.Boolean)                         # realized ∈ [ic50_low, ic50_high]
    inside_ic90      = db.Column(db.Boolean)
    resolved         = db.Column(db.Boolean, default=False, index=True)

    created_at       = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.Index("ix_predaudit_model_pred", "model_version", "predicted_at"),
        db.Index("ix_predaudit_unresolved", "resolved", "predicted_at"),
    )

    def __repr__(self):
        return (f"<PredAudit {self.model_version} +{self.horizon_min}m "
                f"μ={self.mu:.0f}±{self.sigma:.0f} "
                f"{'res' if self.resolved else 'pend'}>")


class SSMInnovation(db.Model):
    """
    Log de innovations del UKF — granular por update individual.

    Cada vez que el filtro absorbe una observación CGM, calculamos:
        innovation     = y_obs − h(x_pred)
        innovation_var = h(P_pred)h.T + R
        innov_z        = innovation / √innovation_var

    Las innovations bajo un modelo bien especificado deben ser ~ white noise:
        E[innov]                = 0       (sin bias)
        Var[innov]              = innov_var_pred (calibrado)
        Autocorr[innov]         ≈ 0       (sin info dejada en la señal)

    Tests downstream (bench/metrics/innovations.py):
      - Ljung-Box sobre la secuencia de innov_z
      - Mean/Var rolling
      - PSD: si cov_health degrada, innovations escalan mal
    """
    __tablename__ = "ssm_innovations"

    id              = db.Column(db.Integer, primary_key=True)
    ts              = db.Column(db.DateTime, nullable=False, index=True)    # del CGM observado
    run_at          = db.Column(db.DateTime, nullable=False, index=True)    # cuándo se computó
    model_version   = db.Column(db.String(40), nullable=False)

    y_obs           = db.Column(db.Float, nullable=False)
    y_pred          = db.Column(db.Float, nullable=False)
    innovation      = db.Column(db.Float, nullable=False)            # y_obs − y_pred
    sigma_pred      = db.Column(db.Float, nullable=False)            # √(H P H.T + R)
    innovation_z    = db.Column(db.Float, nullable=False)            # innov / sigma_pred

    # Snapshot ligero del filter en ese instante
    g_state         = db.Column(db.Float)                            # G en x_pred
    p_g_g           = db.Column(db.Float)                            # var de G en P_pred
    rejected        = db.Column(db.Boolean, default=False)           # outlier gating
    log_likelihood  = db.Column(db.Float)                            # log p(y|x_pred,P_pred)

    created_at      = db.Column(db.DateTime, default=datetime.utcnow)


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


class PMMDriftState(db.Model):
    """
    Estado persistente del detector CUSUM de drift metabólico.

    Una sola fila por usuario (singleton).
    Almacena los acumuladores del CUSUM two-sided y el σ_ref adaptivo.

    drift_dir:
        'resistance'  → CUSUM_pos > h  (glucosa más alta de lo esperado)
        'sensitivity' → CUSUM_neg < -h (glucosa más baja de lo esperado)
        None          → sin drift activo

    drift_factor:
        Factor corrector multiplicativo del bolo (no del ISF).
        Equivalentemente: eff_ISF = ISF / drift_factor

        >1.0 → resistencia: necesitás MÁS insulina
               (dividir ISF por drift_factor → ISF efectivo más bajo
                → corrección mayor por cada mg/dL sobre el objetivo)
        <1.0 → sensibilidad: necesitás MENOS insulina
               (dividir ISF por drift_factor → ISF efectivo más alto
                → corrección menor)
        1.0  → sin drift detectado, ISF sin ajuste
    """
    __tablename__ = "pmm_drift_state"

    id           = db.Column(db.Integer, primary_key=True)
    cusum_pos    = db.Column(db.Float, default=0.0)       # acumulador positivo
    cusum_neg    = db.Column(db.Float, default=0.0)       # acumulador negativo
    sigma_ref    = db.Column(db.Float, default=20.0)      # σ adaptivo del residual
    drift_active = db.Column(db.Boolean, default=False)   # alarma activa
    drift_dir    = db.Column(db.String(20))               # 'resistance' | 'sensitivity' | None
    drift_factor = db.Column(db.Float, default=1.0)       # factor corrector para ISF
    drift_since  = db.Column(db.DateTime)                 # cuándo empezó el drift actual
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        state = f"drift={self.drift_dir}" if self.drift_active else "normal"
        return (f"<PMMDriftState {state} "
                f"C+={self.cusum_pos:.1f} C-={self.cusum_neg:.1f} "
                f"σ={self.sigma_ref:.1f}>")
