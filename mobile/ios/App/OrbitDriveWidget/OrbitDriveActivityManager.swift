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

    static func isAvailable() -> Bool {
        ActivityAuthorizationInfo().areActivitiesEnabled
    }

    static func current() -> Activity<OrbitDriveActivityAttributes>? {
        Activity<OrbitDriveActivityAttributes>.activities.first
    }

    /// Inicia la Live Activity (o actualiza si ya existe).
    @discardableResult
    static func start(_ state: OrbitDriveActivityAttributes.ContentState) -> Bool {
        guard isAvailable() else { return false }
        if current() != nil { update(state); return true }
        let attrs = OrbitDriveActivityAttributes(title: title)
        let content = ActivityContent(state: state,
                                      staleDate: Date().addingTimeInterval(staleAfter))
        do {
            // Con push: permite updates por APNs con la app en background.
            let activity = try Activity.request(attributes: attrs, content: content,
                                                pushType: .token)
            observePushToken(activity)
            return true
        } catch {
            // Sin entitlement de push (cuenta gratis) → modo local, como antes.
            NSLog("OrbitDrive: pushType .token falló (\(error)) — reintento local")
            do {
                _ = try Activity.request(attributes: attrs, content: content, pushType: nil)
                return true
            } catch {
                NSLog("OrbitDrive: no se pudo iniciar Live Activity: \(error)")
                return false
            }
        }
    }

    /// Observa los push tokens de ActivityKit (hex) y los pasa al plugin.
    private static func observePushToken(_ activity: Activity<OrbitDriveActivityAttributes>) {
        Task {
            for await tokenData in activity.pushTokenUpdates {
                let hex = tokenData.map { String(format: "%02x", $0) }.joined()
                NSLog("OrbitDrive: push token actualizado (\(hex.prefix(12))…)")
                onPushToken?(hex)
            }
        }
    }

    /// Actualiza todas las actividades activas con el nuevo estado.
    static func update(_ state: OrbitDriveActivityAttributes.ContentState) {
        let content = ActivityContent(state: state,
                                      staleDate: Date().addingTimeInterval(staleAfter))
        Task {
            for activity in Activity<OrbitDriveActivityAttributes>.activities {
                await activity.update(content)
            }
        }
    }

    /// Termina la Live Activity (al salir de Drive Mode).
    static func end() {
        Task {
            for activity in Activity<OrbitDriveActivityAttributes>.activities {
                await activity.end(nil, dismissalPolicy: .immediate)
            }
        }
    }
}
