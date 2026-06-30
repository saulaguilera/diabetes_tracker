//  CarPlaySceneDelegate.swift
//  ORBIT Drive Mode — escena nativa de CarPlay (safety-first, glanceable).
//
//  Muestra glucosa + flecha + mensaje de seguridad usando plantillas de CarPlay.
//  NO usa la webview. NO muestra dosis ni predicción (igual que /api/copilot/drive).
//
//  Plantilla: CPListTemplate (disponible para la categoría 'audio', que es la
//  puerta de entrada típica de las apps de CGM a CarPlay). El número grande va
//  como header de sección; el mensaje y la frescura como item.
//
//  Lifecycle: declarar esta escena en Info.plist (ver carplay/README.md) y añadir
//  el entitlement com.apple.developer.carplay-audio.

import CarPlay
import UIKit

class CarPlaySceneDelegate: UIResponder, CPTemplateApplicationSceneDelegate {

    var interfaceController: CPInterfaceController?
    private var timer: Timer?
    private let listTemplate = CPListTemplate(title: "ORBIT Drive", sections: [])

    // Conexión a CarPlay
    func templateApplicationScene(_ scene: CPTemplateApplicationScene,
                                  didConnect interfaceController: CPInterfaceController) {
        self.interfaceController = interfaceController
        interfaceController.setRootTemplate(listTemplate, animated: false, completion: nil)
        renderLoading()
        refresh()
        // refresco glanceable; CarPlay limita updates, 30s es conservador
        timer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            self?.refresh()
        }
    }

    // Desconexión
    func templateApplicationScene(_ scene: CPTemplateApplicationScene,
                                  didDisconnectInterfaceController interfaceController: CPInterfaceController) {
        timer?.invalidate(); timer = nil
        self.interfaceController = nil
    }

    // MARK: - Data

    private func refresh() {
        DriveModeClient.fetch { [weak self] payload in
            DispatchQueue.main.async { self?.render(payload) }
        }
    }

    private func renderLoading() {
        let item = CPListItem(text: "Cargando…", detailText: nil)
        listTemplate.updateSections([CPListSection(items: [item])])
    }

    private func render(_ p: DriveModePayload?) {
        guard let p = p else {
            let item = CPListItem(text: "Sin conexión", detailText: "Reintentando…")
            listTemplate.updateSections([CPListSection(items: [item])])
            return
        }

        // Header: número grande + flecha + unidad (lo más glanceable)
        let header = "\(p.valueText) \(p.trendArrow)   \(p.unit)"

        // Item principal: mensaje de seguridad + frescura/sensor
        let main = CPListItem(text: p.message,
                              detailText: "\(p.updatedText) · \(p.sensor)")
        // (opcional) un punto de color según nivel — CarPlay restringe estilos,
        // el color real lo da el sistema; el nivel se comunica por el texto.

        let section = CPListSection(items: [main], header: header, sectionIndexTitle: nil)
        listTemplate.updateSections([section])
    }
}
