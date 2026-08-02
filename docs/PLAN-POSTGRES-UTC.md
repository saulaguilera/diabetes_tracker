# Plan: Postgres + timestamps UTC

> Migración de Orbit de SQLite (volumen Railway) a Postgres gestionado, con
> normalización de TODOS los timestamps a UTC. Pensado para ejecutarse en un
> fin de semana tranquilo, con la SQLite intacta como red de seguridad.

---

## 1. Contexto y porqué

**Tres bugs de relojes cruzados en una semana.** Con usuarios en 3 zonas
horarias distintas y el server clavado en `America/Santiago` (app.py fuerza
`TZ` al arrancar), la convención actual — "todo naive, en la hora local de
alguien" — ya mordió tres veces:

1. El cooldown del rate-limit de Libre mostraba «Espera 45m» siendo 10 el
   tope: el marcador se guardaba en ISO de reloj de pared y se comparaba
   contra el reloj de otro (documentado en `_rate_limit_wait`,
   `blueprints/sync.py`). Se arregló pasándolo a época Unix.
2. Lecturas "viejas" para un usuario de viaje: una lectura fresca en Londres
   se veía con horas de atraso (hoy cubierto por
   `tests/test_user_tz.py::test_lectura_fresca_en_londres_se_ve_fresca`).
3. El `FactoryTimestamp` de Libre (UTC del sensor) convertido a la zona del
   teléfono producía horas distintas según desde dónde escaneabas
   (`tests/test_libre_tz.py`).

Cada fix fue un parche local. La causa raíz es la misma: **la DB no declara
en qué zona está cada valor**. Peor aún, hoy conviven zonas mezcladas *en la
misma fila*: los `created_at` usan `default=datetime.utcnow` (models.py → ya
son UTC) mientras los timestamps de evento (`timestamp`, `assessed_at`,
`predicted_at`…) se escriben con `ahora_usuario()` / `datetime.now()` (hora
local de alguien). Razonar sobre eso es imposible; auditarlo, más.

**SQLite se queda chica.** SQLite tiene UN writer a la vez. Hoy conviven el
cron de sync (cada pocos minutos, por usuario), los requests de la app, el
resolver de predicciones, el backup diario y el panel admin. Cerca de ~10
usuarios activos el lock de escritura empieza a producir `database is locked`
y latencia visible. Postgres da concurrencia real, backups gestionados por
Railway y tipos de fecha decentes. El backup cifrado a S3
(`utils/db_backup.py`) hizo su trabajo mientras duró la etapa SQLite; con
Postgres pasa a ser cinturón y tirantes.

---

## 2. Decisión de diseño

**Todo UTC en la DB. Conversión a la zona del usuario SOLO en los bordes.**

- Los timestamps se guardan **naive en UTC** (columnas `db.DateTime` como
  hoy; cambia la convención, no el tipo — mínimo diff, y los defaults
  `datetime.utcnow` de models.py quedan correctos tal cual).
- La zona del usuario (`tz_usuario()`, setting `tz` auto-capturada del header
  `X-Orbit-TZ`) se aplica exactamente en dos bordes:
  1. **Serialización**: al renderizar templates, armar JSON para la app, PDFs,
     charts y el contexto en lenguaje natural que ve el LLM del copiloto.
  2. **Ventanas de decisión**: todo lo que pregunta "¿qué hora era PARA EL
     USUARIO?" — hipos nocturnas (00–06), fenómeno del alba (3–9 am), bloques
     circadianos ISF/ICR (`hora // 4`), la ventana del daily brief, el corte
     de "día". Se convierte el instante UTC a hora local del dueño *antes* de
     sacar `.hour` o `.date()`.
- **Los timers siguen en época Unix.** `libre_rate_limited_at`, tokens
  APNs/FCM, duraciones de bench: un cooldown es un timer, no una hora de
  pared. Ya son inmunes; no se tocan.
