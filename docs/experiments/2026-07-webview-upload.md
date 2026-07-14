# WebView upload probe (Phase 2) — results

**Question (Gate A):** does the `<input type="file">` picker fire and can bytes `fetch`-POST
back, across the surfaces where a mobile user would add a file? The answer decides whether
the Phase 3 **in-app widget** is worth building, or whether the Phase 1 **link flow** is the
whole mobile story.

This plan splits the probe (see `docs/plans/remote-mcp-file-upload-plan-v4.md`):

- **2a — external-browser probe (this doc, automatable):** open the *existing* signed
  `/files/ul/{token}` page — already an `<input type=file>` + `fetch` POST — in the device's
  real browser. This is exactly the Phase 1 link flow. **Zero new code.**
- **2b — in-app iframe probe:** does the picker fire *inside Claude's own app WebView*? Needs
  a `ui://` widget iframe the repo doesn't have yet → it is Phase 3's first (throwaway-if-red)
  commit, **not** a standalone experiment.

## Method

- **Automated (engine-level):** a throwaway harness
  (`scratchpad/probe_2a.py`) starts the real FastMCP `http_app()` with the `/files/*` routes,
  mints a real `ul` token, and drives the page headless in **both** Playwright engines —
  Chromium (Blink) and WebKit (Safari's engine) — attaching a file and asserting the POST adds
  a source. Fresh single-use token per engine.
- **On-device (manual, pending):** run `notebooklm-mcp` with `NOTEBOOKLM_MCP_PUBLIC_URL` set to
  a public HTTPS URL, connect it on the phone, call `source_add(source_type="file")` to mint a
  link, open `human_upload.url` in the device browser AND inside Claude's app, and record each
  cell.

## Results matrix

| Surface | Engine | picker_opened | file_readable | fetch/POST ok | Verdict | Evidence |
|---|---|:--:|:--:|:--:|---|---|
| claude.ai web (Chrome/Edge), Claude Desktop | Blink | ✅ | ✅ | ✅ | **GREEN** | automated (`probe_2a.py`) |
| claude.ai web (Safari), iOS **browser** (Safari) | WebKit | ✅ | ✅ | ✅ | **GREEN** | automated (`probe_2a.py`) |
| Claude **iOS — in-app** WKWebView | WebKit (embedded) | ? | ? | ? | PENDING (device) — this is **2b/Phase 3** | — |
| Claude **Android — in-app** WebView | Blink (embedded) | ? | ? | ? | PENDING (device) — this is **2b/Phase 3** | — |
| ChatGPT (web/mobile) | — | — | — | — | feature-detect `window.openai.uploadFile` | snippet below |

**Prior (unchanged, informs 2b):** iOS WKWebView presents the picker natively with zero host
code — the WebKit-engine GREEN above is consistent with that. Android in-app WebView typically
**no-ops** unless the host app implements `WebChromeClient.onShowFileChooser()` — an
app-embedding property the engine-level probe **cannot** see, so it stays a real device unknown.

## Phase 2a conclusion

**The external-browser link flow works on both engines.** That is the Phase 1 mobile story, and
it is validated end-to-end (picker → file read → POST → source added) — not just assumed. So
regardless of how 2b lands, mobile users have a working path today.

## Incidental findings (feed Phase 3)

- **Page CSP is strict:** `default-src 'none'; script-src 'unsafe-inline'; style-src
  'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'`
  (`_fileroutes.py:_HTML_SECURITY_HEADERS`). `connect-src 'self'` means the page can only
  `fetch` its own origin — fine for Phase 1 (same-origin POST), but **Phase 3's direct-PUT from
  a widget iframe to the server origin will need CORS + a relaxed `connect-src` / `_meta.ui`
  `connectDomains`**, exactly as the plan flags. (Also surfaced because the strict `no
  'unsafe-eval'` CSP blocks Playwright's `eval_on_selector`/`wait_for_function` — the harness
  polls via non-eval APIs.)
- **Single-use `ul` token confirmed live:** reusing one token across the two engine runs
  returned `403` on the second — the jti burned on first success (ADR-0024 / #1746).

## ChatGPT host-upload feature-detect (Phase 5 interop signal)

Run in the ChatGPT client (apps-sdk widget or console) to see if a host-brokered upload exists:

```js
typeof window.openai?.uploadFile === "function"
```

`true` → ChatGPT offers native mobile upload today (the Phase 3 fallback-ladder's top rung, and
the interop evidence for the Phase 5 `ui/uploadFile` proposal).

## Gate A status

- **External-browser / link flow (2a):** ✅ GREEN — proceed with Phase 1 as the portable mobile
  path (done).
- **In-app widget (2b):** ⏳ PENDING the two on-device cells above. Build the `ui://` probe as
  Phase 3's first commit; if the in-app picker is red on a platform, that platform keeps the
  link flow and we escalate upstream (the `ui/uploadFile` proposal), per the plan.
