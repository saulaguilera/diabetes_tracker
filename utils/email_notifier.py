"""
utils/email_notifier.py — Envío de reportes semanales por correo electrónico.

Configuración (variables de entorno en Railway):
    NOTIFY_EMAIL     destinatario (ej. "vos@gmail.com")
    SMTP_HOST        servidor SMTP (ej. "smtp.gmail.com")
    SMTP_PORT        puerto, default 587 (TLS/STARTTLS)
    SMTP_USER        usuario SMTP (normalmente el email remitente)
    SMTP_PASSWORD    contraseña SMTP o App Password de Gmail

Compatibilidad:
    • Gmail       → smtp.gmail.com:587 + App Password (no la contraseña normal)
    • Outlook     → smtp-mail.outlook.com:587
    • SendGrid    → smtp.sendgrid.net:587, user=apikey, password=API_KEY
    • Mailgun     → smtp.mailgun.org:587

Para Gmail, generá un App Password en:
  Cuenta Google → Seguridad → Verificación en 2 pasos → Contraseñas de aplicación
"""
from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── Variables de entorno ───────────────────────────────────────────────────────
_NOTIFY_EMAIL  = os.environ.get("NOTIFY_EMAIL",   "")
_SMTP_HOST     = os.environ.get("SMTP_HOST",      "")
_SMTP_PORT     = int(os.environ.get("SMTP_PORT",  "587"))
_SMTP_USER     = os.environ.get("SMTP_USER",      "")
_SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD",  "")


def _smtp_configured() -> bool:
    return all([_NOTIFY_EMAIL, _SMTP_HOST, _SMTP_USER, _SMTP_PASSWORD])


def _trend_icon(tendencia: str) -> str:
    return {
        "mejorando":    "📈 Mejorando",
        "empeorando":   "📉 Empeorando",
        "estable":      "➡️ Estable",
        "insuficiente": "⏳ Datos insuficientes",
    }.get(tendencia, tendencia)


def _bias_label(bias: float | None) -> str:
    if bias is None:
        return "—"
    if bias > 3:
        return f"+{bias} mg/dL (el modelo subestima — predice menos de lo real)"
    if bias < -3:
        return f"{bias} mg/dL (el modelo sobreestima — predice más de lo real)"
    return f"{bias:+.1f} mg/dL ✓ centrado"


