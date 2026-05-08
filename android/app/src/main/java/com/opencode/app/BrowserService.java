package com.opencode.app;

import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Base64;
import android.util.DisplayMetrics;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;
import android.window.WindowManager;

import androidx.browser.customtabs.CustomTabsIntent;

import java.io.ByteArrayOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

public class BrowserService {

    private static BrowserService instance;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private android.view.WindowManager windowManager;
    private WebView browserWebView;
    private FrameLayout overlayRoot;
    private android.view.WindowManager.LayoutParams overlayParams;
    private TextView headerTitle;
    private boolean isVisible = false;
    private int pageCounter = 1;

    private BrowserService() {}

    public static synchronized BrowserService getInstance() {
        if (instance == null) instance = new BrowserService();
        return instance;
    }

    public boolean hasOverlayPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M || Settings.canDrawOverlays(getActivity());
    }

    public String open(String url) {
        if (!hasOverlayPermission()) {
            return "{\"error\":\"OVERLAY_PERMISSION_REQUIRED\",\"message\":\"Grant display over other apps permission in Settings.\"}";
        }
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> {
            if (isVisible) destroyOverlay();
            createOverlay();
            browserWebView.setWebViewClient(new WebViewClient() {
                @Override
                public void onPageFinished(WebView view, String u) {
                    mainHandler.postDelayed(() -> snapshotInternal(s -> {
                        result.set(s);
                        latch.countDown();
                    }), 900);
                }
            });
            CookieManager cm = CookieManager.getInstance();
            cm.setAcceptCookie(true);
            cm.setAcceptThirdPartyCookies(browserWebView, true);
            browserWebView.loadUrl(url != null && !url.isEmpty() ? url : "about:blank");
            isVisible = true;
        });
        awaitLatch(latch, 25);
        return result.get();
    }

    public String snapshot() {
        if (!isVisible || browserWebView == null) return "{\"error\":\"No browser open. Call browser_open first.\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> snapshotInternal(s -> { result.set(s); latch.countDown(); }));
        awaitLatch(latch, 15);
        return result.get();
    }

    public String click(String uid) {
        return jsEval(
            "(function(){" +
            "var el=document.querySelector('[data-ocuid=\"" + uid + "\"]');" +
            "if(!el)return 'error: uid not found';" +
            "el.scrollIntoView({block:'center'});" +
            "el.focus();" +
            "el.click();" +
            "['mousedown','mouseup','click'].forEach(function(t){" +
            "  el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true}));" +
            "});" +
            "return 'clicked:'+el.tagName;" +
            "})()"
        );
    }

    public String fill(String uid, String value) {
        String safe = value.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$");
        return jsEval(
            "(function(){" +
            "var el=document.querySelector('[data-ocuid=\"" + uid + "\"]');" +
            "if(!el)return 'error: uid not found';" +
            "el.focus();" +
            "var setter=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');" +
            "if(setter&&setter.set){setter.set.call(el,`" + safe + "`);}" +
            "else if(setter&&setter.set){setter.set.call(el,`" + safe + "`);}" +
            "else{el.value=`" + safe + "`;}" +
            "['input','change'].forEach(function(e){" +
            "  el.dispatchEvent(new Event(e,{bubbles:true}));" +
            "});" +
            "return 'filled';" +
            "})()"
        );
    }

    public String navigate(String url) {
        if (!isVisible || browserWebView == null) return "{\"error\":\"No browser open.\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> {
            browserWebView.setWebViewClient(new WebViewClient() {
                @Override
                public void onPageFinished(WebView view, String u) {
                    mainHandler.postDelayed(() -> snapshotInternal(s -> {
                        result.set(s);
                        latch.countDown();
                    }), 900);
                }
            });
            browserWebView.loadUrl(url);
        });
        awaitLatch(latch, 25);
        return result.get();
    }

    public String evaluate(String script) {
        return jsEval("(function(){try{return String(eval(" + jsonString(script) + "));}catch(e){return 'error:'+e.message;}})()");
    }

    public String waitForText(String text, int timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline) {
            String page = jsEval("document.body?document.body.innerText:''");
            if (page.contains(text)) return snapshot();
            try { Thread.sleep(600); } catch (InterruptedException e) { Thread.currentThread().interrupt(); break; }
        }
        return "{\"error\":\"Timeout waiting for: " + text + "\"}";
    }

    public String screenshot() {
        if (!isVisible || browserWebView == null) return "error: no browser open";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("error: capture failed");
        mainHandler.post(() -> {
            try {
                browserWebView.setDrawingCacheEnabled(true);
                Bitmap bm = Bitmap.createBitmap(browserWebView.getWidth(), browserWebView.getHeight(), Bitmap.Config.ARGB_8888);
                android.graphics.Canvas canvas = new android.graphics.Canvas(bm);
                browserWebView.draw(canvas);
                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                bm.compress(Bitmap.CompressFormat.JPEG, 80, baos);
                result.set("data:image/jpeg;base64," + Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP));
            } catch (Exception e) {
                result.set("error: " + e.getMessage());
            }
            latch.countDown();
        });
        awaitLatch(latch, 10);
        return result.get();
    }

    public String getCookies(String url) {
        String c = CookieManager.getInstance().getCookie(url);
        return c != null ? c : "";
    }

    public void openCCT(String url) {
        mainHandler.post(() -> {
            try {
                CustomTabsIntent cct = new CustomTabsIntent.Builder().setShowTitle(true).build();
                cct.launchUrl(getActivity(), Uri.parse(url));
            } catch (Exception e) {
                Toast.makeText(getActivity(), "Could not open browser: " + e.getMessage(), Toast.LENGTH_SHORT).show();
            }
        });
    }

    public String close() {
        mainHandler.post(this::destroyOverlay);
        return "Browser closed.";
    }

    public String getCurrentUrl() {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> {
            if (browserWebView != null) {
                String u = browserWebView.getUrl();
                result.set(u != null ? u : "");
            }
            latch.countDown();
        });
        awaitLatch(latch, 3);
        return result.get();
    }

    public boolean isOpen() {
        return isVisible && browserWebView != null;
    }

    private void snapshotInternal(Consumer<String> cb) {
        String js = buildSnapshotJs();
        browserWebView.evaluateJavascript(js, raw -> {
            String url = browserWebView.getUrl() != null ? browserWebView.getUrl() : "";
            String title = browserWebView.getTitle() != null ? browserWebView.getTitle() : "";
            String tree = unquoteJs(raw);
            cb.accept("{\"url\":\"" + esc(url) + "\",\"title\":\"" + esc(title) + "\",\"snapshot\":" + (tree != null ? tree : "null") + "}");
        });
    }

    private String buildSnapshotJs() {
        int pid = pageCounter;
        return "(function(){" +
            "var uid=1;" +
            "var TAGS='a,button,input,textarea,select,[role=button],[role=link],[role=checkbox],[role=menuitem],[role=tab],[role=option],[contenteditable=true]';" +
            "function proc(el,d){" +
            "  if(d>9||!el)return null;" +
            "  if(el.nodeType===3){var t=el.textContent.trim();return t?{t:'txt',v:t.substring(0,200)}:null;}" +
            "  if(el.nodeType!==1)return null;" +
            "  var tag=el.tagName.toLowerCase();" +
            "  if(['script','style','svg','noscript','head','meta','link'].includes(tag))return null;" +
            "  var inter=el.matches&&el.matches(TAGS);" +
            "  var id=null;" +
            "  if(inter){id='" + pid + "_'+(uid++);el.setAttribute('data-ocuid',id);}" +
            "  var r={tag:tag};" +
            "  if(id)r.uid=id;" +
            "  var al=el.getAttribute&&el.getAttribute('aria-label');" +
            "  var ph=el.getAttribute&&el.getAttribute('placeholder');" +
            "  var tp=el.getAttribute&&el.getAttribute('type');" +
            "  var hr=el.getAttribute&&el.getAttribute('href');" +
            "  var rl=el.getAttribute&&el.getAttribute('role');" +
            "  var vl=el.value;" +
            "  if(al)r.label=al.substring(0,100);" +
            "  if(ph)r.placeholder=ph.substring(0,100);" +
            "  if(tp)r.type=tp;" +
            "  if(hr)r.href=hr.substring(0,200);" +
            "  if(rl)r.role=rl;" +
            "  if(vl!==undefined&&vl!=='')r.value=String(vl).substring(0,100);" +
            "  if(inter){var txt=el.innerText?el.innerText.trim().substring(0,200):'';if(txt)r.text=txt;}" +
            "  if(!inter){" +
            "    var kids=[];" +
            "    el.childNodes.forEach(function(c){var n=proc(c,d+1);if(n)kids.push(n);});" +
            "    if(kids.length)r.children=kids.slice(0,50);" +
            "  }" +
            "  return r;" +
            "}" +
            "var root=document.body||document.documentElement;" +
            "return JSON.stringify(proc(root,0));" +
            "})()";
    }

    private String jsEval(String script) {
        if (!isVisible || browserWebView == null) return "error: no browser open";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> browserWebView.evaluateJavascript(script, raw -> {
            result.set(unquoteJs(raw) != null ? unquoteJs(raw) : "");
            latch.countDown();
        }));
        awaitLatch(latch, 10);
        return result.get();
    }

    @SuppressLint({"SetJavaScriptEnabled", "ClickableViewAccessibility"})
    private void createOverlay() {
        android.app.Activity activity = getActivity();
        windowManager = (android.view.WindowManager) activity.getSystemService(Context.WINDOW_SERVICE);

        DisplayMetrics dm = activity.getResources().getDisplayMetrics();
        int w = (int) (dm.widthPixels * 0.94f);
        int h = (int) (dm.heightPixels * 0.52f);

        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? android.view.WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : android.view.WindowManager.LayoutParams.TYPE_PHONE;

        overlayParams = new android.view.WindowManager.LayoutParams(
                w, h, type,
                android.view.WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT
        );
        overlayParams.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        overlayParams.y = 80;

        overlayRoot = new FrameLayout(activity);
        overlayRoot.setBackgroundColor(0xFF121212);

        LinearLayout header = new LinearLayout(activity);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setBackgroundColor(0xFF1E1E1E);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(12), 0, dp(12), 0);

        FrameLayout.LayoutParams headerLp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(42));
        header.setLayoutParams(headerLp);

        headerTitle = new TextView(activity);
        headerTitle.setTextColor(0xFFCCCCCC);
        headerTitle.setTextSize(13);
        headerTitle.setSingleLine(true);
        headerTitle.setEllipsize(android.text.TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams titleLp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        headerTitle.setLayoutParams(titleLp);
        headerTitle.setText("Browser");

        TextView closeBtn = new TextView(activity);
        closeBtn.setText("✕");
        closeBtn.setTextColor(0xFF777777);
        closeBtn.setTextSize(18);
        closeBtn.setPadding(dp(12), 0, 0, 0);
        closeBtn.setOnClickListener(v -> destroyOverlay());

        header.addView(headerTitle);
        header.addView(closeBtn);

        browserWebView = new WebView(activity);
        FrameLayout.LayoutParams wvLp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        wvLp.topMargin = dp(42);
        browserWebView.setLayoutParams(wvLp);

        WebSettings s = browserWebView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setSupportZoom(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/120.0.0.0 Mobile Safari/537.36");

        browserWebView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onReceivedTitle(WebView view, String title) {
                mainHandler.post(() -> { if (headerTitle != null && title != null) headerTitle.setText(title); });
            }
        });

        final int[] dragStart = {0, 0, 0, 0};
        header.setOnTouchListener((v, ev) -> {
            switch (ev.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    dragStart[0] = (int) ev.getRawX();
                    dragStart[1] = (int) ev.getRawY();
                    dragStart[2] = overlayParams.x;
                    dragStart[3] = overlayParams.y;
                    break;
                case MotionEvent.ACTION_MOVE:
                    overlayParams.x = dragStart[2] + (int) ev.getRawX() - dragStart[0];
                    overlayParams.y = dragStart[3] - ((int) ev.getRawY() - dragStart[1]);
                    if (overlayRoot != null) {
                        try { windowManager.updateViewLayout(overlayRoot, overlayParams); } catch (Exception ignored) {}
                    }
                    break;
            }
            return true;
        });

        overlayRoot.addView(header);
        overlayRoot.addView(browserWebView);
        windowManager.addView(overlayRoot, overlayParams);
    }

    private void destroyOverlay() {
        if (overlayRoot != null) {
            try { windowManager.removeView(overlayRoot); } catch (Exception ignored) {}
            overlayRoot = null;
        }
        if (browserWebView != null) {
            browserWebView.stopLoading();
            browserWebView.destroy();
            browserWebView = null;
        }
        headerTitle = null;
        isVisible = false;
        pageCounter++;
    }

    private android.app.Activity getActivity() {
        return MainActivity.instance;
    }

    private int dp(int v) {
        return (int) (v * getActivity().getResources().getDisplayMetrics().density);
    }

    private void awaitLatch(CountDownLatch latch, int seconds) {
        try { latch.await(seconds, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }

    private String unquoteJs(String raw) {
        if (raw == null || raw.equals("null")) return null;
        if (raw.length() >= 2 && raw.charAt(0) == '"' && raw.charAt(raw.length() - 1) == '"') {
            return raw.substring(1, raw.length() - 1)
                .replace("\\\"", "\"")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\\\", "\\");
        }
        return raw;
    }

    private String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "");
    }

    private String jsonString(String s) {
        return "\"" + esc(s) + "\"";
    }
}
