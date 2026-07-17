//  OrbitDriveActivityManager.swift
//  ORBIT Drive Mode — control de la Live Activity (start / update / end).
//
//  Vive en el target de la APP (no en la extensión). Convierte el estado JSON
//  que manda la web (payload de /api/copilot/drive) en ContentState y maneja el
//  ciclo de vida de la Live Activity con ActivityKit.
//
//  DISEÑO ANTI-DUPLICADOS (auditoría 2026-07-15; el dedup anterior falló en
//  CarPlay al abrir/cerrar Drive varias veces):
//   1. CADENA SERIAL: start/update/end se ejecutan en orden de llamada dentro
//      de una sola cadena de Tasks. Antes cada una disparaba su propio Task
//      sin orden — un end() viejo podía ejecutarse DESPUÉS del start() nuevo
//      y matar lo recién creado, o dejar dos actividades vivas.
//   2. ESPERA A ACTIVITYKIT: al relanzar la app, Activity.activities tarda
//      unos cientos de ms en poblarse. Si UserDefaults dice que dejamos una
//      actividad viva, start() espera (hasta ~1.5 s) a que aparezca antes de
//      decidir crear otra.
//   3. DEDUP INLINE + BARRIDO: start() adopta la primera y termina el resto
//      AHÍ MISMO; y 2 s después de crear, un barrido mata cualquier
//      no-administrada que haya aparecido tarde. Nada depende del poll de
//      30 s del WebView (que muere al bloquear el teléfono — el caso CarPlay).

import ActivityKit
import Foundation

@available(iOS 16.2, *)
enum OrbitDriveActivityManager {

    private static let title = "ORBIT Drive"
    // Si no llega update en este tiempo, iOS marca la actividad como "stale".
    private static let staleAfter: TimeInterval = 15 * 60
    // Flag: dejamos una actividad viva (sobrevive al kill del proceso).
    private static let flagKey = "orbit_drive_active"

    /// Callback al plugin cuando ActivityKit emite un push token (hex).
    static var onPushToken: ((String) -> Void)?

    /// id de la actividad administrada por esta sesión.
    private static var managedId: String?
    /// ids con observer de push token ya montado (evita observers duplicados).
    private static var observedIds = Set<String>()
    /// Cadena serial: cada operación espera a que termine la anterior.
    private static var chain: Task<Void, Never> = Task {}

    static func isAvailable() -> Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    private static func encolar(_ op: @escaping () async -> Void) {
        let previa = chain
        chain = Task {
            await previa.value
            await op()
        }
    }

    private static func actividades() -> [Activity<OrbitDriveActivityAttributes>] {
        Activity<OrbitDriveActivityAttributes>.activities
    }

    /// Espera (hasta ~1.5 s) a que ActivityKit surfa las actividades vivas
    /// tras un relanzamiento. Solo espera si el flag dice que dejamos una.
    private static func esperarActividades() async -> [Activity<OrbitDriveActivityAttributes>] {
        var all = actividades()
        if all.isEmpty && UserDefaults.standard.bool(forKey: flagKey) {
            for _ in 0..<10 {
                try? await Task.sleep(nanoseconds: 150_000_000)
                all = actividades()
                if !all.isEmpty { break }
            }
        }
        return all
    }

    /// Termina todas las actividades salvo la administrada (dedup inline).
    private static func terminarNoAdministradas() async {
        for a in actividades() where a.id != managedId {
            NSLog("OrbitDrive: terminando actividad duplicada \(a.id)")
            await a.end(nil, dismissalPolicy: .immediate)
        }
    }

    /// Inicia la Live Activity: adopta la existente (re-registrando su push
    /// token) o crea una nueva, y elimina cualquier duplicada en el acto.
    static func start(_ state: OrbitDriveActivityAttributes.ContentState) {
        guard isAvailable() else { return }
        encolar {
            let all = await esperarActividades()
            let content = ActivityContent(state: state,
                                          staleDate: Date().addingTimeInterval(staleAfter))
            if let existing = all.first {
                managedId = existing.id
                adopt(existing)
                await terminarNoAdministradas()
                await existing.update(content)
                UserDefaults.standard.set(true, forKey: flagKey)
                return
            }
            do {
                // Con push: permite updates por APNs con la app en background.
                let activity = try Activity.request(attributes: OrbitDriveActivityAttributes(title: title),
                                                    content: content, pushType: .token)
                managedId = activity.id
                adopt(activity)
            } catch {
                // Sin entitlement de push (cuenta gratis) → modo local.
                NSLog("OrbitDrive: pushType .token falló (\(error)) — reintento local")
                if let activity = try? Activity.request(attributes: OrbitDriveActivityAttributes(title: title),
                                                        content: content, pushType: nil) {
                    managedId = activity.id
                } else {
                    NSLog("OrbitDrive: no se pudo iniciar Live Activity")
                    return
                }
            }
            UserDefaults.standard.set(true, forKey: flagKey)
            // Barrido: si una actividad vieja aparece tarde en la lista
            // (launch race), matarla aunque el WebView ya esté congelado.
            encolarBarrido()
        }
    }

    private static func encolarBarrido() {
        Task {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            encolar { await terminarNoAdministradas() }
        }
    }

    /// Adopta una actividad: re-emite su push token actual (el backend pudo
    /// haberlo perdido o pisado) y observa futuros cambios, una sola vez.
    private static func adopt(_ activity: Activity<OrbitDriveActivityAttributes>) {
        if let data = activity.pushToken {
            let hex = data.map { String(format: "%02x", $0) }.joined()
            NSLog("OrbitDrive: re-registrando push token (\(hex.prefix(12))…)")
            onPushToken?(hex)
        }
        guard !observedIds.contains(activity.id) else { return }
        observedIds.insert(activity.id)
        Task {
            for await tokenData in activity.pushTokenUpdates {
                let hex = tokenData.map { String(format: "%02x", $0) }.joined()
                NSLog("OrbitDrive: push token actualizado (\(hex.prefix(12))…)")
                onPushToken?(hex)
            }
        }
    }

    /// Actualiza la actividad administrada; de paso, dedup de cortesía.
    static func update(_ state: OrbitDriveActivityAttributes.ContentState) {
        let content = ActivityContent(state: state,
                                      staleDate: Date().addingTimeInterval(staleAfter))
        encolar {
            let all = actividades()
            let keepId = managedId ?? all.first?.id
            for activity in all {
                if activity.id == keepId {
                    await activity.update(content)
                } else {
                    NSLog("OrbitDrive: terminando actividad duplicada \(activity.id)")
                    await activity.end(nil, dismissalPolicy: .immediate)
                }
            }
        }
    }

    /// Termina la Live Activity (al salir de Drive Mode). Encadenado: si hay
    /// un start() posterior en la cola, corre DESPUÉS y parte limpio.
    static func end() {
        encolar {
            managedId = nil
            UserDefaults.standard.set(false, forKey: flagKey)
            for activity in actividades() {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }
    }
}
