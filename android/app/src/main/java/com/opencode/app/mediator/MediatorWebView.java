package com.opencode.app.mediator;

import android.annotation.SuppressLint;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * MediatorWebView — WebView that stays logged into a target site (debug: 400dp visible).
 *
 * Shares cookie storage with the main BrowserService WebView automatically
 * (CookieManager is a singleton). User logs in once in the visible browser;
 * this WebView inherits the session for free.
 */
public class MediatorWebView {

    private final WebView webView;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private volatile boolean ready = false;

    @SuppressLint("SetJavaScriptEnabled")
    public MediatorWebView(Context context, FrameLayout hiddenContainer) {
        webView = new WebView(context);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        s.setUserAgentString(
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) " +
            "AppleWebKit/537.36 (KHTML, like Gecko) " +
            "Chrome/120.0.0.0 Mobile Safari/537.36"
        );

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                ready = true;
            }
        });

        // Attach hidden 1×1 to the container
        mainHandler.post(() -> {
            // Debug: fill the visible 400dp container so ChatGPT renders at a real viewport
            android.widget.FrameLayout.LayoutParams lp = new android.widget.FrameLayout.LayoutParams(
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT
            );
            webView.setLayoutParams(lp);
            hiddenContainer.setVisibility(android.view.View.VISIBLE);
            hiddenContainer.addView(webView);
        });
    }

    /** Navigate to a URL and wait for page load (blocks calling thread). */
    public void load(String url, int timeoutMs) {
        ready = false;
        mainHandler.post(() -> webView.loadUrl(url));
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (!ready && System.currentTimeMillis() < deadline) {
            try { Thread.sleep(100); } catch (InterruptedException e) { break; }
        }
    }

    /**
     * Run JS synchronously in the WebView (blocks calling thread up to timeoutMs).
     * Returns the string result, or null on timeout / JS null return.
     */
    public String evaluateJs(String js, int timeoutMs) {
        final CountDownLatch latch = new CountDownLatch(1);
        final String[] result = {null};

        mainHandler.post(() ->
            webView.evaluateJavascript(js, value -> {
                if (value != null && !value.equals("null")) {
                    // Strip surrounding quotes from JSON string
                    if (value.startsWith("\"") && value.endsWith("\"")) {
                        result[0] = value.substring(1, value.length() - 1)
                                         .replace("\\\"", "\"")
                                         .replace("\\n", "\n")
                                         .replace("\\\\", "\\");
                    } else {
                        result[0] = value;
                    }
                }
                latch.countDown();
            })
        );

        try { latch.await(timeoutMs, TimeUnit.MILLISECONDS); }
        catch (InterruptedException e) { Thread.currentThread().interrupt(); }

        return result[0];
    }

    public boolean isReady() { return ready; }

    public void destroy() {
        mainHandler.post(() -> {
            webView.stopLoading();
            webView.destroy();
        });
    }
}
