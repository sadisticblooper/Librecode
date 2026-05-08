package com.opencode.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
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
import android.graphics.Bitmap;
import android.util.Base64;
import android.view.Gravity;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.Toast;

import java.io.ByteArrayOutputStream;

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
    private WebView      browserWebView;
    private FrameLayout  browserPanel;
    private static final int FLASK_PORT = 5000;
    private static final String FLASK_URL = "http://localhost:" + FLASK_PORT;
    private static final int MIN_LOADING_MS = 1000;
    private static final int POLL_INTERVAL_MS = 100;
    private static final int POLL_TIMEOUT_MS = 15000;
    private static final int REQUEST_FOLDER_PICKER = 100;

    private boolean returningFromSettings = false;
    private SharedPreferences prefs;
    private String selectedFolderPath;
    private String storageFolderPath;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        instance = this;
        setContentView(R.layout.activity_main);

        prefs = getSharedPreferences("opencode", MODE_PRIVATE);
        selectedFolderPath = prefs.getString("working_dir", "");
        storageFolderPath = prefs.getString("storage_dir", "");

        if (storageFolderPath == null || storageFolderPath.isEmpty()) {
            File extStorage = Environment.getExternalStorageDirectory();
            storageFolderPath = new File(extStorage, "opencode").getAbsolutePath();
        }

        File storageDir = new File(storageFolderPath);
        if (!storageDir.exists()) {
            storageDir.mkdirs();
        }

        try {
            File storageFile = new File(getApplicationContext().getFilesDir(), "storage_dir.txt");
            java.io.FileWriter writer = new java.io.FileWriter(storageFile);
            writer.write(storageFolderPath);
            writer.close();
        } catch (Exception e) {
            e.printStackTrace();
        }

        extractToybox();
        setupFullscreen();
        requestFileAccess();

        webView = findViewById(R.id.webview);
        loadingWebView = findViewById(R.id.loading_webview);

        setupWebView();
        setupLoadingWebView();
        setupFetchWebView();
        setupBrowserWebView();

        loadingWebView.loadDataWithBaseURL(null, LOADING_HTML, "text/html", "UTF-8", null);

        startFlaskServer();
        waitForFlaskThenLaunch();
    }

    private void waitForFlaskThenLaunch() {
        final long startTime = System.currentTimeMillis();

        new Thread(() -> {
            boolean serverReady = false;
            long elapsed = 0;

            while (elapsed < POLL_TIMEOUT_MS) {
                try {
                    Thread.sleep(POLL_INTERVAL_MS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                }
                elapsed = System.currentTimeMillis() - startTime;

                if (isPingReachable()) {
                    serverReady = true;
                    break;
                }
            }

            final boolean ready = serverReady;
            long remaining = MIN_LOADING_MS - (System.currentTimeMillis() - startTime);
            long delay = Math.max(0, remaining);

            mainHandler.postDelayed(() -> {
                if (ready) {
                    webView.loadUrl(FLASK_URL);
                } else {
                    Toast.makeText(this, "Server failed to start", Toast.LENGTH_LONG).show();
                }
                dismissLoadingOverlay();
            }, delay);

        }).start();
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
        } catch (Exception e) {
            return false;
        }
    }

    private void dismissLoadingOverlay() {
        loadingWebView.animate()
            .alpha(0f)
            .setDuration(300)
            .withEndAction(() -> loadingWebView.setVisibility(View.GONE))
            .start();
    }

    private void extractToybox() {
        try {
            String nativeDir = getApplicationInfo().nativeLibraryDir;
            File nativeDirFile = new File(getFilesDir(), "native_lib_dir.txt");
            java.io.FileWriter nw = new java.io.FileWriter(nativeDirFile);
            nw.write(nativeDir);
            nw.close();

            File toybox = new File(nativeDir, "libtoybox.so");
            if (!toybox.exists()) return;
            File pathFile = new File(getFilesDir(), "toybox_path.txt");
            java.io.FileWriter w = new java.io.FileWriter(pathFile);
            w.write(toybox.getAbsolutePath());
            w.close();
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private void setupFullscreen() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController ctrl = getWindow().getInsetsController();
            if (ctrl != null) {
                ctrl.hide(WindowInsets.Type.statusBars()
                        | WindowInsets.Type.navigationBars());
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

    private void requestFileAccess() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            if (!Environment.isExternalStorageManager()) {
                returningFromSettings = true;
                try {
                    startActivity(new Intent(
                        Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                        Uri.parse("package:" + getPackageName())));
                } catch (Exception e) {
                    startActivity(new Intent(
                        Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                }
            }
        } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            requestPermissions(new String[]{
                android.Manifest.permission.READ_EXTERNAL_STORAGE,
                android.Manifest.permission.WRITE_EXTERNAL_STORAGE
            }, 1001);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (returningFromSettings) {
            returningFromSettings = false;
            mainHandler.postDelayed(() -> webView.loadUrl(FLASK_URL), 500);
        }
    }

    private void startFlaskServer() {
        Thread t = new Thread(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(this));
                }
                Python.getInstance().getModule("runner").callAttr("run");
            } catch (Exception e) {
                mainHandler.post(() ->
                    Toast.makeText(this, "Server error: " + e.getMessage(),
                        Toast.LENGTH_LONG).show());
            }
        });
        t.setDaemon(true);
        t.start();
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

    // ── Browser PiP WebView ───────────────────────────────────────────────────

    @SuppressLint("SetJavaScriptEnabled")
    private void setupBrowserWebView() {
        float density = getResources().getDisplayMetrics().density;

        browserPanel = new FrameLayout(this);
        browserPanel.setBackgroundColor(0xFF111111);
        browserPanel.setVisibility(View.GONE);

        int panelW = (int)(360 * density);
        int panelH = (int)(300 * density);

        FrameLayout.LayoutParams panelParams = new FrameLayout.LayoutParams(panelW, panelH);
        panelParams.gravity     = Gravity.TOP | Gravity.END;
        panelParams.topMargin   = (int)(20 * density);
        panelParams.rightMargin = (int)(12 * density);

        browserWebView = new WebView(this);
        WebSettings bs = browserWebView.getSettings();
        bs.setJavaScriptEnabled(true);
        bs.setDomStorageEnabled(true);
        bs.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        bs.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/120.0.0.0 Mobile Safari/537.36");
        WebView.setWebContentsDebuggingEnabled(true);

        // Offset the WebView below the JS handle strip (36 dp)
        int handleH = (int)(36 * density);
        FrameLayout.LayoutParams wvParams = new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT);
        wvParams.topMargin = handleH;

        browserWebView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                // Inject page-event forwarding script on every load
                view.evaluateJavascript(
                    "(function(){" +
                    "  if(window.__aiEventsAttached) return;" +
                    "  window.__aiEventsAttached=true;" +
                    "  function post(t,d){fetch('/browser_event',{method:'POST'," +
                    "    headers:{'Content-Type':'application/json'}," +
                    "    body:JSON.stringify({type:t,data:d,ts:Date.now()})}).catch(function(){});}" +
                    "  window.addEventListener('load',function(){" +
                    "    post('load',{url:location.href,title:document.title});});" +
                    "  var st;window.addEventListener('scroll',function(){" +
                    "    clearTimeout(st);st=setTimeout(function(){" +
                    "      post('scroll',{x:scrollX,y:scrollY});},300);});" +
                    "})()", null);
                // Sync URL bar in the chat WebView
                final String safeUrl = url.replace("'", "\\'");
                mainHandler.post(() -> webView.evaluateJavascript(
                    "if(typeof browserPanelSetUrl==='function') browserPanelSetUrl('" + safeUrl + "')", null));
            }
        });

        browserPanel.addView(browserWebView, wvParams);
        addContentView(browserPanel, panelParams);
    }

    /** Evaluate JS in the browser page and return the result (blocking). */
    public String browserEvalSync(final String js) {
        final CountDownLatch latch = new CountDownLatch(1);
        final String[] out = {""};
        mainHandler.post(() -> {
            if (browserWebView == null) { latch.countDown(); return; }
            browserWebView.evaluateJavascript(js, value -> {
                out[0] = value != null ? value : "null";
                latch.countDown();
            });
        });
        try { latch.await(10, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        // Unwrap JS string quotes added by evaluateJavascript
        String result = out[0];
        if (result.startsWith("\"") && result.endsWith("\"")) {
            try { result = new org.json.JSONArray("[" + result + "]").getString(0); }
            catch (Exception ignored) {}
        }
        return result;
    }

    /** Capture DOM summary + screenshot from the browser page (blocking). */
    public String browserSnapshotSync() {
        final CountDownLatch latch = new CountDownLatch(1);
        final String[] out = {""};
        final String domJs =
            "(function(){" +
            "  var res=[];var els=Array.from(document.querySelectorAll(" +
            "    'h1,h2,h3,h4,a,button,input,textarea,select,p,li')).slice(0,120);" +
            "  els.forEach(function(el,i){" +
            "    var tag=el.tagName.toLowerCase();" +
            "    var text=(el.innerText||el.value||el.placeholder||'').trim().slice(0,100);" +
            "    var sel=tag;" +
            "    if(el.id) sel+='#'+el.id;" +
            "    else if(el.name) sel+='[name=\"'+el.name+'\"]';" +
            "    else if(el.type) sel+='[type='+el.type+']';" +
            "    res.push('['+i+'] <'+sel+'>'+(text?' '+text:''));});" +
            "  return JSON.stringify({url:location.href,title:document.title," +
            "    domSummary:res.join('\\n')});})()";

        mainHandler.post(() -> {
            if (browserWebView == null) { latch.countDown(); return; }
            browserWebView.evaluateJavascript(domJs, domJson -> {
                // Capture bitmap screenshot
                String b64 = "";
                try {
                    browserWebView.setDrawingCacheEnabled(true);
                    Bitmap bmp = Bitmap.createBitmap(browserWebView.getDrawingCache());
                    browserWebView.setDrawingCacheEnabled(false);
                    if (bmp != null) {
                        Bitmap scaled = Bitmap.createScaledBitmap(bmp, 360, 264, true);
                        bmp.recycle();
                        ByteArrayOutputStream baos = new ByteArrayOutputStream();
                        scaled.compress(Bitmap.CompressFormat.PNG, 85, baos);
                        scaled.recycle();
                        b64 = Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP);
                    }
                } catch (Exception ignored) {}
                // Merge into result JSON
                try {
                    String raw = domJson;
                    if (raw != null && raw.startsWith("\"") && raw.endsWith("\"")) {
                        raw = new org.json.JSONArray("[" + raw + "]").getString(0);
                    }
                    org.json.JSONObject obj = new org.json.JSONObject(raw != null ? raw : "{}");
                    obj.put("screenshot", b64);
                    out[0] = obj.toString();
                } catch (Exception e) {
                    out[0] = "{\"error\":\"" + e.getMessage() + "\"}";
                }
                latch.countDown();
            });
        });
        try { latch.await(15, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        return out[0];
    }

    public String fetchUrlSync(final String url) {
        final CountDownLatch latch = new CountDownLatch(1);
        final String[] result = {""};

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
            try {
                html = new org.json.JSONArray("[" + html + "]").getString(0);
            } catch (Exception ignored) {}
        }
        return html;
    }

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

    class AndroidBridge {
        @JavascriptInterface
        public String getWorkingDir() {
            return selectedFolderPath != null ? selectedFolderPath : "";
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
            if (path == null || path.isEmpty()) {
                path = Environment.getExternalStorageDirectory().getAbsolutePath();
            }
            File dir = new File(path);
            if (!dir.exists() || !dir.isDirectory()) {
                return "[]";
            }
            List<String> files = new ArrayList<>();
            File[] items = dir.listFiles();
            if (items != null) {
                for (File f : items) {
                    files.add(f.getName() + (f.isDirectory() ? "/" : ""));
                }
            }
            return files.toString();
        }

        // ── Browser PiP bridge methods ─────────────────────────────────────

        @JavascriptInterface
        public void browserOpen(String url) {
            mainHandler.post(() -> {
                if (browserPanel == null) return;
                browserPanel.setVisibility(View.VISIBLE);
                if (browserWebView != null) browserWebView.loadUrl(url);
                final String safeUrl = url.replace("'", "\\'");
                webView.evaluateJavascript(
                    "if(typeof browserPanelOpen==='function') browserPanelOpen('" + safeUrl + "')", null);
            });
        }

        @JavascriptInterface
        public void browserClose() {
            mainHandler.post(() -> {
                if (browserPanel != null) browserPanel.setVisibility(View.GONE);
                if (browserWebView != null) browserWebView.loadUrl("about:blank");
                webView.evaluateJavascript(
                    "if(typeof browserPanelClose==='function') browserPanelClose()", null);
            });
        }

        @JavascriptInterface
        public void browserLoadUrl(String url) {
            mainHandler.post(() -> {
                if (browserWebView != null) browserWebView.loadUrl(url);
            });
        }

        @JavascriptInterface
        public void browserMove(int xPx, int yPx) {
            mainHandler.post(() -> {
                if (browserPanel == null) return;
                FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) browserPanel.getLayoutParams();
                lp.gravity      = Gravity.TOP | Gravity.START;
                lp.leftMargin   = xPx;
                lp.topMargin    = yPx;
                lp.rightMargin  = 0;
                lp.bottomMargin = 0;
                browserPanel.setLayoutParams(lp);
            });
        }

        @JavascriptInterface
        public void browserResize(int wPx, int hPx) {
            mainHandler.post(() -> {
                if (browserPanel == null) return;
                android.view.ViewGroup.LayoutParams lp = browserPanel.getLayoutParams();
                lp.width  = wPx;
                lp.height = hPx;
                browserPanel.setLayoutParams(lp);
            });
        }
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQUEST_FOLDER_PICKER && resultCode == RESULT_OK && data != null) {
            Uri treeUri = data.getData();
            if (treeUri == null) return;

            getContentResolver().takePersistableUriPermission(treeUri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);

            String path = treeUri.getPath();
            String authority = treeUri.getAuthority();

            if (path != null) {
                if ("com.android.providers.downloads.documents".equals(authority)) {
                    selectedFolderPath = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS).getAbsolutePath();
                } else if (path.contains(":")) {
                    String[] split = path.split(":", 2);
                    String type = split[0];
                    String relativePath = split.length > 1 ? split[1] : "";

                    if (type.endsWith("primary")) {
                        selectedFolderPath = Environment.getExternalStorageDirectory().getAbsolutePath();
                        if (!relativePath.isEmpty()) {
                            selectedFolderPath += "/" + relativePath;
                        }
                    } else {
                        String volumeId = type.substring(type.lastIndexOf('/') + 1);
                        selectedFolderPath = "/storage/" + volumeId;
                        if (!relativePath.isEmpty()) {
                            selectedFolderPath += "/" + relativePath;
                        }
                    }
                } else {
                    selectedFolderPath = path;
                }
            }

            prefs.edit().putString("working_dir", selectedFolderPath).apply();

            final String finalPath = selectedFolderPath;
            mainHandler.postDelayed(() -> {
                if (webView != null) {
                    String escaped = finalPath.replace("\\", "\\\\").replace("'", "\\'");
                    webView.evaluateJavascript("setWorkingDir('" + escaped + "')", null);
                }
            }, 500);
        }
    }

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
