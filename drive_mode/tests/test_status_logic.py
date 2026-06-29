"""
drive_mode/tests/test_status_logic.py
──────────────────────────────────────
Tests de la lógica determinista de Drive Mode + reglas de seguridad.

Ejecutar:  python3 -m pytest drive_mode/tests/ -q
"""
from drive_mode.state import TrendDirection, Status, StatusLevel
from drive_mode.status_logic import classify_status, classify_trend, MESSAGES
from drive_mode.live_activity_adapter import to_live_activity_payload
from drive_mode import build_drive_mode_state  # smoke (no se llama sin DB)


def st(glucose, trend=TrendDirection.FLAT, age=3, connected=True):
    return classify_status(glucose, trend, age, connected)


# ── Tendencia ────────────────────────────────────────────────────────────
def test_trend_classification():
    assert classify_trend(None) == TrendDirection.UNKNOWN
    assert classify_trend(0.0) == TrendDirection.FLAT
    assert classify_trend(0.9) == TrendDirection.FLAT
    assert classify_trend(1.2) == TrendDirection.RISING_SLOWLY
    assert classify_trend(2.5) == TrendDirection.RISING_FAST
    assert classify_trend(-1.2) == TrendDirection.FALLING_SLOWLY
    assert classify_trend(-3.0) == TrendDirection.FALLING_FAST


# ── Demo states del brief ───────────────────────────────────────────────
def test_demo_stable_112():
    r = st(112, TrendDirection.FLAT)
    assert r["status"] == Status.STABLE
    assert r["status_level"] == StatusLevel.NORMAL
    assert r["safety_message"] == "Stable"


def test_demo_falling_82():
    r = st(82, TrendDirection.FALLING_SLOWLY)
    assert r["status"] == Status.LOW
    assert r["status_level"] == StatusLevel.CAUTION
    assert r["safety_message"] == "Check when safe"


def test_demo_urgent_low_68():
    r = st(68, TrendDirection.FALLING_FAST)
    assert r["status"] == Status.URGENT_LOW
    assert r["status_level"] == StatusLevel.URGENT
    assert "stop when safe" in r["safety_message"].lower()


def test_demo_high_210():
    r = st(210, TrendDirection.RISING_SLOWLY)
    assert r["status"] == Status.HIGH
    assert r["status_level"] == StatusLevel.CAUTION
    assert r["safety_message"] == "Glucose high"


def test_demo_stale():
    r = st(120, TrendDirection.FLAT, age=20)
    assert r["status"] == Status.STALE
    assert r["status_level"] == StatusLevel.UNAVAILABLE
    assert r["safety_message"] == "Data stale"


def test_demo_disconnected():
    r = st(120, TrendDirection.FLAT, connected=False)
    assert r["status"] == Status.DISCONNECTED
    assert r["status_level"] == StatusLevel.UNAVAILABLE
    assert r["safety_message"] == "Sensor disconnected"


# ── Fronteras de glucosa ─────────────────────────────────────────────────
def test_glucose_boundaries():
    assert st(69)["status"] == Status.URGENT_LOW
    assert st(70)["status"] == Status.LOW
    assert st(84)["status"] == Status.LOW
    assert st(85)["status"] == Status.STABLE
    assert st(180)["status"] == Status.STABLE
    assert st(181)["status"] == Status.HIGH
    assert st(250)["status"] == Status.HIGH
    assert st(251)["status"] == Status.URGENT_HIGH


def test_attention_only_when_falling_fast_near_low():
    assert st(95, TrendDirection.FALLING_FAST)["status"] == Status.ATTENTION
    assert st(95, TrendDirection.FLAT)["status"] == Status.STABLE
    assert st(95, TrendDirection.FALLING_SLOWLY)["status"] == Status.STABLE


# ── Seguridad: prioridad y mensajería ────────────────────────────────────
def test_unreliable_data_never_asserts_safety():
    # glucosa "baja" pero sin sensor → NO urgent_low, sino disconnected
    assert st(60, connected=False)["status"] == Status.DISCONNECTED
    # datos viejos ganan a cualquier valor de glucosa (no se afirma urgent_low)
    assert st(60, age=30)["status"] == Status.STALE          # 15 < 30 ≤ 45 → stale
    assert st(60, age=50)["status"] == Status.DISCONNECTED   # > 45 → disconnected
    # en ambos casos el nivel es 'unavailable', nunca urgent por la glucosa
    assert st(60, age=30)["status_level"] == StatusLevel.UNAVAILABLE


def test_messages_are_non_prescriptive():
    banned = ("eat", "carb", "inject", "insulin", "unit", "dose", "bolus", "correct")
    for msg in MESSAGES.values():
        low = msg.lower()
        assert not any(b in low for b in banned), f"mensaje prescriptivo: {msg}"
        assert len(msg) <= 32, f"mensaje muy largo: {msg}"


# ── Safety-first monótono: a menor glucosa, nivel ≥ urgencia ──────────────
def test_lower_glucose_not_less_urgent():
    order = {StatusLevel.NORMAL: 0, StatusLevel.CAUTION: 1, StatusLevel.URGENT: 2}
    lvl = lambda g: order[st(g)["status_level"]]
    assert lvl(60) >= lvl(80) >= lvl(120)


# ── Adapter: payload seguro (sin campos prohibidos) ──────────────────────
def test_adapter_payload_has_no_forbidden_fields():
    from drive_mode.state import DriveModeState
    s = DriveModeState(
        glucose_value_mgdl=112, trend_direction=TrendDirection.FLAT, trend_rate=0.1,
        status=Status.STABLE, status_level=StatusLevel.NORMAL,
        last_update_at="2026-06-27T17:00:00", minutes_since_update=3,
        sensor_name="Libre 3", sensor_connected=True, stale_data=False,
        safety_message="Stable",
    )
    p = to_live_activity_payload(s)
    forbidden = ("iob", "insulin", "bolus", "dose", "forecast", "predict", "carb", "meal")
    assert not any(k for k in p for f in forbidden if f in k.lower())
    assert p["value"] == 112 and p["message"] == "Stable" and p["brand"] == "ORBIT"
    assert p["updated_text"] == "Updated 3 min ago"
