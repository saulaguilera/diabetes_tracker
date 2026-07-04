//  OrbitDriveLiveActivity.swift
//  ORBIT Drive Mode — UI de la Live Activity (Lock Screen + Dynamic Island + CarPlay).
//
//  Pertenece al target de la WIDGET EXTENSION. Glanceable, alto contraste,
//  dark-first. SOLO glucosa + flecha + estado + mensaje + frescura. NADA de
//  insulina/dosis/IOB/COB/predicción/gráficos.
//
//  CarPlay muestra la vista COMPACTA (número + flecha). Por eso ahí la advertencia
//  se comunica con COLOR + un símbolo ⚠️ en estados de riesgo (el texto largo no
//  entra en el banner de CarPlay; sí aparece en Lock Screen / StandBy).

import ActivityKit
import WidgetKit
import SwiftUI

// Nivel de estado → color (verde/ámbar/rojo/gris).
@available(iOS 16.2, *)
private func stateColor(_ level: String) -> Color {
    switch level {
    case "normal":  return Color(red: 0.20, green: 0.85, blue: 0.63)   // verde/cyan
    case "caution": return Color(red: 0.95, green: 0.66, blue: 0.20)   // ámbar
    case "urgent":  return Color(red: 1.00, green: 0.33, blue: 0.30)   // rojo
    default:        return Color(red: 0.55, green: 0.60, blue: 0.68)   // gris (no confiable)
    }
}

// Símbolo de advertencia por nivel (nil en normal → número limpio).
@available(iOS 16.2, *)
private func warningSymbol(_ level: String) -> String? {
    switch level {
    case "urgent":      return "exclamationmark.triangle.fill"
    case "caution":     return "exclamationmark.triangle.fill"
    case "unavailable": return "wifi.slash"
    default:            return nil
    }
}

// Estado en una palabra corta (para el espacio compacto de CarPlay).
private func shortStatus(_ status: String) -> String {
    switch status {
    case "stable":                  return "Stable"
    case "attention":               return "Watch"
    case "low", "urgent_low":       return "Low"
    case "high", "urgent_high":     return "High"
    case "stale":                   return "Stale"
    case "disconnected":            return "Offline"
    default:                        return ""
    }
}

@available(iOS 16.2, *)
struct OrbitDriveLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: OrbitDriveActivityAttributes.self) { context in
            LockScreenView(state: context.state)
                .padding(16)
                .background(Color.black)
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let s = context.state
            let c = stateColor(s.statusLevel)
            let sym = warningSymbol(s.statusLevel)
            return DynamicIsland {
                // ── Expandido (long-press en Dynamic Island) ──
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 6) {
                        if let sym { Image(systemName: sym).foregroundColor(c) }
                        Text(s.valueText)
                            .font(.system(size: 34, weight: .semibold, design: .rounded))
                            .foregroundColor(c)
                        Text(s.trendArrow).font(.title2).foregroundColor(c)
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("mg/dL").font(.caption).foregroundColor(.secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(s.safetyMessage)
                            .font(.headline).foregroundColor(c).lineLimit(1)
                        Text("\(s.updatedText) · \(s.sensorName ?? "CGM")")
                            .font(.caption2).foregroundColor(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                // ── CARPLAY (izquierda): ⚠️ + valor + unidad al lado ──
                HStack(spacing: 3) {
                    if let sym { Image(systemName: sym).foregroundColor(c) }
                    Text(s.valueText)
                        .font(.system(size: 15, weight: .semibold, design: .rounded))
                        .foregroundColor(c)
                    Text("mg/dL").font(.system(size: 9)).foregroundColor(.secondary)
                }
            } compactTrailing: {
                // ── CARPLAY (derecha): flecha + estado corto ──
                HStack(spacing: 3) {
                    Text(s.trendArrow).foregroundColor(c)
                    Text(shortStatus(s.status))
                        .font(.system(size: 11, weight: .medium)).foregroundColor(c)
                }
            } minimal: {
                if let sym {
                    Image(systemName: sym).foregroundColor(c)
                } else {
                    Text(s.valueText)
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .foregroundColor(c)
                }
            }
            .keylineTint(c)
        }
    }
}

// ── Vista del Lock Screen / StandBy (con espacio → más completa) ──
@available(iOS 16.2, *)
struct LockScreenView: View {
    let state: OrbitDriveActivityAttributes.ContentState
    private var c: Color { stateColor(state.statusLevel) }
    private var sym: String? { warningSymbol(state.statusLevel) }
    // Con datos no confiables NO mostramos el número (evita engañar).
    private var showValue: Bool { state.statusLevel != "unavailable" && state.glucoseValueMgdl != nil }

    var body: some View {
        HStack(spacing: 16) {
            // Izquierda: número + flecha (o triángulo si no hay dato confiable)
            if showValue {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(state.valueText)
                        .font(.system(size: 46, weight: .semibold, design: .rounded))
                        .foregroundColor(c)
                    Text(state.trendArrow).font(.system(size: 30)).foregroundColor(c)
                }
            } else {
                Image(systemName: sym ?? "exclamationmark.triangle.fill")
                    .font(.system(size: 34)).foregroundColor(c)
            }

            // Derecha: marca + mensaje (+ ⚠️) + frescura/sensor
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Circle().fill(c).frame(width: 7, height: 7)
                    Text("ORBIT DRIVE")
                        .font(.system(size: 11, weight: .bold)).tracking(1.5)
                        .foregroundColor(Color.white.opacity(0.6))
                }
                HStack(spacing: 6) {
                    if let sym, showValue { Image(systemName: sym).font(.system(size: 14)).foregroundColor(c) }
                    Text(state.safetyMessage)
                        .font(.system(size: 17, weight: .semibold))
                        .foregroundColor(.white).lineLimit(2)
                }
                Text("\(state.updatedText) · \(state.sensorName ?? "CGM")")
                    .font(.caption2).foregroundColor(.secondary)
            }
            Spacer(minLength: 0)
        }
    }
}
