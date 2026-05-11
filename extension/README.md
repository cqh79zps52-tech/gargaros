# Gargaros Bridge — browser extension

A Manifest V3 Chromium extension (Chrome / Edge / Brave) that lets a running Gargaros server drive the active browser tab — DOM-level snapshot, click by selector, type by selector, navigate, list tabs.

## Install (developer mode)

1. Start Gargaros (in another terminal): `python -m gargaros`. It prints a bearer token and saves it to `%APPDATA%\Gargaros\token`.
2. Open `chrome://extensions` (or `edge://extensions`).
3. Enable **Developer mode** (top-right toggle).
4. Click **Load unpacked** and select `D:\Work\Brain1\Gargaros\extension\`.
5. Click the extension's **Details → Extension options**.
6. Paste the bearer token. Click **Save**. The status line should show `saved (Gargaros vX.Y.Z, backend alive)`.
7. The service worker starts long-polling immediately. To watch it: on the extension's row click **Service worker** to open the DevTools console; you should see no errors.

## What it does

| Gargaros endpoint | What the extension does in your active tab |
|---|---|
| `POST /browser/snapshot` | Returns `{url, title, viewport, scroll, doc_height, text, clickable[], inputs[], screenshot_b64}` — clickable elements include CSS selectors so an agent can click by name not pixel. |
| `POST /browser/click {selector}` | Calls `el.click()` after `scrollIntoView`. |
| `POST /browser/type {selector,text,clear,press_enter}` | Sets value via the native setter so React/Vue/etc. notice, fires input/change events; on press_enter dispatches Enter and tries `form.requestSubmit()`. |
| `POST /browser/navigate {url,wait_for_load}` | Updates the tab; optionally awaits `status === "complete"`. |
| `GET /browser/tabs` | Lists every open tab. |

## Architecture

```
HTTP client → Gargaros (Litestar)        ← long-poll →   ext service worker
                  │                                            │
                  └─ enqueue job in BrowserBridge              └─ chrome.tabs.sendMessage
                                                                 to content.js in tab
                                                                 │
                                                                 └─ DOM ops, sendResponse
                  ◄────── POST /browser/_result ◄───────── result/error
```

The extension never accepts incoming connections; it only initiates outbound long-polls to `127.0.0.1:7331`. Auth is the same bearer token Gargaros uses everywhere.

## Permissions

- `tabs`, `scripting`, `activeTab` — to inject `content.js` and call `chrome.tabs.captureVisibleTab` / `update`
- `storage` — to persist the URL + token across worker restarts
- `host_permissions: <all_urls>` — required to inject the content script into arbitrary pages

The extension does **not** request `webRequest`, `cookies`, or persistent identifiers.

## Limitations

- `chrome://*` pages and the Web Store reject script injection — calls there return an `cannot inject content script` error.
- Multi-frame pages: snapshot only inspects the top frame. Same for click/type.
- Selectors are heuristic: `id` is preferred, then tag + classes + nth-of-type. Hand-tuned selectors via the `selector` field in `clickable[]` work best.
