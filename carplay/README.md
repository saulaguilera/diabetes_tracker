# ORBIT Drive Mode — escena CarPlay (scaffold)

Código nativo Swift para mostrar Drive Mode en **CarPlay**. Reusa el contrato
`GET /api/copilot/drive` (mismo `DriveModeState` que la web). La escena solo
**renderiza** — sin lógica, sin predicción, sin dosis.

> `mobile/ios/` está gitignored (Capacitor lo regenera), por eso el scaffold vive
> acá. Copiá estos `.swift` al proyecto Xcode para integrarlos.

## Archivos
- `DriveModeClient.swift` — fetch + decode de `/api/copilot/drive`.
- `CarPlaySceneDelegate.swift` — escena CarPlay con `CPListTemplate`.

## Requisitos (sin esto, no compila/aparece en CarPlay)
1. **Cuenta Apple Developer de pago** (US$99/año).
2. **Entitlement de CarPlay aprobado por Apple**: `com.apple.developer.carplay-audio`
   (ver `ENTITLEMENT_REQUEST.md`). Es el único paso con tiempo incierto.
3. Distribución por **TestFlight/App Store** para release.

## Integración en Xcode (cuando tengas 1–2)
1. **Copiar** `DriveModeClient.swift` y `CarPlaySceneDelegate.swift` a
   `mobile/ios/App/App/` y agregarlos al target **App**.

2. **Entitlement** — en `App.entitlements`:
   ```xml
   <key>com.apple.developer.carplay-audio</key>
   <true/>
   ```

3. **Info.plist** — declarar la escena CarPlay (la app Capacitor sigue usando su
   `AppDelegate` para la ventana normal; CarPlay corre como escena aparte):
   ```xml
   <key>UIApplicationSceneManifest</key>
   <dict>
     <key>UIApplicationSupportsMultipleScenes</key>
     <true/>
     <key>UISceneConfigurations</key>
     <dict>
       <key>CPTemplateApplicationSceneSessionRoleApplication</key>
       <array>
         <dict>
           <key>UISceneConfigurationName</key>
           <string>ORBIT CarPlay</string>
           <key>UISceneClassName</key>
           <string>CPTemplateApplicationScene</string>
           <key>UISceneDelegateClassName</key>
           <string>$(PRODUCT_MODULE_NAME).CarPlaySceneDelegate</string>
         </dict>
       </array>
     </dict>
   </dict>
   ```

4. **Auth** (clave): la escena CarPlay no comparte la cookie del WebView.
   Camino recomendado: que el backend acepte un **token de solo-lectura** en
   `/api/copilot/drive` (header `Authorization: Bearer …`), guardado en
   Keychain/App Group por la app tras login, y seteado en
   `DriveModeClient.authToken`. (Backend: agregar token-auth a ese endpoint —
   cambio chico, lo hacemos cuando avancemos.)

## Probar
- **Simulador CarPlay**: Xcode → corré la app en el Simulador → menú
  **I/O → External Displays → CarPlay**. (Requiere el entitlement en el
  provisioning; con la cuenta de pago + entitlement de desarrollo se puede probar
  antes de publicar.)
- **Auto real**: build firmado con el entitlement, conectar el iPhone a CarPlay.

## Notas de diseño
- `CPListTemplate` está disponible para la categoría `audio` (la puerta típica de
  apps de CGM a CarPlay). El número grande va como header de sección.
- Refresco cada 30s (CarPlay limita updates). Safety-first: mensajes cortos, sin
  dosis ni predicción. Si no hay datos confiables → "Sin conexión".
- Futuro: variante `CPNowPlayingTemplate` + lectura por voz (estilo Sugarmate).
