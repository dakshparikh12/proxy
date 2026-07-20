uires passthrough vs alternative accuracy comparison; only checks emission |
| 4 | AC-SPEAK-16 | P0 | **no test** | first-grounded-text p50<=2s / p95<=4s — no test exists |
| 5 | AC-SPEAK-17 | P0 | **no test** | shallow-SLO population definition — no test exists |
| 6 | AC-SPEAK-18 | P1 | **no test** | un-spoken detail routed to chat — no test exists |
| 7 | AC-SPEAK-19 | P0 | **no test** | audible ack boundary-gated — no test exists |
| 8 | AC-SPEAK-20 | P1 | **no test** | <=500ms reflex degrades to tile ACK — no test exists |
| 9 | AC-CHAT-01 | P1 | toothless | `assert True` — literal no-op |
| 10 | AC-CANVAS-01 | P1 | toothless | `assert canvas is not canvas2` — trivial identity, no uniqueness enforcement |
| 11 | AC-CANVAS-05 | P1 | toothless | Tests trivial `self._state = state` — no resolve-gating |
| 12 | AC-CANVAS-08 | P1 | toothless | No test that self-initiation is blocked |
| 13 | AC-CANVAS-13 | P1 | toothless | Source-grep for "token" — matches docstring, not behavior |
| 14 | AC-FAIL-07 | P1 | toothless | `or True` makes invalid-status assertion vacuous |
| 15 | AC-FAIL-08 | P0 | toothless | Tests inline dict stub, not real WebhookProcessor |
| 16 | AC-FAIL-10 | P0 | toothless | Source-grep for "pending" — no behavioral test |
| 17 | AC-TURN-15 | P1 | placeholder | `assert callable(ctrl.barge_in)` — requires real-session evidence |
| 18 | AC-XCUT-07 | P0 | toothless | Wrong criterion in docstring + `"return" in src.lower()` trivially true |

**Breakdown: 6 P0 refuted, 12 P1 refuted. 5 have no test at all; 10 have toothless tests; 3 are placeholders for eval-realrepo criteria.**

---

### Invariant Check Results

| invariant | status | evidence |
|---|---|---|
| Signal surface = 9 signals | PASS | signals.py EMITTED_SIGNAL_NAMES == expected 9-set |
| Cost model $0.75-$0.85/hr | PASS | cost.py rate_card() returns $0.80/hr |
| 4 seam protocols @runtime_checkable | PASS | seams.py: TransportProvider, STTProvider, TTSProvider, OutputMediaSink |
| Registry closure | PASS | assert_registry_closed() returns None |
| Naming law (no internal names) | PASS | All user-visible strings in delivery.py::user_visible_strings() scanned |
| No forbidden deps | PASS | No pipecat/livekit/bus/broker imports |
| No per-platform branches | PASS | No sys.platform/os.name conditionals |
| No libs/transport directory | PASS | Package correctly at services/transport |

---

**VERDICT: NOT DONE**

18 blocking criteria REFUTED (6 P0, 12 P1). Critical gaps:
- **5 blocking criteria (AC-SPEAK-16 through AC-SPEAK-20) have NO test at all** — including 3 P0 criteria for latency SLOs, boundary-gated ack, and SLO population definition.
- **6 P0 blocking criteria have toothless tests**: AC-JOIN-11 (constructor-only), AC-FAIL-08 (inline stub), AC-FAIL-10 (keyword grep), AC-XCUT-07 (wrong criterion + trivially true assertion), AC-SPEAK-16/17/19 (no test).
- **Multiple tests use `assert True`, `or True`, or source-code keyword matching** instead of exercising real behavior.
