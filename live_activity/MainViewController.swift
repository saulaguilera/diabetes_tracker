import UIKit
import Capacitor

// Registra el plugin nativo local `OrbitDriveActivity`.
// Capacitor 8 + SPM NO auto-descubre plugins del app-target (solo los que vienen
// como paquete). Registro manual = camino Swift-only recomendado (Capacitor 6+).
class MainViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(OrbitDriveActivityPlugin())
    }
}
