# ORBIT — Modelo de negocio, precios y proyección de costos

> Documento de trabajo. Actualizado: 2026-07-07.
> Objetivo: dejar ordenado cómo se monetiza ORBIT, a qué precio, y cuánto cuesta
> realmente la IA por usuario, para poder decidir con números (no con intuición).

---

## 0. Tesis en un párrafo

ORBIT no es "otra app de diabetes". El valor pagable **no es el registro** (eso es
gratis en mil apps y las marcas de sensores lo regalan): es el **copiloto de IA**
que explica, acompaña y educa desde nutrición y endocrinología, con los datos
reales del usuario, sin recetar. Eso es lo que nadie más hace bien hoy — y también
es lo único que tiene costo variable real. Así que la estrategia de precios y la de
costos son **la misma conversación**: cobrar por la IA y controlar el gasto de IA.

**La tensión central que hay que aceptar:** casi todas las apps de diabetes son
gratis o baratísimas porque monetizan por otro lado (tiras reactivas, sensores,
canal clínico). Una suscripción de consumidor de ~$7/mes solo se sostiene si el
copiloto se siente claramente superior a "un chatbot con mis datos". Todo el
producto tiene que empujar hacia ahí.

---

## 1. Modelos de negocio

Tres capas, de menor a mayor esfuerzo de puesta en marcha (y de mayor a menor
disposición a pagar por usuario):

| Modelo | Quién paga | Cómo cobra | Ticket | Esfuerzo | Nota |
|---|---|---|---|---|---|
| **B2C Freemium** | El usuario con T1D | Suscripción mensual/anual | ~$7/mes | Bajo | Ya casi listo técnicamente. Es el "core". |
| **B2C Family / Kids** | El padre/madre/cuidador | Suscripción familiar | ~$12/mes | Medio | Mayor disposición a pagar. "Orbit For Kids" del backlog. |
| **B2B2C Clínicas / RPM** | La clínica / aseguradora | Por-paciente-por-mes, o la clínica reembolsa vía RPM | $10–30/pac/mes | Alto | Aquí está el volumen real en EE.UU. Requiere marco regulatorio. |

### 1.1 B2C Freemium (el motor)
- **Gratis** engancha (registro + sync + dashboard + AGP). El costo de IA acá es ~0.
- **Plus** desbloquea el copiloto completo. Ahí está el margen.
- Conversión esperable de apps de salud: 2–5% gratis→pago. Con un copiloto
  realmente útil se puede apuntar más alto, pero planificar con 3%.

### 1.2 Family / Kids (mayor ticket)
- Los padres de un niño con T1D pagan más y con menos fricción que un adulto por sí
  mismo. Vista de cuidador, múltiples perfiles, alertas compartidas.
- Es el mismo motor de IA con permisos y perfiles encima. **No dupliques backend.**

### 1.3 B2B2C Clínicas / RPM (el escalón grande, más lejano)
- En EE.UU. el monitoreo remoto de pacientes (RPM) es reembolsable por Medicare/
  aseguradoras mediante códigos CPT:
  - **99453** — set-up inicial del dispositivo (una vez)
  - **99454** — provisión del dispositivo + transmisión de datos (cada 30 días)
  - **99457 / 99458** — 20 min de manejo clínico por mes (y cada 20 min extra)
- La clínica factura eso a la aseguradora; ORBIT le cobra a la clínica una fracción
  por paciente. Esto convierte "$7 de un individuo" en "$15–30 por paciente pagados
  por un tercero", que es un negocio distinto de escala.
