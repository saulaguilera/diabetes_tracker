# ORBIT Drive Mode — Live Activity (nativo iOS)

Puente nativo entre el **Drive Mode web** y las superficies glanceables de iOS:
**Lock Screen, Dynamic Island, StandBy** (y, a probar, CarPlay).

> ORBIT Drive Mode Live Activity is designed as a Live Activity / widget-first
> experience. It does **not** implement a full CarPlay app and does **not** use
> CarPlay scenes or entitlements. CarPlay visibility depends on iOS support for
> Live Activities on CarPlay-compatible surfaces.

`mobile/ios/` está gitignored (Capacitor lo regenera), por eso el código vive acá.
Copiá los `.swift` al proyecto Xcode.

## Archivos y a qué target va cada uno
| Archivo | Target |
|---|---|
| `OrbitDriveActivityAttributes.swift` | **App _y_ Widget Extension** (compartido) |
| `OrbitDriveLiveActivity.swift` (SwiftUI: Lock Screen + Dynamic Island) | **Widget Extension** |
| `OrbitDriveActivityManager.swift` (start/update/end) | **App** |
| `OrbitDriveActivityPlugin.swift` (bridge Capacitor) | **App** |
| `frontend/src/drive_mode/liveActivityBridge.js` (web) | web (ya en Railway) |

## Integración en Xcode
1. **Crear un Widget Extension** (File → New → Target → *Widget Extension*, con
   "Include Live Activity"). Nombre sugerido: `OrbitDriveWidget`.
2. Agregar los `.swift` a los targets de la tabla. `OrbitDriveActivityAttributes.swift`
   debe estar en **ambos** (marcalo en *Target Membership*).
3. Registrar el widget en el bundle de la extensión:
   ```swift
   @main
   struct OrbitDriveWidgetBundle: WidgetBundle {
       var body: some Widget { OrbitDriveLiveActivity() }
   }
   ```
4. **Info.plist de la APP** — habilitar Live Activities:
   ```xml
   <key>NSSupportsLiveActivities</key>
   <true/>
   ```
5. **Plugin Capacitor**: `OrbitDriveActivityPlugin` usa `CAPBridgedPlugin`
   (Capacitor 6+/8) → se autodescubre al compilarlo en la app. (Para Capacitor ≤5
   agregá un `.m` con `CAP_PLUGIN(OrbitDriveActivityPlugin, "OrbitDriveActivity", …)`.)
6. **NO** agregar entitlements de CarPlay. **NO** usar `CPTemplateApplicationScene`
   ni escenas de CarPlay. Esto es solo Live Activity.

## Cómo la maneja la web (ya cableado)
`frontend/src/drive_mode/DriveModeScreen.jsx`:
- al entrar a Drive Mode → `startDriveActivity(state)`
- en cada refresco (~30s) → `updateDriveActivity(state)`
- al salir → `endDriveActivity()`
- si el plugin no existe (navegador/Railway) → **no-op**, la web sigue igual.

`state` = payload de `GET /api/copilot/drive` (mismo `DriveModeState`).

## Freshness / seguridad
- Datos viejos → muestra **"Data stale"** en gris (nunca "Stable").
- Sin sensor → **"Sensor disconnected"** en gris.
- Glucosa nula → **no** muestra número engañoso (solo el estado). `staleDate` en
  ActivityKit hace que iOS la marque vieja si no llega update en 15 min.

## Updates
Dos caminos, complementarios:
1. **Local (foreground)**: la web dispara `updateDriveActivity` en cada refresco
   (~30s) mientras Drive Mode está abierto. Funciona sin cuenta paga.
2. **APNs (background)** — rama `feat/drive-apns-push`: el backend empuja el
   nuevo estado en cada sync de Libre, aunque el teléfono esté bloqueado o en
   CarPlay. Requiere cuenta de Apple Developer paga.

### Flujo APNs (ya cableado, flag OFF)
nativo: `Activity.request(pushType: .token)` (fallback a `nil` si no hay
entitlement) → `pushTokenUpdates` → evento `drivePushToken` → web →
`POST /api/copilot/drive/push-token` → settings `drive_apns_token`.
backend: hook en el sync de Libre → `drive_mode/apns_push.py` →
APNs `liveactivity` push con el `content-state` (claves = ContentState Codable).