def _build_html(accuracy: dict, dawn: dict | None = None) -> str:
    """Genera el cuerpo HTML del reporte semanal."""
    now     = datetime.now()
    fecha   = now.strftime("%d/%m/%Y")
    hora    = now.strftime("%H:%M")

    n30      = accuracy.get("n_30", 0)
    mae30    = accuracy.get("mae_30")
    bias30   = accuracy.get("bias_30")
    rmse30   = accuracy.get("rmse_30")
    pct30    = accuracy.get("pct_dentro_20_30")

    n60      = accuracy.get("n_60", 0)
    mae60    = accuracy.get("mae_60")
    bias60   = accuracy.get("bias_60")
    rmse60   = accuracy.get("rmse_60")
    pct60    = accuracy.get("pct_dentro_20_60")

    tendencia = accuracy.get("tendencia", "insuficiente")
    trend_txt = _trend_icon(tendencia)

    # Colores semafóricos por MAE
    def _mae_color(mae):
        if mae is None:     return "#888"
        if mae <= 12:       return "#22c55e"   # verde — excelente
        if mae <= 20:       return "#f59e0b"   # amarillo — aceptable
        return               "#ef4444"          # rojo — mejorar

    def _pct_color(pct):
        if pct is None:     return "#888"
        if pct >= 80:       return "#22c55e"
        if pct >= 60:       return "#f59e0b"
        return               "#ef4444"

    def _val(v, unit="", decimals=1):
        if v is None:
            return "<span style='color:#888'>—</span>"
        return f"{v:.{decimals}f}{unit}"

    # Sección dawn
    dawn_row = ""
    if dawn and dawn.get("ok"):
        mag    = dawn.get("magnitude", 0)
        mag_s  = f"{mag:.3f} mg/dL/min"
        active = "Activo ahora" if dawn.get("active_now") else "Fuera de ventana (3–8am)"
        dawn_row = f"""
        <tr>
          <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;color:#6b7280">Fenómeno del alba</td>
          <td colspan="2" style="padding:10px 16px;border-bottom:1px solid #f3f4f6">
            <strong>{mag_s}</strong> — {active}
          </td>
        </tr>"""

    # Interpretación automática
    if n30 < 5:
        interpretacion = (
            "⚠️ Aún hay pocas predicciones resueltas para evaluar la precisión. "
            "La app necesita al menos 5 ciclos de 30 min con lecturas reales para generar métricas confiables."
        )
    elif mae30 and mae30 <= 12 and pct30 and pct30 >= 80:
        interpretacion = (
            f"✅ El modelo está funcionando muy bien: {pct30}% de las predicciones a +30 min "
            f"tuvieron un error ≤ 20 mg/dL (MAE = {mae30} mg/dL). "
            "Seguí usando la app normalmente para mantener la calibración."
        )
    elif mae30 and mae30 > 20:
        interpretacion = (
            f"⚠️ El error promedio a +30 min es {mae30} mg/dL — por encima del umbral clínico de 20 mg/dL. "
            "Revisá si el ISF y el ICR están bien configurados en Configuración, "
            "o esperá más días para que el modelo acumule más datos."
        )
    else:
        interpretacion = (
            f"El modelo tiene una precisión aceptable (MAE {mae30} mg/dL a 30 min). "
            "A medida que acumule más datos, la calibración va a mejorar automáticamente."
        )

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Reporte semanal — Diabetes Tracker</title>
</head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1f2937">

  <!-- Contenedor principal -->
  <table width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:32px auto">
    <tr>
      <td style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 1px 6px rgba(0,0,0,.08)">

        <!-- Cabecera -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="background:linear-gradient(135deg,#6366f1,#8b5cf6);padding:28px 32px">
              <div style="font-size:22px;font-weight:700;color:#fff">📊 Reporte semanal</div>
              <div style="font-size:13px;color:rgba(255,255,255,.8);margin-top:4px">
                Precisión del modelo de predicción · {fecha} {hora}
              </div>
            </td>
          </tr>
        </table>

        <!-- Resumen rápido -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding:24px 32px 8px">
              <div style="font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#6b7280">
                Tendencia del modelo
              </div>
              <div style="font-size:20px;font-weight:700;margin-top:4px">{trend_txt}</div>
            </td>
          </tr>
        </table>

        <!-- Tabla de métricas -->
        <table width="100%" cellpadding="0" cellspacing="0" style="margin:16px 0">
          <!-- Header -->
          <tr style="background:#f8fafc">
            <th style="padding:10px 16px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;border-bottom:2px solid #e5e7eb">Métrica</th>
            <th style="padding:10px 16px;text-align:center;font-size:12px;color:#6b7280;font-weight:600;border-bottom:2px solid #e5e7eb">+30 min</th>
            <th style="padding:10px 16px;text-align:center;font-size:12px;color:#6b7280;font-weight:600;border-bottom:2px solid #e5e7eb">+60 min</th>
          </tr>
          <!-- Predicciones evaluadas -->
          <tr>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;color:#6b7280">Predicciones evaluadas</td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center"><strong>{n30}</strong></td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center"><strong>{n60}</strong></td>
          </tr>
          <!-- MAE -->
          <tr style="background:#fafafa">
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;color:#6b7280">
              MAE — Error medio absoluto
              <div style="font-size:11px;color:#9ca3af">Cuanto más bajo mejor. &lt;12: excelente, &lt;20: aceptable</div>
            </td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center">
              <strong style="color:{_mae_color(mae30)};font-size:18px">{_val(mae30, ' mg/dL')}</strong>
            </td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center">
              <strong style="color:{_mae_color(mae60)};font-size:18px">{_val(mae60, ' mg/dL')}</strong>
            </td>
          </tr>
          <!-- RMSE -->
          <tr>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;color:#6b7280">
              RMSE — Error cuadrático medio
              <div style="font-size:11px;color:#9ca3af">Penaliza errores grandes. Bueno si ≈ MAE</div>
            </td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center">{_val(rmse30, ' mg/dL')}</td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center">{_val(rmse60, ' mg/dL')}</td>
          </tr>
          <!-- % dentro de ±20 -->
          <tr style="background:#fafafa">
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;color:#6b7280">
              Dentro de ±20 mg/dL
              <div style="font-size:11px;color:#9ca3af">Estándar clínico de precisión CGM</div>
            </td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center">
              <strong style="color:{_pct_color(pct30)}">{_val(pct30, '%', 0) if pct30 is not None else '—'}</strong>
            </td>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;text-align:center">
              <strong style="color:{_pct_color(pct60)}">{_val(pct60, '%', 0) if pct60 is not None else '—'}</strong>
            </td>
          </tr>
          <!-- Bias -->
          <tr>
            <td style="padding:10px 16px;border-bottom:1px solid #f3f4f6;color:#6b7280">
              Bias sistemático
              <div style="font-size:11px;color:#9ca3af">Desviación promedio con signo</div>
            </td>
            <td colspan="2" style="padding:10px 16px;border-bottom:1px solid #f3f4f6;font-size:12px">
              <div>+30 min: {_bias_label(bias30)}</div>
              <div style="margin-top:4px">+60 min: {_bias_label(bias60)}</div>
            </td>
          </tr>
          {dawn_row}
        </table>

        <!-- Interpretación -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding:8px 32px 24px">
              <div style="background:#f0fdf4;border-left:4px solid #22c55e;padding:12px 16px;border-radius:6px;font-size:13px;line-height:1.5">
                {interpretacion}
              </div>
            </td>
          </tr>
        </table>

        <!-- Glosario rápido -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding:0 32px 24px">
              <div style="font-size:12px;color:#6b7280;line-height:1.6">
                <strong>MAE</strong> (Mean Absolute Error): promedio del error absoluto entre lo predicho y la glucemia real medida por el sensor.<br>
                <strong>RMSE</strong>: como el MAE pero penaliza más los errores grandes. Un RMSE mucho mayor que el MAE indica que hubo algunos errores muy grandes.<br>
                <strong>Bias</strong>: si el modelo predice sistemáticamente más o menos que la realidad, se corrige automáticamente en la siguiente predicción.
              </div>
            </td>
          </tr>
        </table>

        <!-- Footer -->
        <table width="100%" cellpadding="0" cellspacing="0">
          <tr>
            <td style="padding:16px 32px;background:#f8fafc;border-top:1px solid #e5e7eb">
              <div style="font-size:11px;color:#9ca3af;text-align:center">
                Diabetes Tracker · Reporte generado automáticamente cada lunes a las 9am<br>
                Para ver más detalles abrí la app → Diagnóstico del modelo.
              </div>
            </td>
          </tr>
        </table>

      </td>
    </tr>
  </table>
