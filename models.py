from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


class User(db.Model):
    """Cuenta de usuario (multi-usuario fase 1). El usuario #1 se siembra al
    primer arranque desde APP_USERNAME/APP_PASSWORD — todos los datos
    históricos pre-multiusuario le pertenecen (backfill en app.py)."""
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(300), nullable=False)
    display_name = db.Column(db.String(120))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Conector CGM del usuario: libre | dexcom | nightscout (utils/cgm_connectors).
    # Las credenciales van cifradas (Fernet derivada de SECRET_KEY), NUNCA en
    # texto plano. Para nightscout: email_enc = URL del sitio, password_enc = token.
    cgm_provider = db.Column(db.String(20), default="libre")
    libre_email_enc = db.Column(db.Text)
    libre_password_enc = db.Column(db.Text)

    def __repr__(self):
        return f"<User {self.id} {self.username!r}>"


class TenantScoped:
    """Mixin: marca un modelo como DATOS DE UN USUARIO. Los eventos del final
    de este archivo (a) filtran automáticamente todo SELECT por el usuario del
    contexto y (b) asignan user_id en cada insert. Un solo punto de
    enforcement: imposible olvidar un filter_by en algún endpoint.

    Para consultas administrativas sin filtro (scripts, migraciones):
        db.session.execute(stmt, execution_options={"all_users": True})
    o correr sin contexto de usuario (current_user_id() → None)."""
    user_id = db.Column(db.Integer, index=True)


class GlucoseReading(TenantScoped, db.Model):
    __tablename__ = "glucose_readings"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    value_mgdl = db.Column(db.Float, nullable=False)
    # manual, cgm_historic, cgm_scan, cgm_strip
    source = db.Column(db.String(20), nullable=False, default="manual")
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Calidad / corrección de lecturas CGM ────────────────────────────
    # is_artifact: True si esta lectura es spurious (compresión, scan failure,
    #   drop-spike-recover artificial). Marcadas como artefacto SE EXCLUYEN
    #   del hypo predictor, del daily brief, del bench y del PMM update.
    # original_value_mgdl: si Libre corrigió esta lectura retroactivamente,
    #   guardamos el valor original para auditoría.
    # corrected_at: cuándo fue corregida por el re-sync.
    is_artifact         = db.Column(db.Boolean, default=False, index=True)
    artifact_reason     = db.Column(db.String(40))   # 'drop_spike' | 'manual' | etc.
    original_value_mgdl = db.Column(db.Float)        # NULL si no fue corregida
    corrected_at        = db.Column(db.DateTime)

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


class Meal(TenantScoped, db.Model):
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


class MealComponent(TenantScoped, db.Model):
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


class InsulinDose(TenantScoped, db.Model):
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


class Activity(TenantScoped, db.Model):
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


class ContextTag(TenantScoped, db.Model):
    """Etiqueta de contexto: estrés, enfermedad, mal sueño, viaje…
    Cosas que mueven la glucosa pero no son comida/insulina/ejercicio.
    Alimentan el contexto del copiloto y el análisis de excursiones."""
    __tablename__ = "context_tags"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    tag = db.Column(db.String(40), nullable=False)     # estres | enfermo | mal_sueno | viaje | alcohol | otro
    notes = db.Column(db.Text)                          # detalle libre opcional
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<Contexto {self.tag} @ {self.timestamp:%d/%m %H:%M}>"


class CopilotNotification(TenantScoped, db.Model):
    """Notificación in-app de Orbit Copilot (p.ej. «🧠 Orbit encontró algo»
    cuando el detector encuentra un patrón nuevo). Se listan en la campanita
    del header; read_at marca cuándo se vieron."""
    __tablename__ = "copilot_notifications"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    kind = db.Column(db.String(40), default="pattern")   # pattern | (futuro: sync, basal…)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text)
    read_at = db.Column(db.DateTime)

    def __repr__(self):
        return f"<Notif {self.kind} {self.title[:30]!r} @ {self.created_at:%d/%m %H:%M}>"


class FoodItem(TenantScoped, db.Model):
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


class MealPreset(TenantScoped, db.Model):
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


class CGMImport(TenantScoped, db.Model):
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

