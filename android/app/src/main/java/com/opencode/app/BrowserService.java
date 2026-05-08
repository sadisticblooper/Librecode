package com.opencode.app;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.ValueAnimator;
import android.annotation.SuppressLint;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Outline;
import android.graphics.Paint;
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

    private static final int CORNER_DP      = 16;
    private static final int HEADER_DP      = 42;
    private static final int RESIZE_DP      = 32;
    private static final int SNAP_THRESH_DP = 60;
    private static final int MIN_W_DP       = 160;
    private static final int MIN_H_DP       = 140;
    private static final int ANIM_MS        = 230;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private WindowManager              windowManager;
    private FrameLayout                overlayRoot;
    private WebView                    browserWebView;
    private WindowManager.LayoutParams overlayParams;
    private TextView                   headerTitle;
    private View                       leftTab;
    private View                       rightTab;

    private boolean isVisible  = false;
    private boolean isSnapped  = false;
    private int     snapSide   = 0;
    private int     lastFreeX, lastFreeY;
    private int     pageCounter = 1;
    private int     displayW, displayH;

    private BrowserService() {}

    public static synchronized BrowserService getInstance() {
        if (instance == null) instance = new BrowserService();
        return instance;
    }

    public boolean hasOverlayPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                || Settings.canDrawOverlays(getActivity());
    }

    public String open(String url) {
        if (!hasOverlayPermission())
            return "{\"error\":\"OVERLAY_PERMISSION_REQUIRED\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> {
            if (isVisible) destroyOverlay();
            createOverlay();
            browserWebView.setWebViewClient(new WebViewClient() {
                @Override public void onPageFinished(WebView v, String u) {
                    // Flush cookies to disk immediately so they persist across chats
                    CookieManager.getInstance().flush();
                    injectFocusListeners(v);
                    mainHandler.postDelayed(() -> snapshotInternal(s -> {
                        result.set(s); latch.countDown();
                    }), 900);
                }
            });
            // CookieManager already configured in buildWebView; just load the URL.
            browserWebView.loadUrl(url != null && !url.isEmpty() ? url : "about:blank");
            isVisible = true;
        });
        awaitLatch(latch, 25);
        return result.get();
    }

    public String snapshot() {
        if (!isVisible || browserWebView == null) return "{\"error\":\"Browser is not open.\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> snapshotInternal(s -> { result.set(s); latch.countDown(); }));
        awaitLatch(latch, 15);
        String r = result.get();
        if (r == null || r.isEmpty())
            return "{\"error\":\"Snapshot timed out — page may still be loading.\"}";
        return r;
    }

    public String click(String uid) {
        return jsEval(
            "(function(){" +
            // Shadow-DOM-aware UID finder (recursive BFS through open shadow roots)
            "function _ocFind(uid){var q='[data-ocuid=\"'+uid+'\"]';" +
            "function s(root){var el=root.querySelector(q);if(el)return el;" +
            "var all=root.querySelectorAll('*');" +
            "for(var i=0;i<all.length;i++){if(all[i].shadowRoot){var f=s(all[i].shadowRoot);if(f)return f;}}" +
            "return null;}return s(document);}" +
            "var el=_ocFind(\"" + uid + "\");" +
            "if(!el)return 'error: uid not found';" +
            "el.scrollIntoView({block:'center'});el.focus();" +
            // composed:true lets synthetic events cross shadow DOM boundaries
            "['mousedown','mouseup','click'].forEach(function(t){" +
            "el.dispatchEvent(new MouseEvent(t,{bubbles:true,cancelable:true,composed:true}));});" +
            "return 'clicked:'+el.tagName;})()"
        );
    }

    public String fill(String uid, String value) {
        String safe = value.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$");
        return jsEval(
            "(function(){" +
            "function _ocFind(uid){var q='[data-ocuid=\"'+uid+'\"]';" +
            "function s(root){var el=root.querySelector(q);if(el)return el;" +
            "var all=root.querySelectorAll('*');" +
            "for(var i=0;i<all.length;i++){if(all[i].shadowRoot){var f=s(all[i].shadowRoot);if(f)return f;}}" +
            "return null;}return s(document);}" +
            "var el=_ocFind(\"" + uid + "\");" +
            "if(!el)return 'error: uid not found';" +
            "el.focus();" +
            "var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value');" +
            "if(d&&d.set)d.set.call(el,`" + safe + "`);else el.value=`" + safe + "`;" +
            "['input','change'].forEach(function(e){el.dispatchEvent(new Event(e,{bubbles:true,composed:true}));});" +
            "return 'filled';})()"
        );
    }

    public String navigate(String url) {
        if (!isVisible || browserWebView == null) return "{\"error\":\"Browser is not open.\"}";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> {
            browserWebView.setWebViewClient(new WebViewClient() {
                @Override public void onPageFinished(WebView v, String u) {
                    CookieManager.getInstance().flush();
                    injectFocusListeners(v);
                    mainHandler.postDelayed(() -> snapshotInternal(s -> {
                        result.set(s); latch.countDown();
                    }), 900);
                }
            });
            browserWebView.loadUrl(url);
        });
        awaitLatch(latch, 25);
        return result.get();
    }

    public String evaluate(String script) {
        // Do NOT use eval() here — pages with strict CSP (e.g. GitHub) block it.
        // WebView.evaluateJavascript() is a privileged native injection that bypasses
        // CSP entirely, so we can embed the script directly as an IIFE.
        if (!isVisible || browserWebView == null) return "error: no browser open";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        // Wrap result in JSON.stringify so the WebView callback always receives a
        // properly-quoted string.  String(undefined) would come back as the literal
        // "undefined" which is indistinguishable from a script that returns the
        // string "undefined".  JSON.stringify(undefined) → null, which we surface
        // as a clear "(no return value)" message; all other values round-trip correctly.
        String wrapped = "(function(){try{var _r=(function(){\n" + script + "\n})();return JSON.stringify(_r===undefined?null:_r);}catch(e){return JSON.stringify('error:'+e.message);}})()";

        mainHandler.post(() -> browserWebView.evaluateJavascript(wrapped, raw -> {
            String v = unquoteJs(raw); result.set(v != null ? v : ""); latch.countDown();
        }));
        awaitLatch(latch, 10);
        return result.get();
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
                Bitmap bm = Bitmap.createBitmap(browserWebView.getWidth(), browserWebView.getHeight(), Bitmap.Config.ARGB_8888);
                browserWebView.draw(new Canvas(bm));
                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                bm.compress(Bitmap.CompressFormat.JPEG, 80, baos);
                result.set("data:image/jpeg;base64," + Base64.encodeToString(baos.toByteArray(), Base64.NO_WRAP));
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
                new CustomTabsIntent.Builder().setShowTitle(true).build().launchUrl(getActivity(), Uri.parse(url));
            } catch (Exception e) {
                Toast.makeText(getActivity(), "Cannot open browser: " + e.getMessage(), Toast.LENGTH_SHORT).show();
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
            if (browserWebView != null) { String u = browserWebView.getUrl(); result.set(u != null ? u : ""); }
            latch.countDown();
        });
        awaitLatch(latch, 3);
        return result.get();
    }

    public boolean isOpen() { return isVisible && browserWebView != null; }

    // ─────────────────────────────────────────────────────────────────────
    // Overlay creation
    // ─────────────────────────────────────────────────────────────────────

    @SuppressLint({"SetJavaScriptEnabled", "ClickableViewAccessibility"})
    private void createOverlay() {
        android.app.Activity activity = getActivity();
        windowManager = (WindowManager) activity.getSystemService(Context.WINDOW_SERVICE);

        DisplayMetrics dm = activity.getResources().getDisplayMetrics();
        displayW = dm.widthPixels;
        displayH = dm.heightPixels;

        // 50 % × 50 % initial size
        int initW = (int) (displayW * 0.50f);
        int initH = (int) (displayH * 0.50f);

        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;

        // FLAG_NOT_TOUCH_MODAL  → touches outside the window reach the app behind it
        // FLAG_LAYOUT_NO_LIMITS → window can slide partially off-screen
        // FLAG_NOT_FOCUSABLE    → overlay never steals IME focus from the main app;
        //                         we clear this flag dynamically when a browser input is
        //                         focused and restore it on blur (see _ocFocus JS interface)
        overlayParams = new WindowManager.LayoutParams(
                initW, initH, type,
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL |
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS |
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,
                PixelFormat.TRANSLUCENT);

        // Keyboard resizes the overlay content area when it appears.
        overlayParams.softInputMode =
                WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE |
                WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED;

        // TOP|START: x = left edge, y = top edge (simple screen coordinates)
        overlayParams.gravity = Gravity.TOP | Gravity.START;
        overlayParams.x = (displayW - initW) / 2;   // centered
        overlayParams.y = (displayH - initH) / 2;
        lastFreeX = overlayParams.x;
        lastFreeY = overlayParams.y;

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
        buildResizeGrip(activity);
        buildEdgeTabs(activity);

        windowManager.addView(overlayRoot, overlayParams);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Header — drag handle
    // ─────────────────────────────────────────────────────────────────────

    @SuppressLint("ClickableViewAccessibility")
    private void buildHeader(android.app.Activity activity) {
        LinearLayout header = new LinearLayout(activity);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setBackgroundColor(0xFF1C1C1E);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(14), 0, dp(14), 0);
        header.setLayoutParams(new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(HEADER_DP)));

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
        LinearLayout.LayoutParams tlp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
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

        final int[] down     = {0, 0, 0, 0};  // rawX, rawY, initX, initY
        final boolean[] drag = {false};

        header.setOnTouchListener((v, ev) -> {
            switch (ev.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    down[0] = (int) ev.getRawX(); down[1] = (int) ev.getRawY();
                    down[2] = overlayParams.x;    down[3] = overlayParams.y;
                    drag[0] = false;
                    if (isSnapped) { unsnap(); return true; }
                    break;
                case MotionEvent.ACTION_MOVE:
                    int dx = (int) ev.getRawX() - down[0];
                    int dy = (int) ev.getRawY() - down[1];
                    if (!drag[0] && Math.abs(dx) + Math.abs(dy) > dp(6)) drag[0] = true;
                    if (drag[0]) {
                        // TOP|START: x/y are direct screen coords, both axes increase normally
                        overlayParams.x = down[2] + dx;
                        overlayParams.y = down[3] + dy;
                        try { windowManager.updateViewLayout(overlayRoot, overlayParams); }
                        catch (Exception ignored) {}
                    }
                    break;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (drag[0]) checkEdgeSnap();
                    drag[0] = false;
                    break;
            }
            return true;
        });

        overlayRoot.addView(header);
    }

    // ─────────────────────────────────────────────────────────────────────
    // WebView
    // ─────────────────────────────────────────────────────────────────────

    private void buildWebView(android.app.Activity activity) {
        // ── Persistent browser data inside opencode/browser_data ────────────────
        // Cookies, localStorage, cache and WebSQL all live here so logins persist
        // across chats and app restarts.
        java.io.File opencodeDir   = new java.io.File(
                android.os.Environment.getExternalStorageDirectory(), "opencode");
        java.io.File browserDataDir  = new java.io.File(opencodeDir, "browser_data");
        browserDataDir.mkdirs();

        // Suffix routes WebView's internal data (localStorage, WebSQL) to a named
        // subdirectory under the app's data folder so it is stable across launches.
        android.webkit.WebView.setDataDirectorySuffix("opencode_browser");

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
        s.setUserAgentString("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36");

        // Accept and persist cookies now — before any page loads
        CookieManager cm = CookieManager.getInstance();
        cm.setAcceptCookie(true);
        cm.setAcceptThirdPartyCookies(browserWebView, true);

        browserWebView.setWebChromeClient(new WebChromeClient() {
            @Override public void onReceivedTitle(WebView view, String title) {
                mainHandler.post(() -> { if (headerTitle != null && title != null) headerTitle.setText(title); });
            }
        });

        // Dynamic focus bridge: remove FLAG_NOT_FOCUSABLE when a browser input is
        // focused so the keyboard can appear, restore it on blur so the main app
        // can reclaim IME focus when the user taps outside the overlay.
        browserWebView.addJavascriptInterface(new Object() {
            @android.webkit.JavascriptInterface
            public void onFocused() {
                mainHandler.post(() -> {
                    if (overlayParams == null || overlayRoot == null || windowManager == null) return;
                    overlayParams.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
                    try {
                        windowManager.updateViewLayout(overlayRoot, overlayParams);
                        if (browserWebView != null) browserWebView.requestFocus();
                    } catch (Exception ignored) {}
                });
            }
            @android.webkit.JavascriptInterface
            public void onBlurred() {
                mainHandler.post(() -> {
                    if (overlayParams == null || overlayRoot == null || windowManager == null) return;
                    overlayParams.flags |= WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
                    try { windowManager.updateViewLayout(overlayRoot, overlayParams); }
                    catch (Exception ignored) {}
                });
            }
        }, "_ocFocus");

        overlayRoot.addView(browserWebView);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Resize grip — bottom-right corner
    // ─────────────────────────────────────────────────────────────────────

    @SuppressLint("ClickableViewAccessibility")
    private void buildResizeGrip(android.app.Activity activity) {
        View grip = new View(activity) {
            private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);
            {
                p.setColor(0x55FFFFFF);
                p.setStrokeWidth(dp(1.5f));
                p.setStrokeCap(Paint.Cap.ROUND);
                setWillNotDraw(false);
            }
            @Override protected void onDraw(Canvas c) {
                int w = getWidth(), h = getHeight(), g = dp(5);
                c.drawLine(w - g,     h - g * 3, w - g * 3, h - g,     p);
                c.drawLine(w - g,     h - g * 5, w - g * 5, h - g,     p);
                c.drawLine(w - g,     h - g * 7, w - g * 7, h - g,     p);
            }
        };

        FrameLayout.LayoutParams glp = new FrameLayout.LayoutParams(dp(RESIZE_DP), dp(RESIZE_DP));
        glp.gravity = Gravity.BOTTOM | Gravity.END;
        grip.setLayoutParams(glp);

        final int[] rd = {0, 0, 0, 0};  // downRawX, downRawY, initW, initH

        grip.setOnTouchListener((v, ev) -> {
            switch (ev.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    rd[0] = (int) ev.getRawX(); rd[1] = (int) ev.getRawY();
                    rd[2] = overlayParams.width; rd[3] = overlayParams.height;
                    break;
                case MotionEvent.ACTION_MOVE:
                    // Resize bottom-right: stretch width/height, top-left stays fixed
                    overlayParams.width  = Math.max(dp(MIN_W_DP), rd[2] + (int) ev.getRawX() - rd[0]);
                    overlayParams.height = Math.max(dp(MIN_H_DP), rd[3] + (int) ev.getRawY() - rd[1]);
                    try { windowManager.updateViewLayout(overlayRoot, overlayParams); }
                    catch (Exception ignored) {}
                    break;
            }
            return true;
        });

        overlayRoot.addView(grip);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Edge overlays — shown when window is tucked to a screen edge.
    // A transparent full-window overlay intercepts any touch on the peeking
    // strip so the user can drag or tap anywhere to unsnap.
    // ─────────────────────────────────────────────────────────────────────

    private void buildEdgeTabs(android.app.Activity activity) {
        // Instead of a small pill button, we use a transparent full-size overlay that
        // sits on top of overlayRoot. When snapped, it is VISIBLE and intercepts ALL
        // touches anywhere on the peeking strip — shape/size of the window doesn't matter.
        // When not snapped, it is GONE so all touches reach the WebView normally.

        // rightTab → shown when window is snapped LEFT (user pulls from the right strip)
        rightTab = new View(activity);
        rightTab.setBackgroundColor(0x00000000); // fully transparent
        rightTab.setLayoutParams(new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        rightTab.setVisibility(View.GONE);
        attachPullListener(rightTab, +1);
        overlayRoot.addView(rightTab);

        // leftTab → shown when window is snapped RIGHT (user pulls from the left strip)
        leftTab = new View(activity);
        leftTab.setBackgroundColor(0x00000000);
        leftTab.setLayoutParams(new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        leftTab.setVisibility(View.GONE);
        attachPullListener(leftTab, -1);
        overlayRoot.addView(leftTab);
    }

    /**
     * Attach a touch listener that unsnapps the window when the user taps or
     * drags anywhere on the transparent overlay that covers the snapped window.
     * pullDir = +1 → snapped left, drag rightward to unsnap.
     * pullDir = -1 → snapped right, drag leftward to unsnap.
     */
    @SuppressLint("ClickableViewAccessibility")
    private void attachPullListener(View tab, int pullDir) {
        final float[] downRawX = {0f};
        final boolean[] didUnsnap = {false};
        tab.setOnTouchListener((v, ev) -> {
            switch (ev.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    downRawX[0] = ev.getRawX();
                    didUnsnap[0] = false;
                    break;
                case MotionEvent.ACTION_MOVE:
                    if (!didUnsnap[0]) {
                        float dragPx = (ev.getRawX() - downRawX[0]) * pullDir;
                        if (dragPx > dp(6)) {  // low threshold — any swipe unsnapps
                            didUnsnap[0] = true;
                            unsnap();
                        }
                    }
                    break;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    if (!didUnsnap[0]) {
                        // Any tap also unsnapps
                        float dragPx = Math.abs(ev.getRawX() - downRawX[0]);
                        if (dragPx < dp(16)) unsnap();
                    }
                    break;
            }
            return true;
        });
    }

    // ─────────────────────────────────────────────────────────────────────
    // Edge-snap logic
    // ─────────────────────────────────────────────────────────────────────

    private void checkEdgeSnap() {
        int winLeft  = overlayParams.x;
        int winRight = overlayParams.x + overlayParams.width;
        int threshold = dp(SNAP_THRESH_DP);

        if (winLeft < threshold) {
            snapTo(-1);
        } else if (winRight > displayW - threshold) {
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

        int peekPx = dp(32); // dp of the window that remain visible on-screen as a pull handle
        int w      = overlayParams.width;

        // side=-1 → slide left until only peekPx pixels remain on the right edge
        // side=+1 → slide right until only peekPx pixels remain on the left edge
        int targetX = (side == -1) ? -(w - peekPx) : (displayW - peekPx);

        animateX(overlayParams.x, targetX, () -> {
            // Show the transparent full-window overlay so any touch on the peeking
            // strip (regardless of window shape/size) triggers the unsnap.
            if (rightTab != null) rightTab.setVisibility(side == -1 ? View.VISIBLE : View.GONE);
            if (leftTab  != null)  leftTab.setVisibility(side == +1 ? View.VISIBLE : View.GONE);
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
        if (onEnd != null) anim.addListener(new AnimatorListenerAdapter() {
            @Override public void onAnimationEnd(Animator a) { onEnd.run(); }
        });
        anim.start();
    }

    // ─────────────────────────────────────────────────────────────────────
    // Focus-bridge injection — called after every page load.
    // Wires focusin/focusout on inputs → _ocFocus Java interface so the
    // overlay window can gain/lose FLAG_NOT_FOCUSABLE dynamically.
    // ─────────────────────────────────────────────────────────────────────

    private void injectFocusListeners(WebView v) {
        v.evaluateJavascript(
            "(function(){" +
            "if(window._ocFocusInstalled)return;" +
            "window._ocFocusInstalled=true;" +
            "var SEL='input,textarea,select,[contenteditable=true]';" +
            "document.addEventListener('focusin',function(e){" +
            "  if(e.target.matches&&e.target.matches(SEL)&&window._ocFocus)" +
            "    window._ocFocus.onFocused();" +
            "},true);" +
            "document.addEventListener('focusout',function(e){" +
            "  if(e.target.matches&&e.target.matches(SEL)&&window._ocFocus)" +
            "    window._ocFocus.onBlurred();" +
            "},true);" +
            "})()", null);
    }

    // ─────────────────────────────────────────────────────────────────────
    // Snapshot / JS helpers
    // ─────────────────────────────────────────────────────────────────────

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
            "if(d>20||!el)return null;" +
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
            // For non-interactive elements: recurse into childNodes AND shadow root
            "if(!inter){var kids=[];" +
            "el.childNodes.forEach(function(c){var n=proc(c,d+1);if(n)kids.push(n);});" +
            // Shadow DOM: if element has an open shadow root, walk it too
            "if(el.shadowRoot){el.shadowRoot.childNodes.forEach(function(c){var n=proc(c,d+1);if(n)kids.push(n);});}" +
            "if(kids.length)r.children=kids.slice(0,60);}" +
            "return r;}" +
            "var root=document.body||document.documentElement;" +
            "return JSON.stringify(proc(root,0));})()";
    }

    private String jsEval(String script) {
        if (!isVisible || browserWebView == null) return "error: no browser open";
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<String> result = new AtomicReference<>("");
        mainHandler.post(() -> browserWebView.evaluateJavascript(script, raw -> {
            String v = unquoteJs(raw); result.set(v != null ? v : ""); latch.countDown();
        }));
        awaitLatch(latch, 10);
        return result.get();
    }

    // ─────────────────────────────────────────────────────────────────────
    // Teardown
    // ─────────────────────────────────────────────────────────────────────

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

    // ─────────────────────────────────────────────────────────────────────
    // Util
    // ─────────────────────────────────────────────────────────────────────

    private android.app.Activity getActivity() { return MainActivity.instance; }

    private int dp(int v) { return (int) (v * getActivity().getResources().getDisplayMetrics().density); }
    private float dp(float v) { return v * getActivity().getResources().getDisplayMetrics().density; }

    private void awaitLatch(CountDownLatch latch, int seconds) {
        try { latch.await(seconds, TimeUnit.SECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }
    }

    private String unquoteJs(String raw) {
        if (raw == null || raw.equals("null")) return null;
        if (raw.length() >= 2 && raw.charAt(0) == '"' && raw.charAt(raw.length() - 1) == '"') {
            return raw.substring(1, raw.length() - 1)
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
