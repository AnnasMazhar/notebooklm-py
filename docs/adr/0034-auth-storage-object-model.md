# ADR-0034: Auth storage object model and incremental extraction

## Status

Accepted. This is the ratified target; production migration is incremental. It amends
[ADR-0033](0033-auth-consolidation-policy.md), whose consolidation removed cap-induced seams but
left independently owned state, lifetimes, and reasons to change in `storage.py`.

## Context

At `87227de1` on 2026-08-08, `_auth/storage.py` is exactly 3,102 lines and owns lock registries,
atomic credential I/O, cookie CAS, raw document policy, account migration, promotion workers,
full replacements, master-token persistence, and compatibility templates. Its exact ceiling and
slack lock mean additions red CI. Seven raw writers exist (six profile intents plus arbitrary-path
`write_master_token`), transaction policies are 3 raise / 1 skip / 2 report, six physical shims
remain, and the corrected patch ledger records 280 sites (171 public, 109 private; storage 27/26).

The static graph has 26 direct modules / 13,745 lines and 68 scoped edges (54 module, 14
function-local). There is no module-only SCC; all scopes produce
`cookies, keepalive, master_token, psidts_recovery, storage`. A safe split must preserve opaque JSON,
per-intent corruption and lock behavior, monkeypatch timing, and v0.x identity while shrinking the
facade in every production stage.

## Decision

Extract by owned state/invariant, not headings. `A -> B` below means A may depend on B.

| Component | Owner / lifetime and state | Invariant | Dependencies |
|---|---|---|---|
| `ProfileStore` | Per raw caller path; separate canonical ordering key; no cached document | One aggregate commit boundary, per-intent locks, lossless round-trip; raw path controls I/O and locks | Down to document, typed I/O, locks, merge, derived token file; never migrator, scheduler, network, facade |
| `ProfileDocument` | Immutable decoded value | Preserves unknown root/namespace keys, origins, raw rows; decode chooses no corruption policy | Values only; no I/O, locks, lifecycle, orchestration |
| `credential_io.py` | Stateless leaf; two typed wrappers over one unchecked bypass | Profile and arbitrary token writes cannot be confused; no other bypass importer | Atomic I/O and values only |
| `StorageLockManager` | Shared process default or injected isolate; owns raw-path locks, registry, OS gateway, warning-once | Same raw lock path serializes stores; only cookie CAS blocks; secure-parent prep stays operation-specific | Lock primitives/values only |
| `CookiePersistence` | Per client; baselines by canonical path plus dispatch order | Baselines never cross profiles; outcome table alone advances order; closes with client | Store, snapshots, thread-dispatch seam |
| `LegacyAccountMigrator` | Stateless service over store and legacy-context collaborator | Owns two-file resolve/promote/scrub and two-read anti-race sequence | Store/account values and dedicated context I/O leaf |
| `LegacyPromotionScheduler` | Process-default canonical once-per-path registry, daemon workers, injected isolates | No 90-second write deadline in per-RPC reads; bounded 2-second-per-worker exit drain | Store, migrator, thread/exit primitives |
| `LoadedAuth` | Closed `InlineLoadedAuth | FileLoadedAuth` value | File result always carries exact auth/store/baseline; inline carries neither store nor baseline | Loader outputs and immutable values |
| `SessionSeedLoader` | Concrete per-attempt service | Initial `HEAL_THEN_NAME_ONLY`, recovery CAS, reread, post-heal baseline precede acquisition | Source, store, browser/recovery leaf, values |
| `StoredAuthLoader` | Concrete per-load application service | Source/store/baseline remain paired through seed, acquisition, merge, result | Seed loader, sole `TokenAcquirer` protocol, migrator, scheduler, source/store/values |
| `AccountRouteResolver` | Concrete source-aware resolver per attempt | File account re-resolves after replacements; inline parses once; acquisition records final route | Source, migrator, scheduler, store/account values; acquirer depends downward |
| `LoginProfileWriter` | One command operation | Failed login write does no legacy reconciliation; success reconciles outside storage lock | Store, migrator, request/result/account values |
| `AccountMetadataWriter` | One account operation | Failed write leaves sibling; success scrubs. Clear attempts in-band first, then scrubs the sibling after returned/no-op failures; naturally propagated exceptions abort before scrub | Store, migrator, account values |
| `MasterTokenFile` | Per explicit token path, even one named `storage_state.json`; direct construction is legacy-adapter-only | Models an arbitrary legacy path without a fake profile; its replace is deliberately unchecked and performs no read-before-replace. Arbitrary production callers must derive it from `ProfileStore` and cannot independently pair token/profile paths | Typed token I/O, locks, codec, token value |
| `MintService` | Per network attempt | Owns OAuth/MergeSession/RotateCookies only; never profile I/O | Network gateways and immutable requests/results |
| `MasterTokenBootstrapper` | Per bootstrap; mint service, one store, bootstrap lock, verifier | Recheck after acquisition; session commits before token; paths cannot be independently paired | Token persistence only through its store; never receives a token file |
| `ColdRecoveryCoordinator` | Per recovery flight | Explicit L2.5 -> L3 -> L4 order, flight/epoch/process rules, replacement baselines, cancellation settlement | Runner, headless, bootstrapper, existing single-flight, source/store |
| `storage.py` facade | v0.x compatibility module | Old signatures, identities, defaults, results, odd policies, and patch seams remain observable | Delegates downward only; no new state or algorithms |