class DailyBrief(TenantScoped, db.Model):
    """
    Resumen narrativo diario del estado metabólico.

    Cache de un día — se genera una vez por día (típicamente al amanecer)
    y se sirve desde DB en cada request. El refresh manual sobrescribe.

    summary_json: el DailyMetabolicSummary serializado (data determinística
        calculada por services/daily_brief.py, no por LLM).
    narrative:    el texto en español generado por Claude a partir del summary.
    confidence:   score [0,1] de la calidad de los datos (data completeness,
        sensor gaps). Si <0.5, el narrative es un fallback no-LLM.

    Append-only: si querés re-generar un día específico, se inserta una row
    nueva y el endpoint sirve la más reciente. Histórico preservado.
    """
    __tablename__ = "daily_briefs"

    id              = db.Column(db.Integer, primary_key=True)
    day             = db.Column(db.Date, nullable=False, index=True)  # día que resume
    generated_at    = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    summary_json    = db.Column(db.Text, nullable=False)            # JSON
    narrative       = db.Column(db.Text, nullable=False)
    confidence      = db.Column(db.Float, default=1.0)
    tone            = db.Column(db.String(20), default="supportive")

    # Para debugging / observability
    llm_used        = db.Column(db.Boolean, default=False)          # True si Claude generó
    llm_model       = db.Column(db.String(40))
    llm_tokens_in   = db.Column(db.Integer)
    llm_tokens_out  = db.Column(db.Integer)
    llm_latency_ms  = db.Column(db.Integer)
    llm_prompt      = db.Column(db.Text)                             # prompt completo
    error           = db.Column(db.Text)                             # si falló

    __table_args__ = (
        db.Index("ix_daily_brief_day", "day"),
    )

    def __repr__(self):
        return f"<DailyBrief {self.day} conf={self.confidence:.2f}>"


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


