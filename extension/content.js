// Gargaros Bridge — content script.
// Idempotent: safe to inject multiple times; the listener registers only once.

(() => {
  if (window.__gargaros_installed) return;
  window.__gargaros_installed = true;

  const MAX_TEXT = 4000;
  const MAX_ELEMENTS = 200;

  function cssSelectorFor(el) {
    if (!(el instanceof Element)) return null;
    if (el.id) return "#" + CSS.escape(el.id);
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && path.length < 5) {
      let part = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) {
        const cls = [...cur.classList].slice(0, 2).map((c) => "." + CSS.escape(c)).join("");
        part += cls;
      }
      const parent = cur.parentElement;
      if (parent) {
        const sibs = [...parent.children].filter((s) => s.tagName === cur.tagName);
        if (sibs.length > 1) {
          part += `:nth-of-type(${sibs.indexOf(cur) + 1})`;
        }
      }
      path.unshift(part);
      cur = cur.parentElement;
    }
    return path.join(" > ");
  }

  function visibleRect(el) {
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return null;
    if (r.bottom < 0 || r.top > innerHeight) return null;
    if (r.right < 0 || r.left > innerWidth) return null;
    return r;
  }

  function snapshot() {
    const clickable = [];
    const inputs = [];
    const candidates = document.querySelectorAll(
      'a, button, [role="button"], [role="link"], [onclick]'
    );
    for (const el of candidates) {
      if (clickable.length >= MAX_ELEMENTS) break;
      const r = visibleRect(el);
      if (!r) continue;
      const txt = (el.innerText || el.getAttribute("aria-label") || el.title || "").trim();
      if (!txt) continue;
      clickable.push({
        text: txt.slice(0, 200),
        selector: cssSelectorFor(el),
        bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
        tag: el.tagName.toLowerCase(),
      });
    }
    const inputEls = document.querySelectorAll(
      'input:not([type="hidden"]), textarea, select, [contenteditable="true"]'
    );
    for (const el of inputEls) {
      if (inputs.length >= MAX_ELEMENTS) break;
      const r = visibleRect(el);
      if (!r) continue;
      inputs.push({
        selector: cssSelectorFor(el),
        type: el.type || el.tagName.toLowerCase(),
        name: el.name || el.id || el.getAttribute("aria-label") || "",
        placeholder: el.placeholder || "",
        value:
          el.tagName === "INPUT" && el.type === "password"
            ? "(hidden)"
            : (el.value || el.innerText || "").slice(0, 200),
        bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      });
    }

    const text = (document.body && document.body.innerText) || "";

    return {
      url: location.href,
      title: document.title,
      viewport: { width: innerWidth, height: innerHeight },
      scroll: { x: scrollX, y: scrollY },
      doc_height: document.documentElement.scrollHeight,
      text: text.slice(0, MAX_TEXT),
      truncated: text.length > MAX_TEXT,
      clickable,
      inputs,
    };
  }

  function clickSelector(selector) {
    const el = document.querySelector(selector);
    if (!el) throw new Error("selector not found: " + selector);
    el.scrollIntoView({ block: "center", inline: "center" });
    el.click();
    return { ok: true };
  }

  function typeSelector(selector, text, clear, pressEnter) {
    const el = document.querySelector(selector);
    if (!el) throw new Error("selector not found: " + selector);
    el.focus();
    if (clear) {
      if ("value" in el) el.value = "";
      else if (el.isContentEditable) el.innerText = "";
    }
    if ("value" in el) {
      const nativeSetter = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(el),
        "value"
      )?.set;
      const newVal = (clear ? "" : el.value || "") + text;
      if (nativeSetter) nativeSetter.call(el, newVal);
      else el.value = newVal;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    } else if (el.isContentEditable) {
      document.execCommand("insertText", false, text);
    }
    if (pressEnter) {
      el.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true })
      );
      el.dispatchEvent(
        new KeyboardEvent("keypress", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true })
      );
      el.dispatchEvent(
        new KeyboardEvent("keyup", { key: "Enter", code: "Enter", keyCode: 13, bubbles: true })
      );
      // Many forms only react to a real submit via Enter on the form itself
      const form = el.form;
      if (form && typeof form.requestSubmit === "function") {
        try {
          form.requestSubmit();
        } catch {
          // ignore — already dispatched key events
        }
      }
    }
    return { ok: true };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    try {
      if (msg.type === "snapshot") {
        sendResponse(snapshot());
      } else if (msg.type === "click") {
        sendResponse(clickSelector(msg.selector));
      } else if (msg.type === "type") {
        sendResponse(typeSelector(msg.selector, msg.text, msg.clear, msg.press_enter));
      } else {
        sendResponse({ error: "unknown message type" });
      }
    } catch (e) {
      sendResponse({ error: String(e && e.message ? e.message : e) });
    }
    return false; // synchronous reply
  });
})();
