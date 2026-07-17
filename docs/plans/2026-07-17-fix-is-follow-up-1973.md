# Plan (v2): Fix `is_follow_up` over-reporting on a first question (#1973)

**Status:** Revised after momus (Claude + Codex, both REJECT v1 — incorporated)
**Target:** 0.8.0b5
**Issue:** #1973 (regression from the #1965 fix, shipped in 0.8.0b4)

## Problem (verified against source)

`chat_ask` with no `conversation_id` reports `is_follow_up: true` for the
**first-ever** question to a notebook. `_chat/api.py:441-445` sets
`override = None if current_id in deleted else current_id;
is_follow_up = override is not None` — True whenever `get_conversation_id`
(hPTbtc) returns any non-None current id, **including an empty conversation**.
Live, hPTbtc returns a non-None id for a notebook with zero user turns (the
backend tracks a current conversation before any question), so the first question
reports `is_follow_up: true`.

Confirmed by both reviewers:
- hPTbtc carries **id only, no turn count** (`_row_adapters/chat.py:135-160`,
  `api.py:508`).
- The POST's streamed id is a **per-stream id, not a conversation id** (#659;
  `wire.py:71-88`, `api.py:339-348`) — no usable server turn index.
- The b4 test that hid the bug mocks first-ask hPTbtc → `[[[]]]`→None
  (`tests/unit/test_conversation.py:46-51,94-98`), forcing the `current_id is
  None` branch; live takes the non-None branch.

## Correct semantic

`is_follow_up` = **True iff this ask continues a conversation that already had
≥1 prior Q&A turn.** Cases:
- Explicit `conversation_id` → True (unchanged).
- Null ask, hPTbtc → None (no current conv) → False (unchanged).
- Null ask, current conv with **≥1 prior turn** → True.
- Null ask, current conv **empty** (0 prior turns) → **False** ← the b4 bug.

## Decision — the fix (resolves both reviewers)

**A pre-POST raw turn-existence probe, inside the per-conversation lock.**

In the null-ask branch (`api.py:435-451`), when `current_id is not None` and
`override is not None` (i.e. not a deleted id), before the POST and **while
holding `_get_conversation_lock(current_id)`**, probe whether the current
conversation has ≥1 server-side turn row, and set `is_follow_up` from it.

Why this exact shape (each point raised by review):