Cross-cutting rules are normative:

- `ProfileDocument` is lossless; each operation owns its distinct corruption policy outside decode.
- Account directives are closed `KeepAccount | ClearAccount | SetAccount`; adapters retain existing
  `_AccountAction`, `KEEP_ACCOUNT`, and `CLEAR_ACCOUNT` identities. `BaselineState` is exactly
  `UninitializedBaseline | ReadyBaseline | FailedBaseline`; `PromotionResult` exactly
  `Promoted | AlreadyInBand | NoLegacyRecord | PromotionFailed`; `ResolvedAccount` exactly
  `InBandAccount | LegacyAccount | NoAccount`.
- Session establishment precedes token use. Loaded source and baseline stay paired. Background work
  has explicit close/drain ownership. Dependency-bottom modules have no upward or lazy facade rejoin.
- Every later `storage.py` change is net-shrinking and lowers its exact LOC pin in the same diff.
  Compatibility lasts through v0.x; removal waits for an announced v1 runway.

The first account seam is the unused dependency-bottom `profile_account.py` leaf. It defines
immutable `ProfileAccount`, closed keep/clear/set directives, `DomainSelection`, and
`StoredSession`; the session composes the canonical immutable `CookieJar`. Direct construction is
permissive so a later compatibility adapter can preserve odd legacy values. Validation belongs to
the named parsers:

| Parser input / view | Typed result |
|---|---|
| Non-mapping account, every view | `None` |
| Mapping, `ROUTE` | normalized account; invalid `authuser` becomes `0`, blank/invalid email becomes `None` |
| Mapping, `OWNER` | normalized account only when a non-blank string email is present |
| Mapping, `CARRY` | the same normalized account-record projection |
| Non-mapping domain namespace | empty `DomainSelection` |
| Mapping domain namespace | strings from a list become a defensive `frozenset`; optional is enabled only by exact `True` |

`CARRY` is not a lossless namespace carrier. Existing remint/capture operations may preserve the
entire raw `notebooklm` namespace, including unknown keys; those operations must not round-trip it
through `ProfileAccount`. No production caller consumes the new leaf in this stage.

The v0.x `AccountRecord`, `_AccountAction`, sentinels, `AccountArg`, writer signature/default, pickle
path, facade/shim identities, and permissive direct construction remain owned by `storage.py`.
Conversion is deferred to the future boundary that migrates a caller, with this exact table:

