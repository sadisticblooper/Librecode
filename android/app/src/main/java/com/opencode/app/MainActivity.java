package com.opencode.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

public class MainActivity extends Activity {

    public static MainActivity instance;
    private WebView webView;
    private WebView loadingWebView;
    private WebView fetchWebView;

    private static final int    FLASK_PORT           = 5000;
    private static final String FLASK_URL            = "http://localhost:" + FLASK_PORT;
    private static final int    MIN_LOADING_MS       = 1000;
    private static final int    POLL_INTERVAL_MS     = 150;
    private static final int    POLL_TIMEOUT_MS      = 30_000;
    private static final int    REQUEST_FOLDER_PICKER = 100;
    private static final int    REQUEST_NOTIF_PERM    = 200;

    private boolean returningFromSettings = false;
    private boolean webViewLoaded        = false;
    private SharedPreferences prefs;
    private String selectedFolderPath;
    private String storageFolderPath;
    private String pendingWorkingDir;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    // Receives FLASK_READY broadcast from FlaskService
    private final BroadcastReceiver flaskReadyReceiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ctx, Intent intent) {
            if (!webViewLoaded) {
                webViewLoaded = true;
                mainHandler.post(() -> {
                    webView.loadUrl(FLASK_URL);
                    dismissLoadingOverlay();
                });
            }
        }
    };

    // ── onCreate ──────────────────────────────────────────────────────────────

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        instance = this;
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences("opencode", MODE_PRIVATE);
        selectedFolderPath = prefs.getString("working_dir", "");
        storageFolderPath  = prefs.getString("storage_dir", "");

        if (storageFolderPath == null || storageFolderPath.isEmpty()) {
            storageFolderPath = new File(
                Environment.getExternalStorageDirectory(), "opencode"
            ).getAbsolutePath();
        }
        new File(storageFolderPath).mkdirs();
        writeStorageDirFile();

        extractToybox();
        setupFullscreen();
        requestAllPermissions();    // storage + notifications + battery-opt

        webView        = findViewById(R.id.webview);
        loadingWebView = findViewById(R.id.loading_webview);

        setupWebView();
        setupLoadingWebView();
        setupFetchWebView();

        loadingWebView.loadDataWithBaseURL(null, LOADING_HTML, "text/html", "UTF-8", null);

        // Register before starting service so we never miss the broadcast
        IntentFilter filter = new IntentFilter("com.opencode.app.FLASK_READY");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(flaskReadyReceiver, filter, RECEIVER_NOT_EXPORTED);
        } else {
            registerReceiver(flaskReadyReceiver, filter);
        }

        startOpenCodeService();

        // Fallback poller in case we missed the broadcast (e.g. service was
        // already running before this activity was created).
        pollForFlask();
    }

    // ── Service management ────────────────────────────────────────────────────

    private void startOpenCodeService() {
        Intent svc = new Intent(this, FlaskService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(svc);
        } else {
            startService(svc);
        }
    }

    /**
     * Fallback: poll /ping until Flask responds, then load the WebView.
     * Covers the case where the service was already running before the
     * activity was created (broadcast already fired, receiver wasn't registered).
     */
    private void pollForFlask() {
        final long start = System.currentTimeMillis();

        new Thread(() -> {
            boolean ready = false;
            while (System.currentTimeMillis() - start < POLL_TIMEOUT_MS) {
                if (isPingReachable()) { ready = true; break; }
                try { Thread.sleep(POLL_INTERVAL_MS); } catch (InterruptedException e) { break; }
            }

            if (!ready) {
                mainHandler.post(() ->
                    Toast.makeText(this, "Server failed to start", Toast.LENGTH_LONG).show());
                mainHandler.post(this::dismissLoadingOverlay);
                return;
            }

            long elapsed   = System.currentTimeMillis() - start;
            long remaining = Math.max(0, MIN_LOADING_MS - elapsed);

            mainHandler.postDelayed(() -> {
                if (!webViewLoaded) {
                    webViewLoaded = true;
                    webView.loadUrl(FLASK_URL);
                    dismissLoadingOverlay();
                }
            }, remaining);

        }, "flask-poller").start();
    }

    private boolean isPingReachable() {
        try {
            URL url = new URL("http://localhost:" + FLASK_PORT + "/ping");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(200);
            conn.setReadTimeout(200);
            conn.setRequestMethod("GET");
            int code = conn.getResponseCode();
            conn.disconnect();
            return code == 200;
        } catch (Exception e) { return false; }
    }

    // ── Resume: reconnect WebView if it went blank ─────────────────────────

    @Override
    protected void onResume() {
        super.onResume();

        if (returningFromSettings) {
            returningFromSettings = false;
            // Re-check storage permission then reload
            mainHandler.postDelayed(() -> {
                if (webViewLoaded) webView.loadUrl(FLASK_URL);
            }, 500);
            return;
        }

        // If user backgrounded & returned, and WebView is already loaded,
        // just make sure it's still showing the right URL (handles blank page).
        if (webViewLoaded && webView != null) {
            String cur = webView.getUrl();
            if (cur == null || cur.isEmpty() || cur.equals("about:blank")) {
                webView.loadUrl(FLASK_URL);
            }
        }
    }

    @Override
    protected void onDestroy() {
        try { unregisterReceiver(flaskReadyReceiver); } catch (Exception ignored) {}
        super.onDestroy();
        // Service intentionally NOT stopped here — it stays alive in background.
    }

    // ── Permissions ───────────────────────────────────────────────────────────

    private void requestAllPermissions() {
        requestFileAccess();
        requestNotificationPermission();
        requestIgnoreBatteryOptimizations();
    }

    private void requestFileAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                returningFromSettings = true;
                try {
                    startActivity(new Intent(
                        Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + getPackageName())));
                } catch (Exception e) {
                    startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                }
            }
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            requestPermissions(new String[]{
                android.Manifest.permission.READ_EXTERNAL_STORAGE,
                android.Manifest.permission.WRITE_EXTERNAL_STORAGE
            }, 1001);
        }
    }

    /** Android 13+ requires explicit runtime permission for notifications. */
    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                    != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                requestPermissions(
                    new String[]{ android.Manifest.permission.POST_NOTIFICATIONS },
                    REQUEST_NOTIF_PERM);
            }
        }
    }

    /**
     * Opens Android's battery-optimization settings for this app so the user
     * can whitelist it. Without this, Android may kill the service on some OEMs
     * (Xiaomi, Samsung, Oppo, etc.) even with a foreground service.
     */
    private void requestIgnoreBatteryOptimizations() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            android.os.PowerManager pm =
                (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (pm != null && !pm.isIgnoringBatteryOptimizations(getPackageName())) {
                try {
                    Intent intent = new Intent(
                        Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:" + getPackageName()));
                    startActivity(intent);
                } catch (Exception e) {
                    // Some devices don't support direct deep-link; open general screen
                    try {
                        startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS));
                    } catch (Exception ignored) {}
                }
            }
        }
    }

    // ── UI helpers ────────────────────────────────────────────────────────────

    private void dismissLoadingOverlay() {
        if (loadingWebView == null) return;
        loadingWebView.animate()
            .alpha(0f)
            .setDuration(300)
            .withEndAction(() -> loadingWebView.setVisibility(View.GONE))
            .start();
    }

    private void setupFullscreen() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController ctrl = getWindow().getInsetsController();
            if (ctrl != null) {
                ctrl.hide(WindowInsets.Type.statusBars() | WindowInsets.Type.navigationBars());
                ctrl.setSystemBarsBehavior(
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            );
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) setupFullscreen();
    }

    // ── WebViews ──────────────────────────────────────────────────────────────

    @SuppressLint("SetJavaScriptEnabled")
    private void setupWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        webView.addJavascriptInterface(new AndroidBridge(), "Android");
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return !r.getUrl().toString().startsWith("http://localhost");
            }
        });
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupLoadingWebView() {
        WebSettings s = loadingWebView.getSettings();
        s.setJavaScriptEnabled(true);
        loadingWebView.setBackgroundColor(0xFF0A0A0A);
        loadingWebView.setAlpha(1f);
        loadingWebView.setVisibility(View.VISIBLE);
    }

    @SuppressLint("SetJavaScriptEnabled")
    private void setupFetchWebView() {
        fetchWebView = new WebView(this);
        addContentView(fetchWebView, new android.view.ViewGroup.LayoutParams(1, 1));
        WebSettings s = fetchWebView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/120.0.0.0 Mobile Safari/537.36");
    }

    // ── JS bridge ─────────────────────────────────────────────────────────────

    class AndroidBridge {
        @JavascriptInterface
        public String getWorkingDir() {
            String dir = pendingWorkingDir != null ? pendingWorkingDir : "";
            pendingWorkingDir = null;
            return dir;
        }

        @JavascriptInterface
        public void clearWorkingDir() {
            pendingWorkingDir = null;
        }

        @JavascriptInterface
        public void setWorkingDir(String path) {
            selectedFolderPath = path;
            prefs.edit().putString("working_dir", path).apply();
        }

        @JavascriptInterface
        public void openFolderPicker() {
            Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
            startActivityForResult(intent, REQUEST_FOLDER_PICKER);
        }

        @JavascriptInterface
        public String listFiles(String path) {
            if (path == null || path.isEmpty())
                path = Environment.getExternalStorageDirectory().getAbsolutePath();
            File dir = new File(path);
            if (!dir.exists() || !dir.isDirectory()) return "[]";
            List<String> files = new ArrayList<>();
            File[] items = dir.listFiles();
            if (items != null)
                for (File f : items)
                    files.add(f.getName() + (f.isDirectory() ? "/" : ""));
            return files.toString();
        }
    }

    // ── Back press ────────────────────────────────────────────────────────────

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    // ── Folder picker result ──────────────────────────────────────────────────

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_FOLDER_PICKER || resultCode != RESULT_OK || data == null) return;

        Uri treeUri = data.getData();
        if (treeUri == null) return;

        getContentResolver().takePersistableUriPermission(treeUri,
            Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);

        String path      = treeUri.getPath();
        String authority = treeUri.getAuthority();
        if (path == null) return;

        if ("com.android.providers.downloads.documents".equals(authority)) {
            selectedFolderPath = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS).getAbsolutePath();
        } else if (path.contains(":")) {
            String[] split   = path.split(":", 2);
            String type      = split[0];
            String relPath   = split.length > 1 ? split[1] : "";
            if (type.endsWith("primary")) {
                selectedFolderPath = Environment.getExternalStorageDirectory().getAbsolutePath()
                    + (relPath.isEmpty() ? "" : "/" + relPath);
            } else {
                String volId = type.substring(type.lastIndexOf('/') + 1);
                selectedFolderPath = "/storage/" + volId + (relPath.isEmpty() ? "" : "/" + relPath);
            }
        } else {
            selectedFolderPath = path;
        }

        prefs.edit().putString("working_dir", selectedFolderPath).apply();
        pendingWorkingDir = selectedFolderPath;

        final String finalPath = selectedFolderPath;
        mainHandler.postDelayed(() -> {
            if (webView != null) {
                String escaped = finalPath.replace("\\", "\\\\").replace("'", "\\'");
                webView.evaluateJavascript("setWorkingDir('" + escaped + "')", null);
            }
        }, 500);
    }

    // ── Misc helpers ──────────────────────────────────────────────────────────

    private void writeStorageDirFile() {
        try {
            java.io.FileWriter w = new java.io.FileWriter(
                new File(getApplicationContext().getFilesDir(), "storage_dir.txt"));
            w.write(storageFolderPath);
            w.close();
        } catch (Exception e) { e.printStackTrace(); }
    }

    private void extractToybox() {
        try {
            String nativeDir = getApplicationInfo().nativeLibraryDir;
            java.io.FileWriter nw = new java.io.FileWriter(
                new File(getFilesDir(), "native_lib_dir.txt"));
            nw.write(nativeDir);
            nw.close();

            File toybox = new File(nativeDir, "libtoybox.so");
            if (!toybox.exists()) return;
            java.io.FileWriter w = new java.io.FileWriter(
                new File(getFilesDir(), "toybox_path.txt"));
            w.write(toybox.getAbsolutePath());
            w.close();
        } catch (Exception e) { e.printStackTrace(); }
    }

    public String fetchUrlSync(final String url) {
        final CountDownLatch latch  = new CountDownLatch(1);
        final String[]       result = {""};

        mainHandler.post(() -> {
            fetchWebView.setWebViewClient(new WebViewClient() {
                private boolean done = false;
                @Override
                public void onPageFinished(WebView view, String u) {
                    if (done) return;
                    done = true;
                    view.evaluateJavascript("document.documentElement.outerHTML", value -> {
                        if (value != null) result[0] = value;
                        latch.countDown();
                    });
                }
                @Override
                public void onReceivedError(WebView view, int code, String desc, String failUrl) {
                    if (done) return;
                    done = true;
                    latch.countDown();
                }
            });
            fetchWebView.loadUrl(url);
        });

        try { latch.await(20, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }

        String html = result[0];
        if (html.length() >= 2 && html.charAt(0) == '"') {
            try { html = new org.json.JSONArray("[" + html + "]").getString(0); }
            catch (Exception ignored) {}
        }
        return html;
    }

    // ── Loading screen HTML ───────────────────────────────────────────────────

    private static final String LOADING_HTML =
        "<!DOCTYPE html><html><head>" +
        "<meta name='viewport' content='width=device-width, initial-scale=1'>" +
        "<style>" +
        "* { margin:0; padding:0; box-sizing:border-box; }" +
        "body { background:#0a0a0a; display:flex; align-items:center;" +
        "       justify-content:center; height:100vh; font-family:monospace; }" +
        ".wrap { text-align:center; color:#666; }" +
        ".title { font-size:20px; color:#fff; margin-bottom:12px; letter-spacing:2px; }" +
        ".dot { display:inline-block; width:6px; height:6px; border-radius:50%;" +
        "       background:#666; margin:0 3px; animation:pulse 1.2s infinite; }" +
        ".dot:nth-child(2) { animation-delay:.2s; }" +
        ".dot:nth-child(3) { animation-delay:.4s; }" +
        "@keyframes pulse { 0%,80%,100%{opacity:.2} 40%{opacity:1} }" +
        "</style></head><body>" +
        "<div class='wrap'>" +
        "<div class='title'>opencode</div>" +
        "<div><span class='dot'></span><span class='dot'></span><span class='dot'></span></div>" +
        "</div></body></html>";
}