- La invariante nueva reemplaza a la vieja de `ahora_usuario()`: antes era
  "timestamps del usuario y su *ahora* en la MISMA zona (la suya)"; ahora es
  "timestamps y *ahora* en la MISMA zona (UTC), y la zona del usuario es un
  asunto de presentación". Las edades y ventanas relativas siguen dando bien
  — que era lo que la invariante vieja protegía — y además dos usuarios en
  zonas distintas pueden compartir DB sin pisarse.

En `helpers.py` nace:

```python
def ahora_utc():           # reemplaza ahora_usuario() como "ahora" canónico
    return datetime.now(timezone.utc).replace(tzinfo=None)

def a_hora_usuario(dt):    # borde de salida (naive UTC → naive local del usuario)
def a_utc(dt_local):       # borde de entrada (forms/parse_datetime → UTC)
```

`ahora_usuario()` **no desaparece**: pasa a significar "ahora en la zona del
usuario, solo para presentación y ventanas de decisión" — que es lo que los
bordes necesitan. Lo que muere es usarlo para *escribir* en la DB o comparar
contra columnas.

---

## 3. Inventario de lo que cambia

### 3.1 Tablas con timestamps a convertir (dato = hora local del dueño)

| Tabla | Tenancy | Columnas a convertir |
|---|---|---|
| `glucose_readings` | TenantScoped | `timestamp`, `corrected_at` |
| `meals` | TenantScoped | `timestamp` |
| `insulin_doses` | TenantScoped | `timestamp` |
| `activities` | TenantScoped | `timestamp` |
| `context_tags` | TenantScoped | `timestamp` |
| `copilot_notifications` | TenantScoped | `created_at` (¡se escribe explícito con hora local en copilot_api!), `read_at` |
| `meal_presets` | TenantScoped | `last_used_at` |
| `cgm_imports` | TenantScoped | `imported_at`, `date_from`, `date_to` |
| `daily_briefs` | TenantScoped | `generated_at` — **`day` NO se toca**: es `db.Date`, un concepto de calendario del usuario, no un instante |
| `glucose_predictions` | global (u1) | `predicted_at` |
| `prediction_audit` | global (u1) | `predicted_at`, `realized_at` |
| `ssm_innovations` | global (u1) | `ts`, `run_at` |
| `pmm_parameters` | global (u1) | `last_updated` |
| `pmm_observations` | global (u1) | `observed_at` |
| `pmm_drift_state` | global (u1) | `drift_since`, `updated_at` |
| `hypo_risk_audit` | global (u1) | `assessed_at`, `projected_trough_time`, `resolved_at`, `actual_hypo_time`, `dismissed_at` |
| `user_settings` | prefijo `u{id}::` | `updated_at` |

Regla del script: la zona del dueño es la del `user_id` de la fila (setting
`u{id}::tz`); filas sin `user_id` (tablas de ciencia PMM/SSM/hypo, todavía
mono-usuario) pertenecen al usuario 1; sin setting → `America/Santiago` (la
zona del server, que fue el reloj por defecto toda la vida del proyecto).

### 3.2 Columnas que YA están en UTC (no convertir)

Los `created_at` con `default=datetime.utcnow` que ningún código escribe a
mano: `users.created_at`, y los `created_at` de glucose_readings, meals,
insulin_doses, activities, context_tags, food_items, meal_presets,
glucose_predictions, tuning_experiments, prediction_audit, ssm_innovations,
pmm_*, hypo_risk_audit, daily_briefs. **La decisión del script es POR
COLUMNA, no por tabla** — este es el detalle que más fácil se arruina.
Excepción confirmada: `copilot_notifications.created_at` (se setea con `now`
local en `blueprints/copilot_api.py:1015`) → sí se convierte.

### 3.3 Settings con ISO de reloj de pared

Guardados vía `_set_setting(..., isoformat())` — hoy en hora local de alguien:

