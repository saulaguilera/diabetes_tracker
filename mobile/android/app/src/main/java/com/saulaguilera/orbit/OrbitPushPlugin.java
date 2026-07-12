//  OrbitPushPlugin.java
//  ORBIT — plugin Capacitor de notificaciones push (Android/FCM).
//
//  Espejo del OrbitPushPlugin de iOS: mismo nombre JS ("OrbitPush"), mismo
//  método register() y mismo evento "appPushToken", así pushBridge.js funciona
//  idéntico en ambas plataformas. La web recibe el token y lo registra en el
//  backend con platform:"android" → settings key app_fcm_token.
//
//  Si el build no tiene google-services.json, Firebase no se inicializa y
//  register() resuelve ok:false sin romper nada (la app sigue normal).

package com.saulaguilera.orbit;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Context;
import android.os.Build;

import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

@CapacitorPlugin(
    name = "OrbitPush",
    permissions = @Permission(
        strings = { Manifest.permission.POST_NOTIFICATIONS },
        alias = OrbitPushPlugin.PERM_ALIAS
    )
)
public class OrbitPushPlugin extends Plugin {

    static final String CHANNEL_ID = "orbit_alerts";
    static final String PERM_ALIAS = "notifications";

    private static OrbitPushPlugin instance;

    @Override
    public void load() {
        instance = this;
        createChannel(getContext());
    }

    /** Canal de notificaciones (obligatorio desde Android 8). El backend
     *  manda channel_id "orbit_alerts" en el payload de FCM. */
    static void createChannel(Context ctx) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "Orbit", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("Patrones, brief matutino y avisos de Orbit");
            NotificationManager nm = ctx.getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    /** El FirebaseMessagingService llama esto cuando FCM rota el token con la
     *  app abierta; la web lo re-registra en el backend. */
    static void emitToken(String token) {
        OrbitPushPlugin p = instance;
        if (p != null && token != null && !token.isEmpty()) {
            JSObject data = new JSObject();
            data.put("token", token);
            p.notifyListeners("appPushToken", data);
        }
    }

    @PluginMethod
    public void register(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 33
                && getPermissionState(PERM_ALIAS) != PermissionState.GRANTED) {
            requestPermissionForAlias(PERM_ALIAS, call, "onPermissionResult");
        } else {
            fetchToken(call);
        }
    }

    @PermissionCallback
    private void onPermissionResult(PluginCall call) {
        // Con o sin permiso intentamos obtener el token: sin permiso no se
        // muestran banners, pero el registro no rompe nada y si el usuario
        // habilita las notificaciones después, ya queda todo conectado.
        fetchToken(call);
    }

    private void fetchToken(PluginCall call) {
        try {
            com.google.firebase.messaging.FirebaseMessaging.getInstance().getToken()
                .addOnSuccessListener(token -> {
                    emitToken(token);
                    JSObject ret = new JSObject();
                    ret.put("ok", true);
                    call.resolve(ret);
                })
                .addOnFailureListener(e -> {
                    JSObject ret = new JSObject();
                    ret.put("ok", false);
                    call.resolve(ret);
                });
        } catch (Exception e) {
            // Firebase sin inicializar (falta google-services.json) → no-op.
            JSObject ret = new JSObject();
            ret.put("ok", false);
            call.resolve(ret);
        }
    }
}