- **Muro:** para vender a clínicas hay que cruzar la línea regulatoria (software
  como dispositivo médico / SaMD, o quedarse deliberadamente del lado "wellness/
  educación"). Hoy ORBIT está bien parado del lado de **acompañar y describir,
  nunca recetar** — eso es una ventaja regulatoria, pero el canal clínico exige
  formalizarlo. Es una decisión de fase 2, no de ahora.

---

## 2. Precios (tiers B2C)

| Tier | Precio | Qué incluye |
|---|---|---|
| **Orbit Free** | $0 | Registro, sync con sensor, dashboard clásico, AGP, estadísticas básicas, export PDF básico. Copiloto **limitado** (p.ej. 5 mensajes/mes, sin brief diario, sin foto). |
| **Orbit Plus** | **$6.99/mes** o **$59/año** (−30%) | Copiloto completo (chat con tus datos + dos miradas), **brief diario**, **estimación por foto**, informe PDF para el médico, memoria del copiloto, etiquetas de contexto, mg/dL↔mmol/L, multi-idioma. |
| **Orbit Family** | **$11.99/mes** o **$99/año** | Todo Plus + hasta 4 perfiles, vista de cuidador, alertas compartidas. |

**Notas de pricing:**
- El anual es clave: baja el churn y cobra por adelantado (mejora el flujo de caja
  y te da margen para el costo de IA de todo el año).
- El límite del tier gratis (5 mensajes, sin foto/brief) **no es tacañería: es
  control de COGS.** El costo variable vive casi todo en el copiloto; el gratis
  tiene que costar ~$0.
- App Store se queda ~15–30% de la suscripción (15% en el plan para PYMEs de Apple,
  <$1M/año). Ese corte hay que restarlo del ingreso neto en toda proyección.

---

## 3. Proyección de costos de IA (el corazón del documento)

### 3.1 Qué llama a la API, y con qué modelo

| Función | Modelo | Entrada aprox. | Salida aprox. | Frecuencia |
|---|---|---|---|---|
| Chat del copiloto | **Sonnet 5** | ~3–8k tok (contexto + herramientas, varias rondas) | ~700–900 tok | por mensaje |
| Brief diario | **Sonnet 5** | ~2k tok | ~450 tok | 1×/día |
| Estimación por foto | **Sonnet 5 (visión)** | ~5k tok (imagen domina) | ~800 tok | por foto |
| Traducción de patrones | **Haiku 4.5** | pequeño, **cacheado** | pequeño | ocasional |

**Precios usados** (API Anthropic, por 1M tokens):
- Sonnet 5: **$3.00 entrada / $15.00 salida** (intro $2/$10 hasta 31-ago-2026)
- Haiku 4.5: **$1.00 / $5.00**

> Proyecto con el precio **estándar** ($3/$15) para ser conservador; hoy con el
> intro real ($2/$10) los números de abajo son **~⅓ más baratos**.

### 3.2 Costo por interacción (unidad)

| Interacción | Costo estimado | Comentario |
|---|---|---|
| 1 mensaje de chat | **$0.02 – $0.04** | El contexto + el bucle de herramientas es lo caro. El *prompt caching* del system prompt lo baja ~a la mitad. |
| 1 brief diario | **$0.01 – $0.015** | Barato, predecible. |
| 1 estimación por foto | **$0.02 – $0.03** | La imagen manda en tokens de entrada. **Es la función más cara por llamada.** |
| 1 traducción de patrón | **~$0.001** | Haiku + caché por hash. Despreciable. |

### 3.3 Costo mensual por usuario (según intensidad de uso)

Supuestos de uso y el COGS de IA resultante (precio estándar $3/$15):

| Perfil | Chat/mes | Foto/mes | Brief/mes | **COGS IA/mes** |
|---|---|---|---|---|
| **Ligero** | ~15 | ~9 | 30 | **~$0.60** |
| **Típico (Plus)** | ~30 | ~15 | 30 | **~$1.60** |
| **Intensivo** | ~90 | ~60 | 30 | **~$4.50** |

**COGS de IA mezclado por usuario pagador ≈ $1.5 – $2.5/mes** (mezcla de ligeros y
típicos, con pocos intensivos). Con el precio intro y con caché agresivo, más cerca
de **$1 – $1.5**.

### 3.4 Las tres palancas que definen el margen

1. **Prompt caching.** El system prompt (candados, marco de dos miradas) es estable
   y grande → cachearlo hace que sus tokens cuesten ~0.1×. Es la palanca #1 y ya
   tenemos el system prompt frozen, así que es casi gratis de implementar.
2. **Metering de la foto/visión.** Es la interacción más cara. Cap de fair-use en
   Plus (p.ej. 5 fotos/día) protege el margen contra el usuario intensivo sin
   molestar a nadie normal.
3. **Modelo correcto por tarea.** El chat y el brief necesitan Sonnet. La traducción
   de patrones ya va en Haiku (5× más barato). No subir de modelo "por las dudas".

---

## 4. Márgenes y break-even

### 4.1 Unidad económica (Orbit Plus, $6.99/mes)

| Concepto | Monto |
|---|---|
| Ingreso bruto | $6.99 |
| − Corte App Store (15%) | −$1.05 |
| = Ingreso neto | **$5.94** |
| − COGS IA (mezclado) | −$2.00 |
| − Infra por usuario (a escala) | −$0.20 |
| = **Margen de contribución** | **~$3.74 (≈ 63%)** |

El margen es sano. **El riesgo no es la unidad económica: es el usuario intensivo**
(que se come el margen) y la **conversión/CAC** (que es lo que de verdad decide si
el negocio existe). Por eso el fair-use cap y el tier gratis limitado.

### 4.2 Costos fijos (cash real, hoy)

| Concepto | Costo |
|---|---|
| Railway (hosting) | ~$5–20/mes según carga |
| Apple Developer | $99/año ≈ $8.25/mes |
| Dominio | ~$1/mes |
| **Total fijo** | **~$15–30/mes** |

*(Tu tiempo no está contado como costo de caja.)*

### 4.3 Proyección a escala

Con margen de contribución ~$3.74/usuario pagador y fijo ~$30/mes:

| Usuarios pagos | Ingreso neto/mes | COGS IA/mes | **Margen bruto/mes** |
|---|---|---|---|
| 10 | $59 | ~$20 | **~$9** (apenas cubre lo fijo) |
| 100 | $594 | ~$200 | **~$364** |
| 1.000 | $5.940 | ~$2.000 | **~$3.740** |
| 10.000 | $59.400 | ~$20.000 | **~$37.400** |

**Break-even de costos fijos: ~7–8 usuarios pagos.** O sea, el negocio es viable
técnica y económicamente desde muy chico. Lo caro no es servir usuarios, es
**conseguirlos** (marketing/CAC) — ese es el número a vigilar de verdad.

---

## 5. Benchmarks (por qué los demás cobran lo que cobran)

| App | Precio | Cómo monetiza de verdad |
|---|---|---|
| mySugr (Roche) | Free / Pro ~$3/mes | En realidad monetiza **tiras reactivas** y el ecosistema Roche. |
| Sugarmate (Dexcom) | Free | Es un **gancho del sensor** Dexcom, no un producto pago. |
| Gluroo | Free / premium | Enfoque cuidadores/familia; monetización temprana. |
| Undermyfork | ~$?/mes | Nicho foto+glucosa, base chica. |

**Lectura:** el mercado entrenó a la gente a **no pagar** por una app de diabetes,
porque los grandes la subsidian con hardware. ORBIT no vende hardware, así que
**solo puede cobrar por inteligencia**. El precio de $7 solo funciona si el copiloto
se siente como "tener un educador en diabetes en el bolsillo", no como un chatbot.
Ese es el listón de producto, y es exactamente donde hemos estado invirtiendo.

---

## 6. Riesgos y palancas

- **Regulatorio (el más importante).** Mantener la línea "describe/acompaña/educa,
  nunca receta" es a la vez un principio de seguridad **y** una ventaja de negocio:
  te deja en "wellness/educación" (barra regulatoria baja) hasta que decidas cruzar
  a SaMD para el canal clínico. No cruzar esa línea por accidente.
- **COGS del usuario intensivo.** Mitigado con fair-use + caché + modelo correcto.
- **Conversión/CAC.** El verdadero cuello de botella. Es más barato subir conversión
  (mejor onboarding, mejor copiloto) que comprar usuarios.
- **Dependencia de un proveedor de IA.** Todo el valor pagable corre sobre Anthropic.
  Aceptable hoy; a futuro, abstraer la capa de LLM.
- **Datos de salud.** No vender datos, nunca. Es línea roja de confianza y a la vez
  requisito regulatorio.

---

## 7. Recomendación / próximos pasos

1. **Implementar prompt caching** en el system prompt del chat y del brief. Palanca
   de margen #1, esfuerzo bajo, system prompt ya está frozen.
2. **Definir el tier gratis limitado** (5 mensajes/mes, sin foto/brief) — controla
   COGS y crea el gancho a Plus.
3. **Fair-use cap en foto** (p.ej. 5/día en Plus) — protege contra el intensivo.
4. **Cablear StoreKit / suscripciones** (Orbit Plus mensual + anual). Requiere la
   cuenta de Apple ya pagada.
5. **Instrumentar el gasto de IA por usuario** (loggear tokens/costo por request)
   para validar estas proyecciones con datos reales antes de escalar marketing.
6. **Family/Kids** como segundo tier una vez que Plus convierta.
7. **Clínicas/RPM** es fase 2 (requiere formalizar el marco regulatorio) — no
   distraerse con esto todavía.

> **En una línea:** la unidad económica cierra desde el usuario ~8; el trabajo real
> es que el copiloto sea tan bueno que la gente pague por él y lo recomiende. Todo
> lo demás (caché, caps, tiers) es afinar el margen alrededor de eso.
