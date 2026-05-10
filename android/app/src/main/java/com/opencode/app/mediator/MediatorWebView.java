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
 * MediatorWebView — WebView that stays logged into a target site.
 *
 * Shares cookie storage with the main BrowserService WebView automatically
 * (CookieManager is a singleton). User logs in once in the visible browser;
 * this WebView inherits the session for free.
 *
 * DEBUG: container is 400dp visible — loadUrl fires immediately on load()
 * so you can see the page without waiting for a full message round-trip.
 */
public class MediatorWebView {

    private final WebView  webView;
    private final Handler  mainHandler = new Handler(Looper.getMainLooper());
    private volatile boolean ready     = false;

    // DEBUG: kept so load() can fire loadUrl even before the view is attached
    private volatile boolean viewAttached = false;
    private volatile String  pendingUrl   = null;

    @SuppressLint("SetJavaScriptEnabled")
    public MediatorWebView(Context context, FrameLayout container) {
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

        mainHandler.post(() -> {
            FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
            );
            webView.setLayoutParams(lp);
            container.setVisibility(View.VISIBLE);
            container.addView(webView);
            viewAttached = true;
            // DEBUG: if load() was already called before the view attached, fire it now
            if (pendingUrl != null) {
                webView.loadUrl(pendingUrl);
            }
        });
    }

    /** Navigate to a URL and wait for page load (blocks calling thread). */
    public void load(String url, int timeoutMs) {
        ready      = false;
        pendingUrl = url;
        mainHandler.post(() -> {
            // Only call loadUrl if the view is already in the hierarchy;
            // otherwise the constructor's mainHandler.post will fire it via pendingUrl.
            if (viewAttached) {
                webView.loadUrl(url);
            }
        });
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
        final CountDownLatch latch    = new CountDownLatch(1);
        final String[]       result   = {null};

        mainHandler.post(() ->
            webView.evaluateJavascript(js, value -> {
                if (value != null && !value.equals("null")) {
                    if (value.startsWith("\"") && value.endsWith("\"")) {
                        result[0] = value.substring(1, value.length() - 1)
                                         .replace("\\\"", "\"")
                                         .replace("\\n",  "\n")
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
