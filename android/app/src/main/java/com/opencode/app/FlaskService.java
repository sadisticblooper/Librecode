package com.opencode.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.net.HttpURLConnection;
import java.net.URL;

public class FlaskService extends Service {

    private static final String CHANNEL_ID      = "opencode_flask_channel";
    private static final int    NOTIFICATION_ID  = 1;
    private static final int    FLASK_PORT        = 5000;
    private static final long   WATCHDOG_INTERVAL = 15_000;

    private Thread flaskThread;
    private Thread watchdogThread;
    private PowerManager.WakeLock wakeLock;
    private volatile boolean running = true;

    @Override
    public void onCreate() {
        super.onCreate();
        // startForeground MUST be called within ~5 s of service creation —
        // do it first, before Python init (which can take several seconds).
        buildNotificationChannel();
        startForeground(NOTIFICATION_ID, buildNotification());

        acquireWakeLock();
        startFlaskThread();
        startWatchdog();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    /** User swiped app away from Recents — reschedule our restart. */
    @Override
    public void onTaskRemoved(Intent rootIntent) {
        Intent restart = new Intent(getApplicationContext(), FlaskService.class);
        restart.setPackage(getPackageName());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(restart);
        } else {
            startService(restart);
        }
        super.onTaskRemoved(rootIntent);
    }

    @Override
    public void onDestroy() {
        running = false;
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    // ── Flask ─────────────────────────────────────────────────────────────────

    private void startFlaskThread() {
        flaskThread = new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(getApplicationContext()));
                }
                Python.getInstance().getModule("runner").callAttr("run");
            } catch (Exception e) {
                e.printStackTrace();
            }
        }, "flask-main");
        flaskThread.setDaemon(false);   // non-daemon keeps process alive
        flaskThread.start();
    }

    private void startWatchdog() {
        // Fast poller: broadcasts FLASK_READY as soon as Flask first responds
        Thread firstReady = new Thread(() -> {
            for (int i = 0; i < 150; i++) {
                if (isPingReachable()) {
                    sendBroadcast(new Intent("com.opencode.app.FLASK_READY"));
                    return;
                }
                try { Thread.sleep(200); } catch (InterruptedException e) { return; }
            }
        }, "flask-first-ready");
        firstReady.setDaemon(true);
        firstReady.start();

        // Slow watchdog: restarts Flask thread if it ever dies
        watchdogThread = new Thread(() -> {
            boolean ready = false;
            while (running) {
                try { Thread.sleep(WATCHDOG_INTERVAL); } catch (InterruptedException e) { break; }
                if (isPingReachable()) {
                    if (!ready) { ready = true; sendBroadcast(new Intent("com.opencode.app.FLASK_READY")); }
                } else if (!flaskThread.isAlive()) {
                    startFlaskThread();
                }
            }
        }, "flask-watchdog");
        watchdogThread.setDaemon(true);
        watchdogThread.start();
    }

    private boolean isPingReachable() {
        try {
            URL url = new URL("http://localhost:" + FLASK_PORT + "/ping");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(2000);
            conn.setReadTimeout(2000);
            conn.setRequestMethod("GET");
            int code = conn.getResponseCode();
            conn.disconnect();
            return code == 200;
        } catch (Exception e) { return false; }
    }

    // ── WakeLock ──────────────────────────────────────────────────────────────

    private void acquireWakeLock() {
        PowerManager pm = (PowerManager) getSystemService(POWER_SERVICE);
        if (pm == null) return;
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "opencode:FlaskWakeLock");
        wakeLock.acquire(12 * 60 * 60 * 1000L);   // 12 h; re-acquired on service restart
    }

    // ── Notification ──────────────────────────────────────────────────────────

    private void buildNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel ch = new NotificationChannel(
                CHANNEL_ID, "OpenCode Server", NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Keeps the OpenCode AI server alive in the background");
            ch.setShowBadge(false);
            NotificationManager nm = getSystemService(NotificationManager.class);
            if (nm != null) nm.createNotificationChannel(ch);
        }
    }

    private Notification buildNotification() {
        Intent launch = new Intent(this, MainActivity.class);
        launch.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(
            this, 0, launch,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            return new Notification.Builder(this, CHANNEL_ID)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("OpenCode")
                .setContentText("AI server running in background")
                .setContentIntent(pi)
                .setOngoing(true)
                .setForegroundServiceBehavior(Notification.FOREGROUND_SERVICE_IMMEDIATE)
                .build();
        } else {
            //noinspection deprecation
            return new Notification.Builder(this)
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle("OpenCode")
                .setContentText("AI server running in background")
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
        }
    }
}
