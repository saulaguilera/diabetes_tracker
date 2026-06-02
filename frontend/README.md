# Orbit Copilot — frontend (React + Vite)

Frontend del **producto** (paciente). El backend sigue siendo Flask; esto solo
es la cara nueva. **No muestra predicciones** — solo estado presente y datos
retrospectivos. Las predicciones viven en el track de research (páginas Jinja).

## Prerrequisito: Node.js

Esta máquina **no tiene Node instalado**. Instalalo antes de correr nada:

- macOS (Homebrew):  `brew install node`
- o descargá el LTS de https://nodejs.org

Verificá: `node --version` (≥18) y `npm --version`.

## Desarrollo

```bash
# 1) backend Flask corriendo aparte (otra terminal), en el puerto 5050:
#    python app.py    → http://localhost:5050
# 2) frontend:
cd frontend
npm install        # solo la primera vez
npm run dev        # → http://localhost:5173
```

En dev, Vite reenvía las llamadas `/api/*` a Flask (`localhost:5050`) — ver
`vite.config.js`. Así la cookie de sesión funciona igual que en producción.

## Build (producción)

```bash
cd frontend
npm run build      # compila a ../static/copilot/
```

El build cae en `static/copilot/`, que Flask sirve. Para empezar conviene
**commitear** `static/copilot/` (build manual) en vez de compilar en Railway.

## Servir desde Flask (paso siguiente — todavía NO hecho)

Cuando se cablee, en `app.py` se agrega una ruta para servir el SPA:

```python
@app.route("/copilot")
@app.route("/copilot/<path:path>")
def copilot_spa(path=""):
    import os
    root = os.path.join(app.static_folder, "copilot")
    target = os.path.join(root, path)
    if path and os.path.isfile(target):
        return send_from_directory(root, path)
    return send_from_directory(root, "index.html")  # fallback SPA
```

(Requiere login igual que el resto; los assets resuelven bajo `/copilot/`
porque `vite.config.js` usa `base: '/copilot/'`.)

## Estructura

```
frontend/
├── index.html              entry de Vite
├── vite.config.js          base /copilot/, outDir ../static/copilot, proxy /api
├── src/
│   ├── main.jsx            monta <App/>
│   ├── index.css           estilos globales (fondo, grano, animaciones)
│   ├── theme.js            PAL (paleta) + makeTheme(dark) + SANS
│   ├── api.js              cliente fetch a /api/copilot/*
│   ├── App.jsx             shell: fondo + nav + pantalla activa
│   ├── components/
│   │   ├── NebulaGuide.jsx guías-nebulosa (las 5 formas de marca)
│   │   ├── Starfield.jsx   campo de estrellas
│   │   └── BottomNav.jsx   navegación inferior (5 tabs)
│   └── screens/            Hoy · Patrones · Registro · Copiloto · Perfil
│       └── (placeholders — se cablean uno por uno)
```

## Estado actual

Andamiaje: shell navegable con las 5 pantallas como **placeholders** (cada una
muestra su guía-nebulosa). Sin datos cableados todavía. Próximo paso:
endpoint `GET /api/copilot/home` + portar la pantalla **Hoy** con datos reales.
