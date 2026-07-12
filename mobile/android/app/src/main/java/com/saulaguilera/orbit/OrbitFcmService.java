//  OrbitFcmService.java
//  ORBIT — FirebaseMessagingService: rotación de token + display en foreground.
//
//  Con la app en BACKGROUND, los mensajes "notification" de FCM los muestra
//  el sistema solo (con el channel_id que manda el backend). Este service
//  cubre lo que el sistema no hace: (a) avisar cuando FCM rota el token, y
//  (b) mostrar la notificación si llega con la app en FOREGROUND.

package com.saulaguilera.orbit;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;

import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

public class OrbitFcmService extends FirebaseMessagingService {

    @Override
    public void onNewToken(@NonNull String token) {
        // Si la web está abierta, el listener la lleva al backend al instante;
        // si no, el próximo register() al abrir la app la registra igual.
        OrbitPushPlugin.emitToken(token);
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage msg) {
        RemoteMessage.Notification n = msg.getNotification();
        if (n == null) return;

        OrbitPushPlugin.createChannel(this);
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent pi = PendingIntent.getActivity(
            this, 0, open,
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);

        Notification notif = new NotificationCompat.Builder(this, OrbitPushPlugin.CHANNEL_ID)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(n.getTitle())
            .setContentText(n.getBody())
            .setStyle(new NotificationCompat.BigTextStyle().bigText(n.getBody()))
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build();

        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null) nm.notify((int) (System.currentTimeMillis() % Integer.MAX_VALUE), notif);
    }
}