- `libre_last_sync`, `dawn_last_estimated` (zona del usuario, sync.py)
- `sched_last_run`, `sched_monitor_last` (server, sync.py / admin_bp.py)
- `notif_scan_last`, `drive_apns_token_updated_at` (copilot_api.py)
- `backup_last`, `backup_last_ok` (db_backup.py)

Todos son marcadores "última vez que…" que se auto-reescriben en la próxima
corrida y se comparan con tolerancias de minutos u horas. Estrategia barata:
**convertirlos a ISO UTC en el script** (son pocos) y cambiar los 4 puntos de
escritura/lectura a `ahora_utc()`. El peor caso de no migrarlos es un backup
adelantado o un "sync viejo" fantasma de una corrida — aceptable, pero por
prolijidad se migran.

### 3.4 Marcadores en época Unix — ya inmunes, cero cambios

`libre_rate_limited_at` (`str(int(time.time()))`), los caches de token de
`drive_mode/apns_push.py` y `fcm_push.py`, `services/model_health.py`,
cronómetros de bench/LLM. La época no tiene zona: quedan como están y son el
modelo a imitar para todo timer futuro.

### 3.5 Call sites de "ahora" por módulo

Conteo de `datetime.now()` + `ahora_usuario()` (grep 2026-08-02; total 197,
incluye tests y bench):

| Módulo | Sitios | Nota |
|---|---|---|
| `blueprints/copilot_api.py` | 21 | el más delicado: horas dentro de prompts LLM |
| `blueprints/sync.py` | 16 | escritor principal de lecturas |
| `helpers.py` | 11 | núcleo: stats, patrones, ISF/ICR circadiano |
| `utils/copilot_tools.py` | 8 | tools de escritura del copiloto |
| `utils/copilot_memory.py` | 7 | |
| `claude_analisis` / `charts` / `ar_model` / `reportes` / `herramientas` | 6 c/u | mayormente bordes de presentación |
| `db_backup` / `patrones` | 4 c/u | |
| `libre_linkup`, `hypo_risk_engine`, `email_notifier`, `hypo_outcome_tracker`, `pmm/anomaly`, `glucemia`, `comidas`, `backup`, `admin_bp`, `app.py` | 3 c/u | |
| resto (≈20 archivos) | 1–2 c/u | |

Bonus: `utils/libre_linkup.py` se **simplifica** — hoy convierte el
`FactoryTimestamp` UTC del sensor a la zona del usuario para guardarlo; con
la DB en UTC esa conversión se borra y el dato entra tal como viene.

---

## 4. Plan paso a paso (Railway)

### Sábado AM — infraestructura y ensayo en frío

1. **Crear el servicio Postgres** en el proyecto Railway (dashboard → New →
   Database → PostgreSQL). Railway expone `DATABASE_URL` como referencia.
   **No** tocar todavía las variables del servicio web — `app.py:40` ya lee
   `DATABASE_URL` si existe, así que setearla ES el switch.
2. Agregar `psycopg2-binary>=2.9` a `requirements.txt` (deploy sin
   `DATABASE_URL` aún: no cambia nada, pero deja el driver listo).
3. **Copia local de la DB**: forzar `run_backup()` y además bajar
   `diabetes.db` del volumen. Ensayar TODO lo que sigue contra esta copia y
   un Postgres local/de staging antes de tocar producción.

### Sábado PM — script de migración

4. `scripts/migrate_sqlite_to_postgres.py`:
   - Lee la SQLite fuente, apunta SQLAlchemy al `DATABASE_URL` destino,
     `db.create_all()` crea el esquema fresco (idéntico a models.py).
   - Copia tabla por tabla con `execution_options={"all_users": True}` (o
     directamente sin contexto de usuario) para saltar el filtro de tenancy.
   - **Conversión por columna** según el inventario §3.1/§3.2: para cada fila,
     resolver la tz del dueño (`u{user_id}::tz` → fallback
     `America/Santiago`), `localize → astimezone(utc) → naive`. En horas
     ambiguas o inexistentes de DST usar `fold=0` y **loguear la fila** (se
     esperan cero o un puñado; si aparecen cientos, algo está mal).
   - Migrar los settings ISO de §3.3 al vuelo.
   - `setval` de todas las secuencias de PK al máximo id copiado (clásico
     olvido: el primer INSERT post-migración explota con PK duplicada).

