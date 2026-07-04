//  OrbitDriveLiveActivity.swift
//  ORBIT Drive Mode — UI de la Live Activity (Lock Screen + Dynamic Island).
//
//  Pertenece al target de la WIDGET EXTENSION. Glanceable, alto contraste,
//  dark-first. SOLO glucosa + flecha + estado + mensaje + frescura. NADA de
//  insulina/dosis/IOB/COB/predicción/gráficos.

import ActivityKit
import WidgetKit
import SwiftUI

// Mapeo de nivel de estado → color (verde/ámbar/rojo/gris).
@available(iOS 16.2, *)
private func stateColor(_ level: String) -> Color {
    switch level {
    case "normal":  return Color(red: 0.20, green: 0.85, blue: 0.63)   // verde/cyan
    case "caution": return Color(red: 0.90, green: 0.64, blue: 0.24)   // ámbar
    case "urgent":  return Color(red: 1.00, green: 0.35, blue: 0.32)   // rojo
    default:        return Color(red: 0.49, green: 0.55, blue: 0.63)   // gris (no confiable)
    }
}

@available(iOS 16.2, *)
struct OrbitDriveLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: OrbitDriveActivityAttributes.self) { context in
            // ── Lock Screen / banner ──
            LockScreenView(state: context.state)
                .padding(16)
                .background(Color.black)
                .activitySystemActionForegroundColor(.white)
        } dynamicIsland: { context in
            let c = stateColor(context.state.statusLevel)
            return DynamicIsland {
                // Expandido
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 6) {
                        Text(context.state.valueText)
                            .font(.system(size: 34, weight: .semibold, design: .rounded))
                            .foregroundColor(c)
                        Text(context.state.trendArrow).font(.title2).foregroundColor(c)
                    }
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("mg/dL").font(.caption).foregroundColor(.secondary)
                }
                DynamicIslandExpandedRegion(.bottom) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(context.state.safetyMessage)
                            .font(.headline).foregroundColor(.white).lineLimit(1)
                        Text(context.state.updatedText)
                            .font(.caption2).foregroundColor(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                }
            } compactLeading: {
                Text(context.state.valueText)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .foregroundColor(c)
            } compactTrailing: {
                Text(context.state.trendArrow).foregroundColor(c)
            } minimal: {
                Text(context.state.valueText)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .foregroundColor(c)
            }
            .keylineTint(c)
        }
    }
}

// ── Vista del Lock Screen ──
@available(iOS 16.2, *)
struct LockScreenView: View {
    let state: OrbitDriveActivityAttributes.ContentState
    private var c: Color { stateColor(state.statusLevel) }
    // Con datos no confiables NO mostramos el número (evita engañar).
    private var showValue: Bool { state.statusLevel != "unavailable" && state.glucoseValueMgdl != nil }

    var body: some View {
        HStack(spacing: 16) {
            // Izquierda: número + flecha (o solo estado si no hay dato)
            if showValue {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(state.valueText)
                        .font(.system(size: 46, weight: .semibold, design: .rounded))
                        .foregroundColor(c)
                    Text(state.trendArrow).font(.system(size: 30)).foregroundColor(c)
                }
            } else {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.system(size: 30)).foregroundColor(c)
            }

            // Derecha: marca + mensaje + frescura
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Circle().fill(c).frame(width: 7, height: 7)
                    Text("ORBIT DRIVE")
                        .font(.system(size: 11, weight: .bold)).tracking(1.5)
                        .foregroundColor(Color.white.opacity(0.6))
                }
                Text(state.safetyMessage)
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundColor(.white).lineLimit(2)
                Text(showValue ? state.updatedText
                               : "\(state.updatedText)")
                    .font(.caption2).foregroundColor(.secondary)
            }
            Spacer(minLength: 0)
        }
    }
}
