//  OrbitDriveActivityAttributes.swift
//  ORBIT Drive Mode — atributos de la Live Activity (ActivityKit).
//
//  IMPORTANTE: este archivo debe pertenecer a AMBOS targets (la app y la Widget
//  Extension) para que el estado se comparta. Espeja DriveModeState del backend
//  (GET /api/copilot/drive). SOLO campos seguros: glucosa, tendencia, estado,
//  mensaje, frescura, sensor. NADA de insulina/dosis/IOB/COB/predicción.

import ActivityKit
import Foundation

public struct OrbitDriveActivityAttributes: ActivityAttributes {

    // Estado dinámico — refleja DriveModeState (solo lo glanceable y seguro).
    public struct ContentState: Codable, Hashable {
        public var glucoseValueMgdl: Int?     // nil → mostrar "--" (no engañar)
        public var trendArrow: String         // → ↗ ↑ ↘ ↓ —
        public var status: String             // stable / low / urgent_low / stale / disconnected …
        public var statusLevel: String        // normal / caution / urgent / unavailable
        public var safetyMessage: String      // "Stable", "Stop when safe", …
        public var minutesSinceUpdate: Int?
        public var sensorName: String?
        public var sensorConnected: Bool
        public var staleData: Bool
        public var updatedText: String         // "Updated 3 min ago" / "No data"

        public init(glucoseValueMgdl: Int?, trendArrow: String, status: String,
                    statusLevel: String, safetyMessage: String, minutesSinceUpdate: Int?,
                    sensorName: String?, sensorConnected: Bool, staleData: Bool, updatedText: String) {
            self.glucoseValueMgdl = glucoseValueMgdl
            self.trendArrow = trendArrow
            self.status = status
            self.statusLevel = statusLevel
            self.safetyMessage = safetyMessage
            self.minutesSinceUpdate = minutesSinceUpdate
            self.sensorName = sensorName
            self.sensorConnected = sensorConnected
            self.staleData = staleData
            self.updatedText = updatedText
        }

        /// Texto glanceable del número (o "--" si no hay dato confiable).
        public var valueText: String { glucoseValueMgdl.map(String.init) ?? "--" }
    }

    public var title: String   // p.ej. "ORBIT Drive"
    public init(title: String) { self.title = title }
}