</body>
</html>"""

    return html


def send_weekly_accuracy_report() -> dict:
    """
    Genera y envía el reporte semanal de precisión del modelo por email.

    Retorna un dict con:
        ok      : bool
        mensaje : str  (descripción del resultado)
        skipped : bool (True si SMTP no está configurado)
    """
    if not _smtp_configured():
        return {
            "ok":      False,
            "skipped": True,
            "mensaje": (
                "SMTP no configurado. Definí las variables de entorno: "
                "NOTIFY_EMAIL, SMTP_HOST, SMTP_USER, SMTP_PASSWORD."
            ),
        }

    try:
        from utils.prediction_feedback import get_model_accuracy
        from utils.kinetics import dawn_roc_mgdl_min
        from helpers import _get_setting

        accuracy = get_model_accuracy(n=50)

        # Estado del fenómeno del alba
        mag_raw = _get_setting("dawn_magnitude")
        dawn = {
            "ok":         mag_raw is not None,
            "magnitude":  float(mag_raw) if mag_raw else None,
            "active_now": dawn_roc_mgdl_min() > 0.05,
        }

        html_body  = _build_html(accuracy, dawn=dawn)
        plain_body = _build_plain(accuracy)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"📊 Reporte semanal — modelo de predicción ({datetime.now().strftime('%d/%m/%Y')})"
        msg["From"]    = _SMTP_USER
        msg["To"]      = _NOTIFY_EMAIL

        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body,  "html",  "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.ehlo()
            server.starttls(context=context)
            server.login(_SMTP_USER, _SMTP_PASSWORD)
            server.sendmail(_SMTP_USER, _NOTIFY_EMAIL, msg.as_bytes())

        return {
            "ok":      True,
            "skipped": False,
            "mensaje": f"Reporte enviado a {_NOTIFY_EMAIL}",
            "n_30":    accuracy.get("n_30", 0),
            "mae_30":  accuracy.get("mae_30"),
            "mae_60":  accuracy.get("mae_60"),
        }

    except smtplib.SMTPAuthenticationError:
        return {
            "ok": False, "skipped": False,
            "mensaje": (
                "Error de autenticación SMTP. Si usás Gmail, necesitás un App Password "
                "(no la contraseña normal). Cuenta Google → Seguridad → Contraseñas de aplicación."
            ),
        }
    except smtplib.SMTPException as e:
        return {"ok": False, "skipped": False, "mensaje": f"Error SMTP: {e}"}
    except Exception as e:
        return {"ok": False, "skipped": False, "mensaje": f"Error inesperado: {e}"}


def _build_plain(accuracy: dict) -> str:
    """Versión texto plano del reporte (fallback para clientes sin HTML)."""
    n30   = accuracy.get("n_30", 0)
    mae30 = accuracy.get("mae_30")
    mae60 = accuracy.get("mae_60")
    bias30 = accuracy.get("bias_30")
    bias60 = accuracy.get("bias_60")
    pct30  = accuracy.get("pct_dentro_20_30")
    pct60  = accuracy.get("pct_dentro_20_60")
    tend   = accuracy.get("tendencia", "insuficiente")
    fecha  = datetime.now().strftime("%d/%m/%Y %H:%M")

    def _f(v, u="", d=1):
        return f"{v:.{d}f}{u}" if v is not None else "—"

    return f"""REPORTE SEMANAL — DIABETES TRACKER
{fecha}

Tendencia: {tend.upper()}

PRECISIÓN A +30 MIN ({n30} predicciones evaluadas)
  MAE:              {_f(mae30, ' mg/dL')}
  RMSE:             {_f(accuracy.get('rmse_30'), ' mg/dL')}
  Dentro de ±20:    {_f(pct30, '%', 0)}
  Bias:             {_f(bias30, ' mg/dL')}

PRECISIÓN A +60 MIN ({accuracy.get('n_60', 0)} predicciones evaluadas)
  MAE:              {_f(mae60, ' mg/dL')}
  RMSE:             {_f(accuracy.get('rmse_60'), ' mg/dL')}
  Dentro de ±20:    {_f(pct60, '%', 0)}
  Bias:             {_f(bias60, ' mg/dL')}

Glosario:
  MAE  = Error Absoluto Medio. Bueno si < 12 mg/dL, aceptable < 20.
  Bias = Error sistemático. Se corrige automáticamente en las predicciones.

--
Generado automáticamente por Diabetes Tracker cada lunes 9am.
"""
