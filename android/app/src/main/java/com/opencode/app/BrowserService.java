package com.opencode.app;

import android.animation.ValueAnimator;
import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Outline;
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
import android.view.View;
import android.view.ViewGroup;
import android.view.ViewOutlineProvider;
import android.view.WindowManager;
import android.view.animation.DecelerateInterpolator;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.browser.customtabs.CustomTabsIntent;

import java.io.ByteArrayOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;

public class BrowserService {

    private static BrowserService instance;

    private static final int CORNER_DP    = 18;
    private static final int HEADER_DP    = 44;
    private static final int TAB_DP       = 46;
    private static final int SNAP_EDGE_DP = 72;
    private static final int ANIM_MS      = 260;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private WindowManager              windowManager;
    private FrameLayout                overlayRoot;
    private WebView                    browserWebView;
    private WindowManager.LayoutParams overlayParams;
    private TextView                   headerTitle;
    private View                       leftTab;
    private View                       rightTab;

    private boolean isVisible = false;
    private boolean isSnapped = false;
    private int     snapSide  = 0;       // -1 = left edge, +1 = right edge
    private int     lastFreeX = 0;
    private int     lastFreeY = 80;
    private int     pageCounter = 1;

    private int displayW, overlayW, overlayH;

    private BrowserService() {}

    public static synchronized BrowserService getInstance() {
        if (instance == null) instance = new BrowserService();
        return instance;
    }

    // ── Public API ──────────────────────────────────────────────────────

