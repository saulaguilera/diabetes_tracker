//  OrbitDriveActivityManager.swift
//  ORBIT Drive Mode — control de la Live Activity (start / update / end).
//
//  Vive en el target de la APP (no en la extensión). Convierte el estado JSON
//  que manda la web (payload de /api/copilot/drive) en ContentState y maneja el
//  ciclo de vida de la Live Activity con ActivityKit.
//
//  MVP: updates locales (los dispara la web en cada refresco). Sin APNs.
//  TODO (futuro): ActivityKit push token + APNs para updates en background más
//  confiables cuando la app no está en foreground.

import ActivityKit
import Foundation

@available(iOS 16.2, *)
enum OrbitDriveActivityManager {

    private static let title = "ORBIT Drive"
    // Si no llega update en este tiempo, iOS marca la actividad como "stale".
    private static let staleAfter: TimeInterval = 15 * 60

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
        do {
            let attrs = OrbitDriveActivityAttributes(title: title)
            let content = ActivityContent(state: state,
                                          staleDate: Date().addingTimeInterval(staleAfter))
            _ = try Activity.request(attributes: attrs, content: content, pushType: nil)
            return true
        } catch {
            NSLog("OrbitDrive: no se pudo iniciar Live Activity: \(error)")
            return false
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