| Legacy runtime value | Future internal directive |
|---|---|
| exact `KEEP_ACCOUNT` singleton | `KeepAccount()` |
| `AccountRecord` instance | `SetAccount(ProfileAccount(value.authuser, value.email))`, without normalization |
| everything else, including `CLEAR_ACCOUNT` and out-of-contract values | `ClearAccount()` |

`ProfileDocument` is the next dependency-bottom seam. `decode()` accepts only an object at
the root, then captures a recursively immutable, insertion-ordered snapshot without validating
nested shapes. `to_json()` always returns a new deep mutable tree. The lossless representation keeps
unknown root and namespace members, arbitrary origins, every cookie-list slot and duplicate, opaque
rows, unknown row fields, and scalar distinctions such as integer `-1` versus float `-1.0`.

Raw and typed views have separate jobs. `raw_cookie_rows()` requires an actual raw list and returns
defensive copies of all slots; missing or non-list cookies produce a bounded, value-free structural
error. `cookies()`, account views, and domain-selection views are intentionally tolerant, lossy
projections through the canonical cookie/account parsers. A typed view is never serialized back
into the raw document, and typed `CARRY` never substitutes for lossless namespace carry.

Copy-on-write operations preserve the original document and all unrelated raw members:

- cookie-row replacement eagerly copies an iterable without filtering or normalizing it;
- namespace replacement installs an exact copy, preserves `{}`, or removes the member for `None`;
- indexed row patches validate the fully materialized patch set before applying it, reject boolean
  or non-integer, out-of-range, and duplicate indices with bounded value-free errors, and allow
  mapping/opaque replacements in either direction.

This value chooses no corruption or operation policy. Missing files, filesystem and Unicode errors,
JSON text/syntax failures, malformed nested shapes, warnings/logging, backups, lock outcomes,
filtering, and whether an invalid raw cookie list is fatal remain decisions of the reader or
operation boundary. The first production consumers are the pure cookie decision leaf and its
`storage.py` transaction adapter; no reader, lock, I/O helper, facade, or lifecycle owner may import
the document value directly.

The first production owner extraction is `storage_lock.py`. `StorageLockManager` now owns the
process-default exact-raw-path thread-lock registry, concrete POSIX/Windows gateway, synchronous
bounded retry dependencies, and thread-safe cookie-warning claim. Direct construction creates an
isolated lifecycle. `storage.py` retains secure-parent and per-intent outcome policy plus its v0.x
`_file_lock` / `_file_lock_exclusive` seams; `keepalive.py` retains a separate local `_file_lock`
wrapper, and both route through the same process default. Full-writer white-box tests now replace
`storage._STORAGE_LOCKS`; cookie seam tests may still patch `storage._file_lock`. The old static
warning bool and `_acquire_storage_lock` helper are removed, and the exact storage pin falls from
3,102 to 2,829 lines in the same diff.

Cookie merge policy is the first algorithm extracted from the facade. `cookie_merge.py` owns the
pure snapshot/CAS decision and the permanent no-baseline overlay over `ProfileDocument`,
`CookieJar`, and `RecoveryObservation` values. It names dirty-tuple comparison (excluding
SameSite), value-only CAS, and exact-path dotted-domain equivalence independently; neither
`Cookie.__eq__` nor serialization chooses those policies. Decisions are immutable, redacted, and
carry a complete replacement document only when rows changed, plus the next baseline and rejected
identities. Unknown raw members survive ordinary changes; recovery replacement intentionally emits
one canonical winning row and drops that winner's unknown keys, preserving the established
contract.

`storage.py` remains the sole transaction and compatibility owner in this stage. It reads and
classifies corruption under the existing blocking cookie lock, converts the legacy NamedTuple
snapshot/recovery inputs to immutable values, invokes the pure decision, reproduces the existing
value-free CAS logs, performs the single sanctioned raw write, and projects the old bool or
`CookieSaveResult`. The old tuple types, private helper signatures, same-module late binding, lock
semantics, writer authority, and caller identities do not move. This extraction lowers the exact
facade line pin again without changing bytes or baseline advancement behavior.

