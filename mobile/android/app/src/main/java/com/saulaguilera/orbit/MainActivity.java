package com.saulaguilera.orbit;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Plugins propios: registrar ANTES de super.onCreate (Capacitor 8).
        registerPlugin(OrbitPushPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