class HypoRiskAudit(db.Model):
    """
    Audit trail de evaluaciones de riesgo de hipoglucemia nocturna.

    Cada fila = un assessment completo generado por hypo_risk_engine.py.
    Permite revisión post-evento: comparar riesgo proyectado vs. real.

    Ciclo de vida de una fila
    ─────────────────────────
      1. INSERT en assess_nocturnal_hypo_risk() → campos prediction_* poblados.
      2. resolve_pending_hypo_audits() corre después del sync LibreLinkUp:
         busca lecturas CGM reales en la ventana [trough±90 min] y rellena
         los campos outcome_*.
      3. compute_hypo_performance() agrega las filas resueltas para métricas.
    """
    __tablename__ = "hypo_risk_audit"

    id                        = db.Column(db.Integer, primary_key=True)
    assessed_at               = db.Column(db.DateTime, nullable=False, index=True)
    current_glucose           = db.Column(db.Float, nullable=False)
    roc                       = db.Column(db.Float)                # mg/dL/min
    proposed_bolus            = db.Column(db.Float, default=0.0)

    # Core probabilístico
    risk_score                = db.Column(db.Float, nullable=False)
    p_hypo_70                 = db.Column(db.Float, nullable=False)
    p_hypo_55                 = db.Column(db.Float)
    min_predicted_glucose     = db.Column(db.Float)
    min_glucose_eta_min       = db.Column(db.Integer)
    severity                  = db.Column(db.String(20))           # low/moderate/high/critical

    # Metadata del modelo
    ssm_available             = db.Column(db.Boolean, default=False)
    fallback_used             = db.Column(db.Boolean, default=False)

    # Factores contribuyentes serializados
    contributing_factors_json = db.Column(db.Text)

    # ── Campos de predicción extendidos (Outcome Tracking) ────────────────────
    # Guardados en el momento del assessment para poder resolver después.
    projected_trough_time     = db.Column(db.DateTime)             # assessed_at + eta_min
    alert_triggered           = db.Column(db.Boolean, default=False)  # ¿se disparó alerta?
    resolved_confidence       = db.Column(db.Float)                # confidence.score en assess time

    # ── Outcome (rellenados por resolve_pending_hypo_audits) ──────────────────
    resolved_at               = db.Column(db.DateTime, index=True) # NULL = aún sin resolver
    outcome_class             = db.Column(db.String(2))            # TP | FP | FN | TN
    true_positive             = db.Column(db.Boolean)
    false_positive            = db.Column(db.Boolean)
    false_negative            = db.Column(db.Boolean)
    true_negative             = db.Column(db.Boolean)

    # Valores reales del CGM en la ventana de observación
    actual_nadir              = db.Column(db.Float)    # mínimo real (mg/dL)
    actual_hypo_time          = db.Column(db.DateTime) # primera lectura < 70 en la ventana
    prediction_error          = db.Column(db.Float)    # min_predicted_glucose - actual_nadir
    warning_lead_time_min     = db.Column(db.Integer)  # minutos entre alerta y hipo real

    # ── Alert fatigue tracking (Fase 8) ───────────────────────────────────────
    # True si el usuario desestimó la alerta manualmente (vía /api/hypo-risk/dismiss)
    alert_fatigue_ignored     = db.Column(db.Boolean, default=False)
    dismissed_at              = db.Column(db.DateTime)

    created_at                = db.Column(db.DateTime, default=datetime.utcnow)

    # ── Propiedades computadas ────────────────────────────────────────────────

    @property
    def is_resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def had_real_hypo(self) -> bool:
        """True si la ventana de observación tuvo una glucemia < 70."""
        return self.actual_nadir is not None and self.actual_nadir < 70.0

    def to_dict(self) -> dict:
        return {
            "id":                     self.id,
            "assessed_at":            self.assessed_at.isoformat() if self.assessed_at else None,
            "current_glucose":        self.current_glucose,
            "risk_score":             self.risk_score,
            "p_hypo_70":              self.p_hypo_70,
            "p_hypo_55":              self.p_hypo_55,
            "min_predicted_glucose":  self.min_predicted_glucose,
            "min_glucose_eta_min":    self.min_glucose_eta_min,
            "severity":               self.severity,
            "alert_triggered":        self.alert_triggered,
            "projected_trough_time":  self.projected_trough_time.isoformat() if self.projected_trough_time else None,
            "resolved_confidence":    self.resolved_confidence,
            # Outcome
            "is_resolved":            self.is_resolved,
            "resolved_at":            self.resolved_at.isoformat() if self.resolved_at else None,
            "outcome_class":          self.outcome_class,
            "actual_nadir":           self.actual_nadir,
            "actual_hypo_time":       self.actual_hypo_time.isoformat() if self.actual_hypo_time else None,
            "prediction_error":       self.prediction_error,
            "warning_lead_time_min":  self.warning_lead_time_min,
            # Fatigue
            "alert_fatigue_ignored":  self.alert_fatigue_ignored,
        }

    def __repr__(self):
        outcome = f" [{self.outcome_class}]" if self.outcome_class else " [unresolved]"
        return (f"<HypoRiskAudit {self.assessed_at} "
                f"risk={self.risk_score:.2f} sev={self.severity} "
                f"G={self.current_glucose} bolus={self.proposed_bolus}U{outcome}>")


# ── Multi-usuario: enforcement automático de tenancy ─────────────────────────
# (a) Todo SELECT que involucre un modelo TenantScoped se filtra por el usuario
#     del contexto (with_loader_criteria se propaga a joins, relaciones y
#     lazy-loads). (b) Todo insert de un TenantScoped recibe user_id.
# Sin contexto de usuario (arranque, scripts, tests de infraestructura) no se
# filtra — los endpoints de datos exigen login, así que un request autenticado
# SIEMPRE tiene contexto.
from sqlalchemy import event as _sa_event
from sqlalchemy.orm import Session as _SASession, with_loader_criteria as _wlc


@_sa_event.listens_for(_SASession, "do_orm_execute")
def _tenant_read_filter(execute_state):
    if not execute_state.is_select:
        return
    if execute_state.execution_options.get("all_users", False):
        return
    from helpers import current_user_id
    uid = current_user_id()
    if uid is None:
        return
    execute_state.statement = execute_state.statement.options(
        _wlc(TenantScoped, lambda cls: cls.user_id == uid, include_aliases=True)
    )


@_sa_event.listens_for(_SASession, "before_flush")
def _tenant_write_assign(session, flush_context, instances):
    from helpers import current_user_id
    uid = current_user_id()
    if uid is None:
        return
    for obj in session.new:
        if isinstance(obj, TenantScoped) and obj.user_id is None:
            obj.user_id = uid