1. **Pre-POST, in the conversation lock, after the `_deleted_conversations`
   recheck (`api.py:436`), skip when `override is None`.** This placement is the
   correctness mechanic: two null-asks resolving the same fresh 0-turn id
   serialize on the conversation lock — the first probes 0→False→POSTs, the
   second wakes and probes 1→True. Probing *before* the lock races both to read
   0; probing *after* the POST always sees ≥1 (its own turn) and is subject to
   post-POST replication lag. A deleted current id keeps `is_follow_up=False`
   (override is None ⇒ we don't probe) — preserves #1875.

2. **Use a RAW turn-row existence check, NOT `get_history`.** Add/So use
   `self._rpc` GET_CONVERSATION_TURNS with `limit=1` and
   `unwrap_conversation_turns(...)`, testing raw row non-emptiness.
   `get_history()` (`api.py:549-582`) parses Q&A *pairs* after reversing, and
   `_parse_turns_to_qa_pairs` (`api.py:603-635`) drops a leading answer-only row
   — so `get_history(limit=1)` can return `[]` while turns exist, flipping a real
   follow-up to False. It **also catches `ChatError`/`NetworkError` and returns
   `[]`** (`api.py:572`), which would silently make a probe failure read as
   `is_follow_up=False`. Both are unacceptable for this bug.

3. **Explicit probe-failure behavior.** The probe must NOT silently fall back to
   `[]`→False. On a khqZz probe error, **let the exception propagate and fail the
   ask** (the null-ask already does live RPCs and can raise ChatError/NetworkError
   — a probe failure is the same class). Do NOT swallow it. (If a future decision
   wants a fallback, it must be explicit and justified; default = propagate.)

4. **Extract a private helper** `_has_prior_server_turns(notebook_id,
   conversation_id) -> bool` so the raw-unwrap + no-swallow policy lives in one
   place and isn't duplicated inline.

5. **Local-cache fast path (agy review) — RECONSIDERED, default = DROP it.** The
   original idea: if `self._cache.get_cached_conversation(current_id)` holds ≥1
   turn, report follow-up and skip the khqZz probe, confining the RPC to the
   cold/stateless case. It is *monotonic-safe in isolation* (a non-empty cache
   proves ≥1 prior turn; an empty cache still probes) and *never fires on the
   stateless remote* (cold every request). **But under a concurrent second client
   (the NotebookLM Web UI on the same account) it introduces a cross-client
   staleness edge:** a same-client delete clears the cache (`api.py:696` +
   `_deleted_conversations`), but a **Web-UI delete** of the current conversation
   does not — so a warm stdio client + `hPTbtc` replication lag can fast-path to
   `is_follow_up=True` on a now-fresh conversation, re-opening the #1973 symptom
   (narrow window, non-corrupting — the cache only sets the boolean; the POST
   always sends `history=None`, so no answer is ever affected).

   **Decision: DROP the fast path — always probe.** The win is one `limit=1` RPC
   on warm-stdio follow-ups only (nothing on the remote), against a cross-client
   correctness edge + an extra branch to test. Always-probing is
   server-authoritative and simpler. If interactive-stdio TTFT later proves this
   matters, re-add the fast path *with* a documented cross-client-delete
   limitation — but do not treat it as load-bearing for correctness. (This
   supersedes the earlier "keep the fast path" framing; see the #1974 review
   thread where the same trade-off was raised.)

### Cost & latency (explicit sign-off required)

Null-ask is the **default** chat path, and this probe is **pre-POST**, so it adds
to time-to-first-token, not just total time. Today the pre-POST cost is 1 RPC
(hPTbtc); with the fast path dropped (point 5), a null ask with a current
conversation adds a second (khqZz `limit=1`) whenever `override is not None`.
Accepted as the cost of a correct, server-authoritative signal, bounded by
`limit=1` existence-only (not a full-history pull). On the stateless remote this
was already the behavior (cache always cold); the only new cost is one `limit=1`
RPC on **warm-stdio** follow-ups — deemed acceptable vs. the cross-client
staleness the fast path would introduce. The explicit-id path is unchanged.

## `turn_number` — deliberately OUT OF SCOPE, but owned (not waved off)

Review split here: fixing `is_follow_up` makes the *pre-existing* `turn_number`
incoherence visible — on the stateless remote `cache_turn` (`api.py:405-414`)
counts the empty per-request local cache, so `turn_number` is ~always 1; after
this fix a real follow-up can report `is_follow_up=True, turn_number=1`, which is
self-contradictory.

Resolution: **do not** expand #1973 to fix `turn_number`. An accurate ordinal
needs a different cost model — a *full* turns fetch + reliable pairing (the raw
row count can't be halved into pairs safely, and pre-POST the newest prior row is
an answer that trips the pairing drop). Deriving it cheaply from the `limit=1`
existence probe is impossible. Forcing it into #1973 risks a rushed change to the
delicate turns/pairing/locking logic. Instead:
- **File a separate issue** ("`turn_number` is client-cache-local and unreliable
  (~always 1) on the stateless remote MCP connector; can contradict
  `is_follow_up`"), milestone 0.8.1.
- **Document** the known transient inconsistency in the `chat_ask`/`AskResult`
  docstring (turn_number reflects the client's locally-cached turns, not the
  server total; unreliable on the stateless remote).

This honors Claude's objection (own it explicitly, don't ship a silent
contradiction) and Codex's scoping (turn_number needs its own cost model).

## Q1 (does hPTbtc return None for a truly-fresh notebook?) — test fidelity, not a blocker

The fix is correct for all three hPTbtc outcomes regardless of Q1: None→False
(unchanged); id+0 turns→probe 0→False (fixed); id+≥1→probe ≥1→True. Q1 only
decides whether the `current_id is None` branch is dead in prod and how faithfully
to mock. Downgraded to "confirm for test realism," not an implementation gate.

## Test plan (incl. existing-fixture fallout)

- **New unit cases** (`tests/unit/test_conversation.py`): null ask, non-None
  current id + khqZz returns 0 rows → `is_follow_up=False` (the #1973 shape the
  old `None` mock missed); + ≥1 row → True; hPTbtc None → False; explicit id →
  True; deleted current id → False (no probe).
- **Probe-failure test**: khqZz raises → the ask raises (not silent False).
- **Warm-cache probes anyway** (fast path dropped, point 5): a null ask with a
  warm client cache for `current_id` still issues the khqZz probe and derives
  `is_follow_up` from the *server* — assert the probe IS called even with a warm
  cache (so no one silently re-adds a cache short-circuit). State each test case's
  cache state explicitly regardless, since a stray cache-based branch would
  otherwise bypass the khqZz mock.
- **Fixture fallout (REQUIRED — the probe breaks existing passing tests):**
  `tests/conftest.py` (~L390 `mock_get_conversation_id`) only mocks hPTbtc; add a
  `mock_get_conversation_turns` / khqZz fixture, and update every existing
  null-ask-with-current-id test (`test_ask_new_conversation`,
  `test_ask_implicit_continue_is_follow_up`, etc.) to mock it or they hit an
  unmocked request and fail.
- **VCR cassette (REQUIRED):** `tests/integration/mcp_vcr/test_chat.py` documents
  exactly three recorded calls; the new probe adds a fourth — update the cassette
  + the documented call count.
- **mypy / ruff / full `pytest -q`** (guardrails full-suite-only).

## Risks
- Hot-path latency/quota: +1 khqZz per null-ask (mitigated: `limit=1` existence
  only).
- Concurrency: probe MUST sit inside `_get_conversation_lock(current_id)` after
  the deleted-id recheck — misplacement reintroduces the resolve-vs-POST race
  (#1875).
- Explicit-id 0-turn edge: an explicitly-passed empty conversation id still
  reports `is_follow_up=True` — knowingly untouched (the caller asserted a
  conversation), noted for transparency.

## Out of scope
- `turn_number` accuracy (separate issue, above).
- Connector manifest staleness (#1956).
