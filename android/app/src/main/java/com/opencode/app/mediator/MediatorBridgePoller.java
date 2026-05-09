package com.opencode.app.mediator;

import android.util.Log;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * MediatorBridgePoller — one per script session.
 *
 * Polls  GET  http://localhost:5000/mediator/eval/<scriptId>
 *   → if pending=true: runs the JS in MediatorWebView
 *   → POSTs result to  POST http://localhost:5000/mediator/eval/<scriptId>
 *
 * The Python side blocks waiting for the result; this loop delivers it.
 */
public class MediatorBridgePoller {

    private static final String TAG       = "MediatorBridge";
    private static final String BASE      = "http://localhost:5000";
    private static final int    POLL_MS   = 300;   // fast poll while session active
    private static final int    EVAL_TIMEOUT_MS = 30_000;

    // Active pollers keyed by scriptId
    private static final ConcurrentHashMap<String, MediatorBridgePoller> ACTIVE =
        new ConcurrentHashMap<>();

    private final String           scriptId;
    private final MediatorWebView  webView;
    private final AtomicBoolean    running = new AtomicBoolean(false);
    private Thread                 thread;

    private MediatorBridgePoller(String scriptId, MediatorWebView webView) {
        this.scriptId = scriptId;
        this.webView  = webView;
    }

    // ── Static lifecycle ───────────────────────────────────────────────────────

    public static void start(String scriptId, MediatorWebView webView) {
        stop(scriptId); // stop any existing poller for this id
        MediatorBridgePoller poller = new MediatorBridgePoller(scriptId, webView);
        ACTIVE.put(scriptId, poller);
        poller.startLoop();
        Log.i(TAG, "Bridge poller started for: " + scriptId);
    }

    public static void stop(String scriptId) {
        MediatorBridgePoller existing = ACTIVE.remove(scriptId);
        if (existing != null) {
            existing.stopLoop();
            Log.i(TAG, "Bridge poller stopped for: " + scriptId);
        }
    }

    public static void stopAll() {
        for (String id : ACTIVE.keySet()) stop(id);
    }

    // ── Poller loop ────────────────────────────────────────────────────────────

    private void startLoop() {
        running.set(true);
        thread = new Thread(() -> {
            while (running.get()) {
                try {
                    pollOnce();
                    Thread.sleep(POLL_MS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    break;
                } catch (Exception e) {
                    Log.w(TAG, "Poll error [" + scriptId + "]: " + e.getMessage());
                    try { Thread.sleep(1000); } catch (InterruptedException ie) { break; }
                }
            }
        }, "mediator-bridge-" + scriptId);
        thread.setDaemon(true);
        thread.start();
    }

    private void stopLoop() {
        running.set(false);
        if (thread != null) thread.interrupt();
    }

    private void pollOnce() throws Exception {
        String pollUrl = BASE + "/mediator/eval/" + scriptId;
        String body    = httpGet(pollUrl);
        if (body == null) return;

        JSONObject json = new JSONObject(body);
        if (!json.optBoolean("pending", false)) return;

        String reqId = json.getString("id");
        String js    = json.getString("js");

        Log.d(TAG, "[" + scriptId + "] eval req " + reqId + ": " + js.substring(0, Math.min(80, js.length())));

        // Execute JS synchronously in the WebView
        String result = webView.evaluateJs(js, EVAL_TIMEOUT_MS);

        Log.d(TAG, "[" + scriptId + "] eval result: " + result);

        // Post result back to Python
        JSONObject resultBody = new JSONObject();
        resultBody.put("id", reqId);
        resultBody.put("result", result != null ? result : JSONObject.NULL);
        httpPost(pollUrl, resultBody.toString());
    }

    // ── HTTP helpers ───────────────────────────────────────────────────────────

    private String httpGet(String urlStr) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(urlStr).openConnection();
            c.setRequestMethod("GET");
            c.setConnectTimeout(5_000);
            c.setReadTimeout(5_000);
            int code = c.getResponseCode();
            if (code != 200) return null;
            BufferedReader r = new BufferedReader(
                new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
            r.close();
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    private void httpPost(String urlStr, String jsonBody) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(urlStr).openConnection();
            c.setRequestMethod("POST");
            c.setRequestProperty("Content-Type", "application/json");
            c.setDoOutput(true);
            c.setConnectTimeout(5_000);
            c.setReadTimeout(5_000);
            byte[] bytes = jsonBody.getBytes(StandardCharsets.UTF_8);
            try (OutputStream os = c.getOutputStream()) { os.write(bytes); }
            c.getResponseCode(); // consume response
            c.disconnect();
        } catch (Exception e) {
            Log.w(TAG, "httpPost error: " + e.getMessage());
        }
    }
}
