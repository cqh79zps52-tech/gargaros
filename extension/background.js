// Gargaros Bridge — service worker.
// Long-polls /browser/_pull, dispatches jobs to a content script in the active tab,
// posts results back to /browser/_result. Reconnects with backoff on failure.

const STATE = {
  url: null,
  token: null,
  running: false,
  backoff: 1000, // ms
};

async function loadConfig() {
  const data = await chrome.storage.local.get(["gargarosUrl", "gargarosToken"]);
  STATE.url = (data.gargarosUrl || "http://127.0.0.1:7331").replace(/\/+$/, "");
  STATE.token = data.gargarosToken || null;
}

function authHeaders() {
  return STATE.token ? { Authorization: "Bearer " + STATE.token } : {};
}

async function ensureContentScript(tabId) {
  // Inject content.js if it isn't already present (idempotent: it self-marks).
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
    });
  } catch (e) {
    // Pages like chrome:// or the Web Store reject injection — caller will get an error.
    throw new Error("cannot inject content script: " + e.message);
  }
}

async function activeTabId(explicit) {
  if (typeof explicit === "number") return explicit;
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (tabs.length === 0) throw new Error("no active tab");
  return tabs[0].id;
}

async function dispatchJob(job) {
  const op = job.op;
  const args = job.args || {};

  if (op === "tabs") {
    const tabs = await chrome.tabs.query({});
    return {
      tabs: tabs.map((t) => ({
        id: t.id,
        url: t.url,
        title: t.title,
        active: t.active,
        windowId: t.windowId,
      })),
    };
  }

  if (op === "navigate") {
    const tabId = await activeTabId(args.tab_id);
    await chrome.tabs.update(tabId, { url: args.url });
    if (args.wait_for_load) {
      await new Promise((resolve) => {
        const listener = (id, info) => {
          if (id === tabId && info.status === "complete") {
            chrome.tabs.onUpdated.removeListener(listener);
            resolve();
          }
        };
        chrome.tabs.onUpdated.addListener(listener);
        setTimeout(() => {
          chrome.tabs.onUpdated.removeListener(listener);
          resolve();
        }, 15000);
      });
    }
    const updated = await chrome.tabs.get(tabId);
    return { ok: true, tab_id: tabId, final_url: updated.url };
  }

  // snapshot / click / type all run in the page via the content script
  const tabId = await activeTabId(args.tab_id);
  await ensureContentScript(tabId);

  if (op === "snapshot") {
    const snap = await chrome.tabs.sendMessage(tabId, { type: "snapshot" });
    let screenshot_b64 = null;
    if (args.include_screenshot) {
      try {
        const tab = await chrome.tabs.get(tabId);
        const dataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, {
          format: "jpeg",
          quality: 80,
        });
        // strip "data:image/jpeg;base64," prefix
        screenshot_b64 = dataUrl.split(",", 2)[1] || null;
      } catch (e) {
        // captureVisibleTab can fail on chrome:// pages; carry on without
      }
    }
    return { ...snap, screenshot_b64 };
  }

  if (op === "click") {
    const r = await chrome.tabs.sendMessage(tabId, {
      type: "click",
      selector: args.selector,
    });
    if (r && r.error) throw new Error(r.error);
    return r;
  }

  if (op === "type") {
    const r = await chrome.tabs.sendMessage(tabId, {
      type: "type",
      selector: args.selector,
      text: args.text,
      clear: args.clear,
      press_enter: args.press_enter,
    });
    if (r && r.error) throw new Error(r.error);
    return r;
  }

  throw new Error("unknown op: " + op);
}

async function pollOnce() {
  // Re-read config every cycle so options-page changes take effect within one poll.
  await loadConfig();
  if (!STATE.token) {
    // Don't hammer the server — wait for the user to set a token in options.
    await sleep(5000);
    return;
  }
  const url = STATE.url + "/browser/_pull?wait=25";
  let r;
  try {
    r = await fetch(url, { headers: authHeaders() });
  } catch (e) {
    await sleep(STATE.backoff);
    STATE.backoff = Math.min(STATE.backoff * 2, 30000);
    return;
  }
  STATE.backoff = 1000; // reset on any successful round-trip

  if (r.status === 204) return; // no jobs queued; loop and re-poll
  if (r.status === 401 || r.status === 403) {
    console.warn("[gargaros] auth rejected by server; check options page");
    await sleep(10000);
    return;
  }
  if (!r.ok) {
    console.warn("[gargaros] _pull returned", r.status);
    await sleep(2000);
    return;
  }

  const job = await r.json();
  let result, error;
  try {
    result = await dispatchJob(job);
  } catch (e) {
    error = String(e && e.message ? e.message : e);
  }
  await fetch(STATE.url + "/browser/_result", {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: job.job_id, result, error }),
  });
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function loop() {
  if (STATE.running) return;
  STATE.running = true;
  await loadConfig();
  while (true) {
    try {
      await pollOnce();
    } catch (e) {
      console.error("[gargaros] poll error", e);
      await sleep(STATE.backoff);
      STATE.backoff = Math.min(STATE.backoff * 2, 30000);
    }
  }
}

chrome.runtime.onInstalled.addListener(loop);
chrome.runtime.onStartup.addListener(loop);
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "config-changed") {
    loadConfig();
  }
});

// Pick up token/URL changes from the options page immediately.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && (changes.gargarosUrl || changes.gargarosToken)) {
    loadConfig();
  }
});

// Kick the loop on every service-worker wake-up.
loop();
