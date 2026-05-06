package com.opencode.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;

public class FlaskService extends Service {

    private static final String CHANNEL_ID = "opencode_flask_channel";
    private static final int NOTIFICATION_ID = 1;

    private Thread flaskThread;
    private Thread pollThread;
    private PowerManager.WakeLock wakeLock;

    @Override
    public void onCreate() {
        super.onCreate();

        acquireWakeLock();
        startFlaskServer();
        createNotification();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        super.onDestroy();
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
    }

    private void acquireWakeLock() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = pm.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK,
            "opencode:FlaskWakeLock"
        );
        wakeLock.acquire(10 * 60 * 60 * 1000L);
    }

    private void startFlaskServer() {
        flaskThread = new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(this));
                }
                Python.getInstance().getModule("runner").callAttr("run");
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
        flaskThread.setDaemon(false);
        flaskThread.start();

        pollThread = new Thread(() -> {
            for (int i = 0; i < 120; i++) {
                try {
                    URL url = new URL("http://localhost:5000/ping");
                    HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("GET");
                    conn.setConnectTimeout(2000);
                    conn.setReadTimeout(2000);
                    BufferedReader reader = new BufferedReader(
                        new InputStreamReader(conn.getInputStream())
                    );
                    StringBuilder response = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        response.append(line);
                    }
                    reader.close();
                    conn.disconnect();
                    if (response.toString().contains("ok")) {
                        broadcastFlaskReady();
                        return;
                    }
                } catch (Exception e) {
                    try { Thread.sleep(500); } catch (InterruptedException ignored) {}
                }
            }
        });
        pollThread.setDaemon(false);
        pollThread.start();
    }

    private void broadcastFlaskReady() {
        new Handler(Looper.getMainLooper()).post(() -> {
            Intent broadcast = new Intent("com.opencode.app.FLASK_READY");
            sendBroadcast(broadcast);
        });
    }

    private void createNotification() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "OpenCode Server",
                NotificationManager.IMPORTANCE_LOW
            );
            channel.setDescription("OpenCode Flask server is running");
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) manager.createNotificationChannel(channel);
        }

        Intent launchIntent = new Intent(this, MainActivity.class);
        launchIntent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pendingIntent = PendingIntent.getActivity(
            this, 0, launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        Notification notification = new Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("OpenCode")
            .setContentText("Server running — tap to open")
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build();

        startForeground(NOTIFICATION_ID, notification);
    }
}