### Activación (cuando la cuenta esté aprobada)
1. developer.apple.com → Certificates → **Keys** → crear clave con
   **Apple Push Notifications service (APNs)** → bajar el `.p8`, anotar
   **Key ID** y **Team ID**.
2. Xcode → target **App** → Signing & Capabilities → **+ Push Notifications**
   (el widget NO la necesita). Team = la cuenta paga, en App y Widget.
3. Railway → variables:
   `DRIVE_APNS_ENABLED=1`, `APNS_TEAM_ID`, `APNS_KEY_ID`,
   `APNS_KEY_P8` (contenido del .p8; admite `\n` escapados o base64),
   `APNS_ENV=sandbox` (builds de Xcode; `production` para TestFlight/App Store).
   Topic default: `com.saulaguilera.orbit2026.push-type.liveactivity`.
4. Rebuild de la app → abrir Drive Mode una vez (registra el token) → los
   updates siguen solos con el teléfono bloqueado.
Con `DRIVE_APNS_ENABLED=0` (default) todo el camino APNs es no-op.

## Probar
1. Rebuild en Xcode (la Live Activity es nativa — Railway solo actualiza la web).
2. Abrir Orbit → tarjeta **"Modo conducción"** → arranca la Live Activity.
3. Verla en **Lock Screen** y **Dynamic Island** (iPhone 14 Pro+).
4. **CarPlay (a probar, no garantizado):** Simulador → I/O → External Displays →
   CarPlay; iniciar la Live Activity; observar si aparece en Home/Dashboard/
   notificaciones de CarPlay. **Documentar el comportamiento.**

## Demo / estados de test
Los 6 estados están en `frontend/src/drive_mode/demoStates.js` (stable 112,
falling 82, urgent low 68, high 210, stale, disconnected). Para probar la Live
Activity con cada uno, se puede pasar cada demo state al plugin desde una build
de debug, o esperar a que el estado real los recorra. Verificar que **ningún**
campo prohibido aparezca (insulina/dosis/IOB/COB/predicción).

## Estado
- Acceptance criteria 1–14, 16–17: cubiertos por este scaffold + el bridge web.
- **15 (Xcode build) y 16 (test en Lock Screen/Dynamic Island):** requieren
  compilar en Xcode (paso nativo). El código está listo para eso.

## ⚠️ Registro del plugin (OBLIGATORIO con Capacitor 8 + SPM)
IMPORTANTE: la carpeta `App` usa referencias clásicas (no sincronizada). Si agregás
un archivo nuevo por fuera de Xcode NO entra al target → el storyboard no encuentra
la clase → **pantalla negra**. Por eso poné la clase dentro de `AppDelegate.swift`
(que ya está en el target App):
```swift
// al final de AppDelegate.swift (que ya importa Capacitor):
class MainViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(OrbitDriveActivityPlugin())
    }
}
```
Y en `Main.storyboard` → view controller class = `MainViewController` (módulo App,
`customModuleProvider="target"`). Rebuild.

--- (enfoque alternativo con archivo separado, NO usar si App no es carpeta sincronizada) ---

Capacitor 8 con SPM **no auto-descubre** plugins locales del app-target. Hay que
registrarlo manualmente (Swift-only, sin `.m` ni bridging header):

1. Agregá `MainViewController.swift` (en este dir) al target **App**:
   ```swift
   class MainViewController: CAPBridgeViewController {
       override func capacitorDidLoad() {
           bridge?.registerPluginInstance(OrbitDriveActivityPlugin())
       }
   }
   ```
2. En `Main.storyboard`, cambiá la clase del view controller de
   `CAPBridgeViewController` (módulo Capacitor) a **`MainViewController`**
   (módulo `App`, `customModuleProvider="target"`).
3. Rebuild. En la consola del webview: `window.Capacitor.Plugins.OrbitDriveActivity`
   debe existir (ya no `undefined`).