    public boolean hasOverlayPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                || Settings.canDrawOverlays(getActivity());
    }

    public String open(String url) {
        if (!hasOverlayPermission())
            return "{\"error\":\"OVERLAY_PERMISSION_REQUIRED\"}";

        CountDownLatch latch  = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");

        mainHandler.post(() -> {
            if (isVisible) destroyOverlay();
            createOverlay();
            browserWebView.setWebViewClient(new WebViewClient() {
                @Override public void onPageFinished(WebView v, String u) {
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
        if (!isVisible || browserWebView == null)
            return "{\"error\":\"No browser open.\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> snapshotInternal(s -> { result.set(s); latch.countDown(); }));
        awaitLatch(latch, 15);
        return result.get();
    }

    public String click(String uid) {
        return jsEval(
            "(function(){var el=document.querySelector('[data-ocuid=\"" + uid + "\"]');" +
            "if(!el)return 'error: uid not found';" +
            "el.scrollIntoView({block:'center'});el.focus();" +
            "['mousedown','mouseup','click'].forEach(function(t){" +
            "el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true}));});" +
            "return 'clicked:'+el.tagName;})()"
        );
    }

    public String fill(String uid, String value) {
        String safe = value.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$");
        return jsEval(
            "(function(){var el=document.querySelector('[data-ocuid=\"" + uid + "\"]');" +
            "if(!el)return 'error: uid not found';" +
            "el.focus();" +
            "var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');" +
            "if(d&&d.set)d.set.call(el,`" + safe + "`);else el.value=`" + safe + "`;" +
            "['input','change'].forEach(function(e){el.dispatchEvent(new Event(e,{bubbles:true}));});" +
            "return 'filled';})()"
        );
    }

    public String navigate(String url) {
        if (!isVisible || browserWebView == null) return "{\"error\":\"No browser open.\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> {
            browserWebView.setWebViewClient(new WebViewClient() {
                @Override public void onPageFinished(WebView v, String u) {
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
                Bitmap bm = Bitmap.createBitmap(
                        browserWebView.getWidth(), browserWebView.getHeight(), Bitmap.Config.ARGB_8888);
                browserWebView.draw(new android.graphics.Canvas(bm));
                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                bm.compress(Bitmap.CompressFormat.JPEG, 80, baos);
                result.set("data:image/jpeg;base64," +
                        Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP));
            } catch (Exception e) { result.set("error: " + e.getMessage()); }
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
                new CustomTabsIntent.Builder().setShowTitle(true).build()
                        .launchUrl(getActivity(), Uri.parse(url));
            } catch (Exception e) {
                Toast.makeText(getActivity(), "Cannot open browser: " + e.getMessage(),
                        Toast.LENGTH_SHORT).show();
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

    public boolean isOpen() { return isVisible && browserWebView != null; }

    // ── Overlay creation ─────────────────────────────────────────────────

    @SuppressLint({"SetJavaScriptEnabled", "ClickableViewAccessibility"})
    private void createOverlay() {
        android.app.Activity activity = getActivity();
        windowManager = (WindowManager) activity.getSystemService(Context.WINDOW_SERVICE);

        DisplayMetrics dm = activity.getResources().getDisplayMetrics();
        displayW  = dm.widthPixels;
        overlayW  = (int) (displayW * 0.94f);
        overlayH  = (int) (dm.heightPixels * 0.52f);

        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;

        overlayParams = new WindowManager.LayoutParams(
                overlayW, overlayH, type,
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        overlayParams.gravity = Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL;
        overlayParams.x = lastFreeX;
        overlayParams.y = lastFreeY;

        // Root with rounded corners
        overlayRoot = new FrameLayout(activity);
        overlayRoot.setBackgroundColor(0xFF111111);
        overlayRoot.setClipToOutline(true);
        overlayRoot.setOutlineProvider(new ViewOutlineProvider() {
            @Override public void getOutline(View view, Outline outline) {
                outline.setRoundRect(0, 0, view.getWidth(), view.getHeight(), dp(CORNER_DP));
            }
        });

        buildHeader(activity);
        buildWebView(activity);
        buildEdgeTabs(activity);

        windowManager.addView(overlayRoot, overlayParams);
    }

    private void buildHeader(android.app.Activity activity) {
        LinearLayout header = new LinearLayout(activity);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setBackgroundColor(0xFF1C1C1E);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(14), 0, dp(14), 0);

        FrameLayout.LayoutParams hlp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(HEADER_DP));
        header.setLayoutParams(hlp);

        // Drag handle dot
        TextView handle = new TextView(activity);
        handle.setText("⠿");
        handle.setTextColor(0xFF555555);
        handle.setTextSize(16);
        handle.setPadding(0, 0, dp(10), 0);

        headerTitle = new TextView(activity);
        headerTitle.setTextColor(0xFFBBBBBB);
        headerTitle.setTextSize(12.5f);
        headerTitle.setSingleLine(true);
        headerTitle.setEllipsize(android.text.TextUtils.TruncateAt.END);
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        headerTitle.setLayoutParams(tlp);
        headerTitle.setText("Browser");

        TextView closeBtn = new TextView(activity);
        closeBtn.setText("✕");
        closeBtn.setTextColor(0xFF666666);
        closeBtn.setTextSize(17);
        closeBtn.setPadding(dp(12), 0, 0, 0);
        closeBtn.setOnClickListener(v -> destroyOverlay());

        header.addView(handle);
        header.addView(headerTitle);
        header.addView(closeBtn);

        final int[] down = {0, 0, 0, 0};
        final boolean[] dragging = {false};

        header.setOnTouchListener((v, ev) -> {
            switch (ev.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    down[0] = (int) ev.getRawX();
                    down[1] = (int) ev.getRawY();
                    down[2] = overlayParams.x;
                    down[3] = overlayParams.y;
                    dragging[0] = false;
                    if (isSnapped) {
                        unsnap();
                        return true;
                    }
                    break;

                case MotionEvent.ACTION_MOVE:
                    int dx = (int) ev.getRawX() - down[0];
                    int dy = (int) ev.getRawY() - down[1];
                    if (!dragging[0] && Math.abs(dx) + Math.abs(dy) > dp(6)) dragging[0] = true;
                    if (dragging[0]) {
                        overlayParams.x = down[2] + dx;
                        overlayParams.y = down[3] - dy;
                        try { windowManager.updateViewLayout(overlayRoot, overlayParams); }
                        catch (Exception ignored) {}
                    }
                    break;

                case MotionEvent.ACTION_UP:
                    if (dragging[0]) checkEdgeSnap();
                    else if (isSnapped) unsnap();
                    break;
            }
            return true;
        });

        overlayRoot.addView(header);
    }

    private void buildWebView(android.app.Activity activity) {
        browserWebView = new WebView(activity);
        FrameLayout.LayoutParams wlp = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        wlp.topMargin = dp(HEADER_DP);
        browserWebView.setLayoutParams(wlp);

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
            @Override public void onReceivedTitle(WebView view, String title) {
                mainHandler.post(() -> {
                    if (headerTitle != null && title != null) headerTitle.setText(title);
                });
            }
        });

        overlayRoot.addView(browserWebView);
    }

    private void buildEdgeTabs(android.app.Activity activity) {
        // Left-side tab (visible when snapped to right edge — right portion of window hidden)
        leftTab = makeTab(activity, "›");
        FrameLayout.LayoutParams llp = new FrameLayout.LayoutParams(dp(TAB_DP),
                ViewGroup.LayoutParams.MATCH_PARENT);
        llp.gravity = Gravity.START | Gravity.CENTER_VERTICAL;
        leftTab.setLayoutParams(llp);
        leftTab.setVisibility(View.GONE);
        leftTab.setOnClickListener(v -> unsnap());
        overlayRoot.addView(leftTab);

        // Right-side tab (visible when snapped to left edge)
        rightTab = makeTab(activity, "‹");
        FrameLayout.LayoutParams rlp = new FrameLayout.LayoutParams(dp(TAB_DP),
                ViewGroup.LayoutParams.MATCH_PARENT);
        rlp.gravity = Gravity.END | Gravity.CENTER_VERTICAL;
        rightTab.setLayoutParams(rlp);
        rightTab.setVisibility(View.GONE);
        rightTab.setOnClickListener(v -> unsnap());
        overlayRoot.addView(rightTab);
    }

    private View makeTab(android.app.Activity activity, String arrow) {
        android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
        bg.setColor(0xCC1A73E8);
        bg.setCornerRadius(dp(CORNER_DP));
        TextView tab = new TextView(activity);
        tab.setText(arrow);
        tab.setTextColor(0xFFFFFFFF);
        tab.setTextSize(22);
        tab.setGravity(Gravity.CENTER);
        tab.setBackground(bg);
        return tab;
    }

    // ── Edge snap logic ──────────────────────────────────────────────────

    private void checkEdgeSnap() {
        int leftEdge  = (displayW - overlayW) / 2 + overlayParams.x;
        int rightEdge = (displayW + overlayW) / 2 + overlayParams.x;
        int threshold = dp(SNAP_EDGE_DP);

        if (leftEdge < threshold) {
            snapTo(-1);
        } else if (rightEdge > displayW - threshold) {
            snapTo(1);
        } else {
            lastFreeX = overlayParams.x;
            lastFreeY = overlayParams.y;
        }
    }

    private void snapTo(int side) {
        isSnapped = true;
        snapSide  = side;
        lastFreeX = overlayParams.x;
        lastFreeY = overlayParams.y;

        int tabPx   = dp(TAB_DP);
        int halfSum  = (displayW + overlayW) / 2;
        int targetX  = (side == -1)
                ? -(halfSum - tabPx)   // slide left, right tab visible
                :  (halfSum - tabPx);  // slide right, left tab visible

        animateX(overlayParams.x, targetX, () -> {
            if (leftTab  != null) leftTab.setVisibility(side == 1 ? View.VISIBLE : View.GONE);
            if (rightTab != null) rightTab.setVisibility(side == -1 ? View.VISIBLE : View.GONE);
        });
    }

    private void unsnap() {
        if (leftTab  != null) leftTab.setVisibility(View.GONE);
        if (rightTab != null) rightTab.setVisibility(View.GONE);
        isSnapped = false;
        animateX(overlayParams.x, lastFreeX, null);
    }

    private void animateX(int from, int to, Runnable onEnd) {
        ValueAnimator anim = ValueAnimator.ofInt(from, to);
        anim.setDuration(ANIM_MS);
        anim.setInterpolator(new DecelerateInterpolator());
        anim.addUpdateListener(a -> {
            if (overlayRoot == null) return;
            overlayParams.x = (int) a.getAnimatedValue();
            try { windowManager.updateViewLayout(overlayRoot, overlayParams); }
            catch (Exception ignored) {}
        });
        if (onEnd != null) anim.addListener(new android.animation.AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(android.animation.Animator a) { onEnd.run(); }
        });
        anim.start();
    }

    // ── Snapshot / JS ────────────────────────────────────────────────────

    private void snapshotInternal(Consumer<String> cb) {
        browserWebView.evaluateJavascript(buildSnapshotJs(), raw -> {
            String url   = browserWebView.getUrl()   != null ? browserWebView.getUrl()   : "";
            String title = browserWebView.getTitle() != null ? browserWebView.getTitle() : "";
            String tree  = unquoteJs(raw);
            cb.accept("{\"url\":\"" + esc(url) + "\",\"title\":\"" + esc(title) +
                      "\",\"snapshot\":" + (tree != null ? tree : "null") + "}");
        });
    }

    private String buildSnapshotJs() {
        int pid = pageCounter;
        return "(function(){var uid=1;" +
            "var TAGS='a,button,input,textarea,select,[role=button],[role=link]," +
            "[role=checkbox],[role=menuitem],[role=tab],[role=option],[contenteditable=true]';" +
            "function proc(el,d){" +
            "if(d>9||!el)return null;" +
            "if(el.nodeType===3){var t=el.textContent.trim();return t?{t:'txt',v:t.substring(0,200)}:null;}" +
            "if(el.nodeType!==1)return null;" +
            "var tag=el.tagName.toLowerCase();" +
            "if(['script','style','svg','noscript','head','meta','link'].includes(tag))return null;" +
            "var inter=el.matches&&el.matches(TAGS);" +
            "var id=null;" +
            "if(inter){id='" + pid + "_'+(uid++);el.setAttribute('data-ocuid',id);}" +
            "var r={tag:tag};" +
            "if(id)r.uid=id;" +
            "var al=el.getAttribute&&el.getAttribute('aria-label');" +
            "var ph=el.getAttribute&&el.getAttribute('placeholder');" +
            "var tp=el.getAttribute&&el.getAttribute('type');" +
            "var hr=el.getAttribute&&el.getAttribute('href');" +
            "var rl=el.getAttribute&&el.getAttribute('role');" +
            "var vl=el.value;" +
            "if(al)r.label=al.substring(0,100);" +
            "if(ph)r.placeholder=ph.substring(0,100);" +
            "if(tp)r.type=tp;" +
            "if(hr)r.href=hr.substring(0,200);" +
            "if(rl)r.role=rl;" +
            "if(vl!==undefined&&vl!=='')r.value=String(vl).substring(0,100);" +
            "if(inter){var txt=el.innerText?el.innerText.trim().substring(0,200):'';if(txt)r.text=txt;}" +
            "if(!inter){var kids=[];el.childNodes.forEach(function(c){var n=proc(c,d+1);if(n)kids.push(n);});" +
            "if(kids.length)r.children=kids.slice(0,50);}" +
            "return r;}" +
            "var root=document.body||document.documentElement;" +
            "return JSON.stringify(proc(root,0));})()";
    }

    private String jsEval(String script) {
        if (!isVisible || browserWebView == null) return "error: no browser open";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> browserWebView.evaluateJavascript(script, raw -> {
            String v = unquoteJs(raw);
            result.set(v != null ? v : "");
            latch.countDown();
        }));
        awaitLatch(latch, 10);
        return result.get();
    }

    // ── Teardown ─────────────────────────────────────────────────────────

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
        leftTab     = null;
        rightTab    = null;
        isVisible   = false;
        isSnapped   = false;
        pageCounter++;
    }

    // ── Helpers ──────────────────────────────────────────────────────────

    private android.app.Activity getActivity() { return MainActivity.instance; }

    private int dp(int v) {
        return (int) (v * getActivity().getResources().getDisplayMetrics().density);
    }

    private void awaitLatch(CountDownLatch latch, int seconds) {
        try { latch.await(seconds, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }

    private String unquoteJs(String raw) {
        if (raw == null || raw.equals("null")) return null;
        if (raw.length() >= 2 && raw.charAt(0) == '"' && raw.charAt(raw.length()-1) == '"') {
            return raw.substring(1, raw.length()-1)
                .replace("\\\"", "\"").replace("\\n", "\n")
                .replace("\\t", "\t").replace("\\\\", "\\");
        }
        return raw;
    }

    private String esc(String s) {
        return s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","");
    }

    private String jsonString(String s) { return "\"" + esc(s) + "\""; }
}
