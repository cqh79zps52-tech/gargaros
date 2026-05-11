const $ = (id) => document.getElementById(id);

async function load() {
  const data = await chrome.storage.local.get(["gargarosUrl", "gargarosToken"]);
  $("url").value = data.gargarosUrl || "http://127.0.0.1:7331";
  $("token").value = data.gargarosToken || "";
}

async function save() {
  const url = $("url").value.trim().replace(/\/+$/, "");
  const token = $("token").value.trim();
  await chrome.storage.local.set({ gargarosUrl: url, gargarosToken: token });

  // Probe /health to give immediate feedback
  $("status").textContent = "saving…";
  $("status").className = "";
  try {
    const r = await fetch(url + "/health", { headers: { Authorization: "Bearer " + token } });
    if (r.ok) {
      const body = await r.json();
      $("status").textContent = ` saved (Gargaros v${body.version}, backend ${body.backend_alive ? "alive" : "down"})`;
      $("status").className = "ok";
    } else {
      $("status").textContent = ` saved, but /health returned ${r.status}`;
      $("status").className = "err";
    }
  } catch (e) {
    $("status").textContent = ` saved, but couldn't reach ${url}: ${e.message}`;
    $("status").className = "err";
  }

  // Notify the service worker so it picks up the new token without a restart
  try {
    chrome.runtime.sendMessage({ type: "config-changed" });
  } catch {
    // service worker may be sleeping; it will pick up new config on next wake
  }
}

document.addEventListener("DOMContentLoaded", load);
$("save").addEventListener("click", save);