### Domingo AM — corte

5. **Pausar escrituras**: desactivar el cron de sync (y avisar que la app
   queda solo-lectura ~30 min). La SQLite no se escribe más: desde este
   momento es la foto de rollback.
6. Correr el script contra producción. Con el ensayo del sábado, esto es
   re-ejecutar algo ya probado.
7. **Verificación (gate para el switch)**:
   - Conteos por tabla SQLite vs Postgres: iguales o no hay switch.
   - Muestreo: N filas al azar por tabla, convertir el valor Postgres de
     vuelta UTC→tz del dueño y comparar contra el original SQLite (ida y
     vuelta exacta).
   - Invariantes clínicas: última lectura de cada usuario con "hace X min"
     sensato; TIR 7d por usuario igual antes/después (misma ventana, mismo
     resultado); el brief de hoy existe y su `day` no se corrió.
8. **Switch**: setear `DATABASE_URL` en el servicio web → redeploy →
   reactivar cron. Si el sync corrió lecturas nuevas a la SQLite entre la
   pausa y el corte (no debería), delta-sync a mano de esas filas.

### Rollback

Quitar `DATABASE_URL` del servicio web y redeployar: la app vuelve a la
SQLite del volumen, **que nadie escribió desde el paso 5**. Costo del
rollback: perder los datos ingresados durante la ventana Postgres (por eso el
switch se hace con el día tranquilo, no un lunes 8 am). El volumen no se
elimina hasta ≥2 semanas de Postgres estable; el backup S3 diario sigue
corriendo mientras tanto (adaptarlo a `pg_dump` es tarea posterior, no
bloqueante).

---

## 5. Orden de los cambios de código

El cambio de código va en una rama y puede empezar ANTES del fin de semana;
lo único acoplado al corte de datos es el deploy. Orden por capas, de adentro
hacia afuera:

1. **Núcleo** (`helpers.py`): `ahora_utc()`, `a_hora_usuario()`, `a_utc()`.
   `parse_datetime()` (forms llegan en hora local del usuario) convierte a
   UTC al final. Los defaults de models.py quedan como están.
2. **Escritores** — todo lo que persiste un instante pasa a UTC:
   `sync.py` + `libre_linkup.py` (borrar la conversión a tz usuario),
   `glucemia`, `comidas`, `insulina`, `actividad`, `copilot_tools`
   (registro por chat), `hypo_risk_engine`, motores PMM, `daily_brief`
   (`generated_at`; el cálculo de `day` usa la ventana local — borde).
3. **Lectores y ventanas de decisión** — convertir a hora local ANTES de
   `.hour`/`.date()`: `_detectar_patrones` (nocturnas 00–06, alba, Somogyi),
   `_calcular_isf_circadiano` / `_calcular_icr_circadiano` (bloque de 4 h),
   `stats_resumen`, ventana del daily brief, detector del alba en sync.
4. **Bordes de presentación**: templates/charts/reportes/PDF con
   `a_hora_usuario()`; JSON de la API con sufijo explícito (`…Z` o offset —
   la app ya manda `X-Orbit-TZ`, sabe pintarlo); `copilot_api` arma el
   contexto del LLM en hora local del usuario (el prompt debe decir la hora
   que el usuario ve en su muñeca) pero calcula edades con UTC.
