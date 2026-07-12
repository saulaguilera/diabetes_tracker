//  OrbitDriveActivityManager.swift
//  ORBIT Drive Mode — control de la Live Activity (start / update / end).
//
//  Vive en el target de la APP (no en la extensión). Convierte el estado JSON
//  que manda la web (payload de /api/copilot/drive) en ContentState y maneja el
//  ciclo de vida de la Live Activity con ActivityKit.
//
//  Updates: locales (web en foreground) + APNs push token para background.
//  El push token se pide con pushType .token; si el build no tiene el
//  entitlement de push (cuenta gratis / capability sin agregar) el request
//  falla → reintenta con pushType nil y todo sigue funcionando como antes.
//
//  Duplicados: si iOS mata la app con una actividad viva, al relanzar
//  `Activity.activities` puede estar vacía por un instante (carga asíncrona)
//  y un start() crea una SEGUNDA actividad → pantalla "doble" y la vieja se
//  congela (su token ya no está registrado). Por eso: (a) al adoptar una
//  actividad existente se re-emite su push token, y (b) cada update() termina
//  cualquier actividad que no sea la administrada.

import ActivityKit
import Foundation

@available(iOS 16.2, *)
enum OrbitDriveActivityManager {

    private static let title = "ORBIT Drive"
    // Si no llega update en este tiempo, iOS marca la actividad como "stale".
    private static let staleAfter: TimeInterval = 15 * 60

    /// Callback al plugin cuando ActivityKit emite un push token (hex).
    /// El plugin lo reenvía a la web y la web lo registra en el backend.
    static var onPushToken: ((String) -> Void)?

    /// id de la actividad que administra esta sesión — las demás se terminan.
    private static var managedId: String?
    /// ids que ya tienen observer de push token (evita observers duplicados).
    private static var observedIds = Set<String>()

    static func isAvailable() -> Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    static func current() -> Activity<OrbitDriveActivityAttributes>? {
        let all = Activity<OrbitDriveActivityAttributes>.activities
        if let id = managedId, let mine = all.first(where: { $0.id == id }) {
            return mine
        }
        return all.first
    }

    /// Inicia la Live Activity (o adopta la existente si sobrevivió a un
    /// relanzamiento de la app: la actualiza y re-registra su push token).
    @discardableResult
    static func start(_ state: OrbitDriveActivityAttributes.ContentState) -> Bool {
        guard isAvailable() else { return false }
        if let existing = current() {
            managedId = existing.id
            adopt(existing)
            update(state)
            return true
        }
        let attrs = OrbitDriveActivityAttributes(title: title)
        let content = ActivityContent(state: state,
                                      staleDate: Date().addingTimeInterval(staleAfter))
        do {
            // Con push: permite updates por APNs con la app en background.
            let activity = try Activity.request(attributes: attrs, content: content,
                                                pushType: .token)
            managedId = activity.id
            adopt(activity)
            return true
        } catch {
            // Sin entitlement de push (cuenta gratis) → modo local, como antes.
            NSLog("OrbitDrive: pushType .token falló (\(error)) — reintento local")
            do {
                let activity = try Activity.request(attributes: attrs, content: content,
                                                    pushType: nil)
                managedId = activity.id
                return true
            } catch {
                NSLog("OrbitDrive: no se pudo iniciar Live Activity: \(error)")
                return false
            }
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

    /// Actualiza la actividad administrada y termina cualquier duplicada
    /// (p. ej. la que quedó de una sesión anterior y apareció tarde en la
    /// lista, causando la pantalla "doble").
    static func update(_ state: OrbitDriveActivityAttributes.ContentState) {
        let content = ActivityContent(state: state,
                                      staleDate: Date().addingTimeInterval(staleAfter))
        Task {
            let all = Activity<OrbitDriveActivityAttributes>.activities
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

    /// Termina la Live Activity (al salir de Drive Mode).
    static func end() {
        managedId = nil
        Task {
            for activity in Activity<OrbitDriveActivityAttributes>.activities {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }
    }
}
