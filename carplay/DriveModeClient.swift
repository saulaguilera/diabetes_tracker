//  DriveModeClient.swift
//  ORBIT Drive Mode — cliente nativo que consume el MISMO contrato que la web.
//
//  GET https://web-production-c2f1.up.railway.app/api/copilot/drive
//  → { "ok": true, "drive": { value, unit, trend_arrow, message, ... } }
//
//  Reusa DriveModeState/adapter del backend: la escena CarPlay solo renderiza.
//
//  ⚠️ AUTH (TODO de integración): el endpoint /api/copilot/drive está protegido
//  por sesión (login). La escena CarPlay corre en un proceso separado del
//  WebView, así que NO comparte la cookie automáticamente. Opciones:
//   (a) que el backend acepte un token de solo-lectura en /drive (header
//       Authorization o ?token=) y guardarlo en Keychain/App Group desde la app;
//   (b) compartir la cookie de sesión vía WKHTTPCookieStore → HTTPCookieStorage.
//  Acá dejamos el hook `authHeader` listo para (a).

import Foundation

struct DriveModePayload {
    let valueText: String        // "112" o "--"
    let unit: String             // "mg/dL"
    let trendArrow: String       // → ↗ ↑ ↘ ↓ —
    let status: String           // stable / low / urgent_low / ...
    let level: String            // normal / caution / urgent / unavailable
    let tint: String             // positive / warning / critical / muted
    let message: String          // "Stable", "Stop when safe", ...
    let updatedText: String      // "Updated 3 min ago" / "No data"
    let sensor: String
    let connected: Bool
}

enum DriveModeClient {

    // Mismo host que server.url del capacitor.config.json
    static let baseURL = "https://web-production-c2f1.up.railway.app"
    // TODO: setear desde Keychain/App Group tras login. Si nil → la request irá
    // sin token (y el backend devolverá 401 hasta implementar token auth en /drive).
    static var authToken: String? = nil

    static func fetch(completion: @escaping (DriveModePayload?) -> Void) {
        guard let url = URL(string: "\(baseURL)/api/copilot/drive") else {
            completion(nil); return
        }
        var req = URLRequest(url: url, timeoutInterval: 8)
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.setValue("OrbitApp-CarPlay", forHTTPHeaderField: "User-Agent")
        if let t = authToken { req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization") }

        URLSession.shared.dataTask(with: req) { data, resp, _ in
            guard
                let data = data,
                let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                let drive = json["drive"] as? [String: Any]
            else { completion(nil); return }

            // value puede venir como Int (112) o String ("--")
            let valueText: String
            if let i = drive["value"] as? Int { valueText = String(i) }
            else if let s = drive["value"] as? String { valueText = s }
            else { valueText = "--" }

            let p = DriveModePayload(
                valueText:   valueText,
                unit:        drive["unit"] as? String ?? "mg/dL",
                trendArrow:  drive["trend_arrow"] as? String ?? "—",
                status:      drive["status"] as? String ?? "disconnected",
                level:       drive["level"] as? String ?? "unavailable",
                tint:        drive["tint"] as? String ?? "muted",
                message:     drive["message"] as? String ?? "Sensor disconnected",
                updatedText: drive["updated_text"] as? String ?? "No data",
                sensor:      drive["sensor"] as? String ?? "CGM",
                connected:   drive["connected"] as? Bool ?? false
            )
            completion(p)
        }.resume()
    }
}