5. **Settings** ISO → `ahora_utc()` en los 4 archivos de §3.3.
6. **Tests**: `test_user_tz.py` se adapta a la invariante nueva (DB en UTC,
   ventanas en local — los nombres de los tests siguen valiendo, cambia el
   fixture); `test_libre_tz.py` se simplifica; `test_tenancy`,
   `test_observability` y el resto deben pasar sin tocar.

Nota sobre las migraciones inline de `app.py` (bloque `inspector` + `ALTER
TABLE`): en Postgres el esquema nace fresco de `create_all()`, así que esas
ramas no encuentran columnas faltantes y no se ejecutan — quedan como código
muerto inofensivo para la era SQLite. No adaptarlas; el reemplazo real es
Alembic, y es un proyecto aparte (backlog).

---

## 6. Estimación honesta por fase

| Fase | Estimación | Comentario |
|---|---|---|
| Script de migración + ensayo contra copia | 3–4 h | La lógica por-columna y el muestreo de verificación son lo que lleva tiempo |
| Código: núcleo + escritores (capas 1–2) | 4–5 h | sync + libre_linkup se achican; el resto es mecánico |
| Código: ventanas + bordes (capas 3–4) | 5–6 h | Acá viven los bugs sutiles; copilot_api (21 sitios, prompts) es lo más delicado |
| Settings + tests (capas 5–6) | 2–3 h | |
| Corte en producción + verificación | 2–3 h | Ventana de solo-lectura real: < 30 min si el ensayo del sábado salió limpio |
| Buffer (DST, filas huérfanas, sorpresas) | 2 h | Siempre se usa |
| **Total** | **~18–23 h** | Un fin de semana largo trabajado. NO es "una tarde"; sí entra en sábado + domingo sin heroísmo si el código se adelanta en semana |

---

## 7. Riesgos y cómo los detectamos

- **Doble conversión** (mostrar como local algo ya convertido, o guardar
  local creyendo que era UTC): es EL bug de esta clase de migración. Red:
  los tests de regresión de tz existentes (`test_user_tz.py`,
  `test_libre_tz.py`) adaptados a la invariante nueva, más una prueba manual
  de humo: registrar una comida desde el teléfono y verla con la hora
  correcta en home, historial y brief.
- **Ventanas de decisión en UTC por descuido**: hipos "nocturnas" que
  aparecen a mediodía, bloques circadianos corridos 3–4 h. Red:
  `test_ventana_del_brief_sigue_la_zona_del_usuario` + fixture nuevo para
  `_detectar_patrones` con un usuario en zona ≠ servidor.
- **El panel `/admin/estado` como termómetro**: muestra edades ("visto hace
  X min") de sync, monitor externo y backup. Un error de zona no da valores
  raros — da valores corridos EXACTAMENTE ±3, ±4 o ±5 h, que en ese panel
  cantan solas. Primer lugar a mirar el lunes post-migración, y los días de
  cambio de DST de Chile.
- **DST en la migración**: horas ambiguas/inexistentes al localizar. Red: el
  log del script; si el volumen de filas logueadas no es ~0, parar y mirar.
- **Postgres se comporta distinto que SQLite**: booleans reales (no 0/1),
  comparaciones más estrictas, y el pool de conexiones reemplaza al lock
  (configurar `pool_pre_ping` y un `pool_size` acorde a los workers de
  gunicorn). Red: los tests corren también contra un Postgres local antes
  del corte, y Sentry ya está cableado para gritar los errores nuevos.
- **Predicciones/auditoría a caballo del corte**: filas de
  `prediction_audit` sin resolver escritas en hora vieja que se resuelven
  con hora nueva darían innovations absurdas. Red: el script convierte TODO
  (resueltas y pendientes) y el bench (`bench/`) se corre una vez
  post-migración como verificación de que las métricas no saltaron.
- **Rollback ensayado, no teórico**: antes del corte, probar una vez el
  camino de vuelta (quitar `DATABASE_URL` en staging) para que el domingo no
  sea la primera vez.
