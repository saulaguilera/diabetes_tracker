//  OrbitDriveActivityPlugin.swift
//  ORBIT Drive Mode — plugin Capacitor que expone la Live Activity a la web.
//
//  La web (Drive Mode) llama a estos métodos. En navegador/Railway el plugin no
//  existe → el bridge JS hace no-op y la web sigue funcionando igual.
//
//  Métodos (JS): OrbitDriveActivity.{isLiveActivityAvailable, startDriveActivity,
//                updateDriveActivity, endDriveActivity}
//  `state` = payload de GET /api/copilot/drive.

import Foundation
import Capacitor
import ActivityKit

@objc(OrbitDriveActivityPlugin)
public class OrbitDriveActivityPlugin: CAPPlugin, CAPBridgedPlugin {

    public let identifier = "OrbitDriveActivityPlugin"
    public let jsName = "OrbitDriveActivity"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "isLiveActivityAvailable", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "startDriveActivity",      returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "updateDriveActivity",     returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "endDriveActivity",        returnType: CAPPluginReturnPromise),
    ]

    // Reenvía el push token de ActivityKit a la web como evento
    // "drivePushToken" — la web lo registra en el backend (APNs updates).
    public override func load() {
        if #available(iOS 16.2, *) {
            OrbitDriveActivityManager.onPushToken = { [weak self] hex in
                self?.notifyListeners("drivePushToken", data: ["token": hex])
            }
        }
    }

    @objc func isLiveActivityAvailable(_ call: CAPPluginCall) {
        if #available(iOS 16.2, *) {
            call.resolve(["available": OrbitDriveActivityManager.isAvailable()])
        } else {
            call.resolve(["available": false])
        }
    }

    @objc func startDriveActivity(_ call: CAPPluginCall) {
        guard #available(iOS 16.2, *) else { call.resolve(["ok": false, "reason": "ios_too_old"]); return }
        guard let state = call.getObject("state") else { call.reject("Falta 'state'"); return }
        // El manager encola la operación (cadena serial anti-duplicados);
        // "ok" = las Live Activities están habilitadas en este dispositivo.
        OrbitDriveActivityManager.start(contentState(from: state))
        call.resolve(["ok": OrbitDriveActivityManager.isAvailable()])
    }

    @objc func updateDriveActivity(_ call: CAPPluginCall) {
        guard #available(iOS 16.2, *) else { call.resolve(["ok": false]); return }
        guard let state = call.getObject("state") else { call.reject("Falta 'state'"); return }
        OrbitDriveActivityManager.update(contentState(from: state))
        call.resolve(["ok": true])
    }

    @objc func endDriveActivity(_ call: CAPPluginCall) {
        if #available(iOS 16.2, *) { OrbitDriveActivityManager.end() }
        call.resolve(["ok": true])
    }

    // ── Mapea el payload de /api/copilot/drive → ContentState ──
    @available(iOS 16.2, *)
    private func contentState(from d: JSObject) -> OrbitDriveActivityAttributes.ContentState {
        // value puede venir como Int (112) o String ("--")
        let glucose: Int? = (d["value"] as? Int)
            ?? (d["value"] as? NSNumber)?.intValue
            ?? Int((d["value"] as? String) ?? "")

        return OrbitDriveActivityAttributes.ContentState(
            glucoseValueMgdl:   glucose,
            trendArrow:         d["trend_arrow"] as? String ?? "—",
            status:             d["status"] as? String ?? "disconnected",
            statusLevel:        d["level"] as? String ?? "unavailable",
            safetyMessage:      d["message"] as? String ?? "Sensor disconnected",
            minutesSinceUpdate: d["minutes_since_update"] as? Int,
            sensorName:         d["sensor"] as? String,
            sensorConnected:    d["connected"] as? Bool ?? false,
            staleData:          d["stale"] as? Bool ?? true,
            updatedText:        d["updated_text"] as? String ?? "No data"
        )
    }
}