The compatibility inventory is explicit:

- Profile writers: `merge_cookie_delta`, `update_account_metadata`, `clear_in_band_account`,
  `replace_from_remint`, `replace_from_login`, `persist_minted_jar`; arbitrary-path writer:
  `write_master_token`.
- Cookie adapters: `save_cookies_to_storage`, `snapshot_cookie_jar`,
  `advance_cookie_snapshot_after_save`, `CookiePersistence.__init__`,
  `CookiePersistence.capture_open_snapshot`, `CookiePersistence.save`.
- Client/token seams: `NotebookLMClient.__init__(cookie_saver=...)`, `AuthTokens.__init__`,
  `AuthTokens.from_storage`.
- Ladder facade: `load_auth_from_storage`, `fetch_tokens`, `fetch_tokens_passive`,
  `fetch_tokens_with_domains`, `validate_with_recovery`, `recover_psidts_in_memory`.
- Account facade: `assert_account_writable`, `read_account_metadata`, `write_account_metadata`,
  `clear_account_metadata`, `read_account_metadata_from_storage_state`,
  `get_authuser_for_storage`, `get_account_email_for_storage`, `drop_legacy_account_key`,
  `repair_account_metadata_from_playwright_storage`, `resolve_account_identity`.
- Mint/token facade: `exchange_master_token`, `generate_android_id`, `mint_cookies`,
  `persist_minted_jar`, `read_master_token`, `write_master_token`; coarse operations:
  `master_token_bootstrap`, `master_token_remint`,
  `bootstrap_missing_storage_from_master_token`.

The frozen read policy is per intent: cookie merge hard-fails non-raising on missing/read/format
input; account update creates when absent but otherwise propagates I/O/decode and wraps JSON/root
shape; account clear is best effort for absent/OSError/JSON/non-object but propagates Unicode;
remint replaces absent/OSError/JSON/non-object without namespace but propagates Unicode; login never
parses the destination and backs up exact bytes before writing; minted-session creation obeys its
under-lock owner gate, with corrupt/unknown existing owner refused by default; master-token read
returns `None` only when absent, wraps OSError/JSON with causes, propagates Unicode, and rejects
malformed records. Executable tests pin returns, exceptions/messages, logs, backup bytes, post bytes,
and write counts.

## Consequences

`ProfileStore` becomes the real aggregate and `storage.py` a shrinking facade without creating an
`_auth/storage/` package. Raw and canonical paths have visibly separate jobs. Cookie merge stays a
pure decision plus one commit; lifecycle state gains owners; secret-bearing baselines do not cross a
host extension protocol. Only `TokenAcquirer` is structural: ports appear with consumers, not ahead
of them.

The cost is a long compatibility migration. Old tuples, enum constants, transaction shims, direct
token paths, saver injection, loader fallback, verifier bridge, and intentionally odd corruption
behavior remain until their runway ends. Every stage carries exact writer, signature, patch,
module-size, transaction, and import-graph evidence, making temporary duplication visible.

## Alternatives considered

**Mechanical file split or a class inside `storage.py`.** Rejected: both retain shared globals and
upward imports; the latter also cannot grow under the exact pin.

**`_auth/storage/` package or generic repository/unit-of-work.** Rejected: one aggregate does not
justify another facade or abstraction family.

**Universal mutation/corruption policy.** Rejected: the frozen intents deliberately disagree.

**Immutable transport jar or equality-driven cookie merge.** Rejected: httpx mutates its jar;
SameSite is serialized but excluded from equality, and merge predicates are independently named.

**Delete process state, promotion scheduling, or its exit drain.** Rejected: shared lock identity,
recovery flights, non-blocking per-RPC reads, and short-process durability are real lifecycles.

**Alias old tuple/directive values or fake a profile for arbitrary token paths.** Rejected: runtime
identity and explicit-path behavior are observable v0.x contracts.
