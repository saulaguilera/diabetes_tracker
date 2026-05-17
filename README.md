# DiabetesTracker

Aplicación web personal para el manejo de diabetes tipo 1, con sincronización con FreeStyle Libre, predicción de glucosa, cálculo de dosis y análisis de patrones.

Desplegada en [Railway](https://railway.app) con PostgreSQL.

---

## Características principales

### Monitoreo de glucosa
- Sincronización automática con **FreeStyle Libre** via LibreView LinkUp
- Dashboard en tiempo real con glucemia actual, tendencia (ROC) y flecha de dirección
- Gráficos interactivos (Plotly) de glucosa, IOB, COB e insulina
- Perfil ambulatorio de glucosa (AGP)

### Predicción de glucosa
- Predicción a 30/60/90 minutos con intervalo de confianza (Monte Carlo)
- Modelo que integra: IOB bolus + IOB basal + COB + grasa/proteína + ejercicio + fenómeno del alba
- Fenómeno del alba personalizado: estimación automática desde datos históricos del CGM

### Calculadora de dosis
- Sugerencia de bolo corrección + comida
- ISF circadiano: sensibilidad variable por bloque horario (cada 4h)
- Ajuste automático por ejercicio reciente (+/- sensibilidad)
- Recomendación de bolo extendido para comidas con alto contenido de grasa/proteína
- Deducción correcta de IOB de bolus activo (basal excluida intencionalmente)

### Insulina activa (IOB)
- IOB de bolus (insulina rápida): modelo biexponencial con pico
- IOB basal: lectura desde registros reales de InsulinDose, soporte para Glargina / Toujeo / Degludec / Detemir / NPH
- Modo híbrido: basal entra en predicción solo en las primeras 4h post-inyección

### Registro rápido (QuickLog)
- Registro simultáneo de glucemia, comida, insulina y actividad en una sola pantalla
- Campo inteligente: parsea cantidades del texto ("manzana 150g", "1/4 taza arroz", "2 cucharadas")
- Estimación automática de macros + índice glucémico desde base nutricional interna y OpenFoodFacts
- Sugerencia de bolo en tiempo real mientras se registra la comida

### Comidas y nutrición
- Registro de comidas con carbohidratos, proteína, grasa, fibra e índice glucémico por ingrediente
- Base de alimentos con búsqueda y estimación automática de macros
- Absorción diferenciada: carbohidratos simples vs complejos vs grasa/proteína

### Actividad física
- Clasificación automática: aeróbico / anaeróbico / mixto por nombre de actividad
- Ajuste de sensibilidad a la insulina según tipo, intensidad y tiempo transcurrido
- Deportes de conjunto (tenis, basket, fútbol) clasificados como mixto

### Análisis y reportes
- Reporte semanal de precisión del modelo por email (APScheduler, lunes 9am)
- Análisis de patrones: hipoglucemias por horario, correlación ejercicio-glucosa
- Diagnóstico completo del modelo: desglose de ISF, ICR, IOB, COB, ejercicio
- Exportación PDF

### Configuración
- ISF, ICR, objetivo glucémico, DIA configurables manualmente o estimados desde datos reales
- Estimación automática de DIA desde datos históricos
- Tipo de insulina basal configurable (afecta ventana de IOB)

---

## Stack técnico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.12, Flask 3.1 |
| Base de datos | PostgreSQL (Railway) / SQLite (local) |
| ORM | Flask-SQLAlchemy |
| Gráficos | Plotly, Matplotlib |
| Modelos | NumPy, Pandas, Kalman filter, Monte Carlo |
| Scheduler | APScheduler (cron jobs) |
| Deploy | Railway, Gunicorn |
| CGM | FreeStyle Libre via LibreView LinkUp API |

---

## Estructura del proyecto

```
diabetes_tracker/
├── app.py                  # Configuración principal, scheduler, auth
├── models.py               # Modelos SQLAlchemy
├── helpers.py              # Funciones compartidas (ISF, ICR, settings)
├── blueprints/
│   ├── sync.py             # Sync LibreView, predicción, calculadora, diagnóstico
│   ├── herramientas.py     # QuickLog, configuración, calculadora ISF
│   ├── glucemia.py         # Registro manual de glucemia
│   ├── insulina.py         # Registro de dosis
│   ├── comidas.py          # Registro de comidas
│   ├── actividad.py        # Registro de actividad física
│   ├── alimentos.py        # Base de alimentos, estimación de macros
│   ├── patrones.py         # Análisis de patrones
│   ├── reportes.py         # Reportes y configuración API
│   └── backup.py           # Backup / restore
└── utils/
    ├── kinetics.py         # IOB, COB, ROC, ejercicio, dawn phenomenon
    ├── charts.py           # Gráficos Plotly
    ├── ar_model.py         # Modelo AR de predicción de glucosa
    ├── monte_carlo.py      # Intervalos de confianza
    ├── kalman.py           # Filtro de Kalman para suavizado de CGM
    ├── nutrition_db.py     # Base nutricional interna
    ├── libre_linkup.py     # Cliente LibreView LinkUp
    ├── email_notifier.py   # Reportes semanales por email
    └── recommendations.py  # Motor de recomendaciones
```

---

## Variables de entorno (Railway)

| Variable | Descripción |
|---|---|
| `DATABASE_URL` | URL de PostgreSQL (Railway la inyecta automáticamente) |
| `SECRET_KEY` | Clave secreta de Flask |
| `APP_PASSWORD` | Contraseña de acceso a la app |
| `SYNC_TOKEN` | Token para endpoints de sincronización sin sesión |
| `LIBRE_USERNAME` | Email de LibreView |
| `LIBRE_PASSWORD` | Contraseña de LibreView |
| `NOTIFY_EMAIL` | Email destino para reportes semanales |
| `SMTP_HOST` | Servidor SMTP (ej: smtp.gmail.com) |
| `SMTP_PORT` | Puerto SMTP (587 para TLS) |
| `SMTP_USER` | Usuario SMTP |
| `SMTP_PASSWORD` | Contraseña SMTP o App Password |
| `TZ` | Zona horaria del servidor (ej: `America/Santiago`) |

---

## Deploy en Railway

1. Fork o clona el repositorio
2. Crea un nuevo proyecto en [Railway](https://railway.app)
3. Agrega un servicio PostgreSQL y uno de Python
4. Configura las variables de entorno listadas arriba
5. Railway detecta el `Procfile` y despliega automáticamente

```
# Procfile
web: gunicorn app:app
```

---

## Desarrollo local

```bash
# Clonar
git clone https://github.com/tu-usuario/diabetes_tracker.git
cd diabetes_tracker

# Entorno virtual
python -m venv venv
source venv/bin/activate

# Dependencias
pip install -r requirements.txt

# Variables de entorno (crear .env)
cp .env.example .env
# Editar .env con tus credenciales

# Inicializar base de datos
flask shell
>>> from app import db; db.create_all()

# Correr
flask run
```

---

## Notas de diseño

**Timestamps**: Todos los timestamps se almacenan en hora local del servidor (`TZ=America/Santiago`). No se usa UTC para simplificar la visualización sin conversiones.

**IOB basal vs bolus**: La insulina basal (Toujeo, Glargina, etc.) se muestra en el IOB total pero **no se resta** del bolo sugerido en la calculadora. La basal cubre la producción hepática de glucosa, no el exceso que se corrige con insulina rápida.

**Fenómeno del alba**: Modelo gaussiano centrado en 05:30, estimado automáticamente desde los datos del CGM cada 24h. Se agrega como componente independiente en la predicción (sin supresión por COB).

---

## Licencia

Uso personal. No apto para decisiones clínicas sin supervisión médica.
