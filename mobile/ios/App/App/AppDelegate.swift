import UIKit
import Capacitor
import UserNotifications

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {

    var window: UIWindow?

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?) -> Bool {
        // Notificaciones: delegate para mostrar el banner aunque la app esté abierta.
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    // ── Push de notificaciones normales (campanita «Orbit encontró algo») ──
    // El token del dispositivo llega acá; OrbitPushBridge lo puentea al plugin
    // (y de ahí a la web, que lo registra en el backend).
    func application(_ application: UIApplication,
                     didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        OrbitPushBridge.lastToken = hex
        OrbitPushBridge.onToken?(hex)
    }

    func application(_ application: UIApplication,
                     didFailToRegisterForRemoteNotificationsWithError error: Error) {
        print("OrbitPush: registro APNs falló — \(error.localizedDescription)")
    }

    func applicationWillResignActive(_ application: UIApplication) {
        // Sent when the application is about to move from active to inactive state. This can occur for certain types of temporary interruptions (such as an incoming phone call or SMS message) or when the user quits the application and it begins the transition to the background state.
        // Use this method to pause ongoing tasks, disable timers, and invalidate graphics rendering callbacks. Games should use this method to pause the game.
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        // Use this method to release shared resources, save user data, invalidate timers, and store enough application state information to restore your application to its current state in case it is terminated later.
        // If your application supports background execution, this method is called instead of applicationWillTerminate: when the user quits.
    }

    func applicationWillEnterForeground(_ application: UIApplication) {
        // Called as part of the transition from the background to the active state; here you can undo many of the changes made on entering the background.
    }

    func applicationDidBecomeActive(_ application: UIApplication) {
        // Restart any tasks that were paused (or not yet started) while the application was inactive. If the application was previously in the background, optionally refresh the user interface.
    }

    func applicationWillTerminate(_ application: UIApplication) {
        // Called when the application is about to terminate. Save data if appropriate. See also applicationDidEnterBackground:.
    }

    func application(_ app: UIApplication, open url: URL, options: [UIApplication.OpenURLOptionsKey: Any] = [:]) -> Bool {
        // Called when the app was launched with a url. Feel free to add additional processing here,
        // but if you want the App API to support tracking app url opens, make sure to keep this call
        return ApplicationDelegateProxy.shared.application(app, open: url, options: options)
    }

    func application(_ application: UIApplication, continue userActivity: NSUserActivity, restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void) -> Bool {
        // Called when the app was launched with an activity, including Universal Links.
        // Feel free to add additional processing here, but if you want the App API to support
        // tracking app url opens, make sure to keep this call
        return ApplicationDelegateProxy.shared.application(application, continue: userActivity, restorationHandler: restorationHandler)
    }

}

// ── Registro del plugin nativo OrbitDriveActivity ──────────────────────────
// Capacitor 8 + SPM NO auto-descubre plugins locales del app-target. Registramos
// la instancia a mano en el bridge. La clase vive acá (AppDelegate.swift ya está
// en el target App) para NO tener que agregar un archivo nuevo al target clásico.
// El storyboard debe usar `MainViewController` (módulo App).
class MainViewController: CAPBridgeViewController {
    override func capacitorDidLoad() {
        bridge?.registerPluginInstance(OrbitDriveActivityPlugin())
        bridge?.registerPluginInstance(OrbitPushPlugin())
    }
}


// ── Banner con la app abierta ───────────────────────────────────────────────
extension AppDelegate: UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler:
                                    @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound])
    }
}


// ── OrbitPush: plugin mínimo para notificaciones normales ──────────────────
// Vive acá (mismo truco que MainViewController) para no agregar archivos al
// target de Xcode. La web llama OrbitPush.register() → permiso + token APNs →
// evento "appPushToken" hacia JS, que lo registra en el backend.
enum OrbitPushBridge {
    static var onToken: ((String) -> Void)?
    static var lastToken: String?
}

@objc(OrbitPushPlugin)
public class OrbitPushPlugin: CAPPlugin, CAPBridgedPlugin {

    public let identifier = "OrbitPushPlugin"
    public let jsName = "OrbitPush"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "register", returnType: CAPPluginReturnPromise),
    ]

    public override func load() {
        OrbitPushBridge.onToken = { [weak self] hex in
            self?.notifyListeners("appPushToken", data: ["token": hex])
        }
        // si el token llegó antes de que JS escuchara, reemitirlo al cargar
        if let hex = OrbitPushBridge.lastToken {
            notifyListeners("appPushToken", data: ["token": hex])
        }
    }

    @objc func register(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            DispatchQueue.main.async {
                if granted { UIApplication.shared.registerForRemoteNotifications() }
                call.resolve(["granted": granted])
            }
        }
    }
}
