# Acceptance-Criteria Generator — Production Specification v2

## 0. Purpose

### Input

One authoritative build-specification bundle for a bounded autonomous coding session.

The bundle may contain:

- product and technical specifications;
- canonical decisions;
- architecture invariants;
- threat model;
- operational service-level objectives;
- platform definition of done;
- compatibility and deployment requirements;
- known escaped defects that must never recur.

### Output

A versioned, machine-readable **Acceptance Bundle** containing:

1. qualified atomic requirements;
2. derived obligations;
3. acceptance criteria;
4. executable-test and evaluation obligations;
5. estate and environment definitions;
6. fault models;
7. statistical protocols;
8. traceability links;
9. immutable verification configuration;
10. the exact stop-decision policy.

The Acceptance Bundle is the external verifier's release contract. It is not merely a list of tests and is not editable by the builder.

### Objective

Produce the strongest practical, automatable evidence that the implementation satisfies the supplied authorities under the defined environments and fault models.

This system does **not** claim to prove that the specification is complete, that the implementation has no defects, or that untested failures cannot exist.

---

## 1. Governing principles

### 1.1 Acceptance is independent from implementation

The agent that builds the implementation SHALL NOT be the authority that decides whether the build is complete.

At minimum:

- acceptance criteria are generated before implementation begins;
- the Acceptance Bundle is sealed and hashed;
- the builder has read-only access to acceptance artifacts;
- the builder cannot modify tests, thresholds, estates, goldens, verifier code, or evidence;
- an external verifier executes the acceptance package and makes the stop decision;
- changing acceptance artifacts creates a new bundle version and invalidates prior evidence.

A fresh model context is useful for reducing local self-consistency bias, but it is not sufficient independence by itself. Independence comes from separate authority, immutable artifacts, and separate execution permissions.

### 1.2 Criteria describe behavior; tests provide evidence

An acceptance criterion is one externally observable property with one final decision rule.

A criterion is **not** required to correspond one-to-one with a test. One criterion may require several unit, property, integration, security, real-estate, and performance tests. One test may provide evidence for several criteria.

The trace graph SHALL be:

```text
Authority
  -> Requirement or derived obligation
  -> Acceptance criterion
  -> Executable test or evaluation
  -> Execution result
  -> Evidence artifact
```

### 1.3 Every criterion requires authority

Every criterion SHALL trace to at least one approved authority:

- `specification`;
- `canonical_decision`;
- `architecture_invariant`;
- `threat_model`;
- `platform_definition_of_done`;
- `operational_slo`;
- `compatibility_policy`;
- `regulatory_policy`;
- `escaped_defect`.

A criterion without authority is unapproved scope expansion and SHALL block emission.

A legitimate safety, reliability, or operational need omitted by the build spec SHALL be represented as a **derived obligation** and SHALL trigger either:

1. a formal addition to the authoritative bundle; or
2. a `SPEC_BLOCKED` result requiring specification repair.

The generator SHALL NOT silently suppress the obligation or attach it to an unrelated requirement.

### 1.4 Deterministic oracles are preferred

The generator SHALL use the strongest available oracle in this order:

1. exact deterministic comparison;
2. invariant or property;
3. schema or protocol validation;
4. model-based state transition check;
5. differential or metamorphic comparison;
6. calibrated statistical or learned evaluator;
7. human review, only when no executable oracle is credible.

LLM judges SHALL NOT be the final arbiter for security boundaries, permissions, schema validity, exact citations, financial values, destructive actions, or other properties that can be checked deterministically.

### 1.5 Risk determines verification depth

Verification dimensions are selected because they apply to the system model and risk—not because every requirement receives the same mechanically generated siblings.

The generator SHALL spend the greatest verification budget on:

- security and tenant isolation;
- data corruption or irreversible writes;
- core externally visible behavior;
- difficult recovery paths;
- high-concurrency state;
- high-cost or high-latency paths;
- known historically fragile behavior.

### 1.6 Evidence is exact and reproducible

Every result SHALL be tied to:

- acceptance-bundle hash;
- implementation commit hash;
- verifier commit hash;
- fixture and golden hashes;
- dependency lock or snapshot;
- container or machine-image digest;
- runtime and compiler versions;
- operating system and architecture;
- resource limits;
- environment configuration;
- test or evaluation ID;
- random seeds where applicable;
- execution timestamp.

Evidence from a different bundle, build, verifier, or environment SHALL NOT satisfy the current gate.

---

## 2. Assurance statement and limits

### 2.1 What a green bundle means

A green Acceptance Bundle means:

- every identified authority has been atomized and dispositioned;
- every mandatory requirement and derived obligation has sufficient linked acceptance evidence;
- all blocking criteria passed under every applicable environment;
- the mandatory fault model was detected by the verifier;
- probabilistic and performance claims met their pinned protocols;
- structural and mutation signals contain no unexplained critical gaps;
- the evidence exactly matches the sealed acceptance and implementation versions.

### 2.2 What it does not mean

A green bundle does not prove:

- that the source specification contains every real-world requirement;
- that no unmodeled state or environment can fail;
- that mutation operators represent every possible implementation defect;
- that sampled performance proves behavior under all workloads;
- that a real-repository portfolio represents every repository;
- that a calibrated learned evaluator is infallible;
- that the software contains no defects.

The generator SHALL emit these limitations in every Acceptance Bundle.

---

## 3. Roles and trust boundaries

### 3.1 Roles

#### Authority owner

Supplies and versions the authoritative specification bundle.

#### Criteria generator

Extracts requirements, builds the system and risk models, proposes criteria, and emits the candidate Acceptance Bundle.

#### Adversarial reviewer

Runs in separate context and authority from the criteria generator. It searches for:

- omitted requirements;
- weak or circular oracles;
- missing failure modes;
- overbroad or impossible criteria;
- invalid thresholds;
- estate gaps;
- hidden implementation assumptions;
- opportunities for the builder to game the verifier.

#### Builder

May inspect public criteria and verifier failure reports. It SHALL NOT modify the sealed acceptance package or its evidence.

#### External verifier

Executes the sealed package, classifies failures, and emits the only authoritative stop decision.

### 3.2 Protected artifacts

The following SHALL be protected from builder writes:

- requirements and obligations;
- criteria definitions;
- test and evaluation code;
- fixture repositories;
- hidden cases;
- expected outputs and goldens;
- fault-injection implementations;
- threshold configuration;
- estate definitions;
- verifier scripts;
- calibration sets;
- evidence manifests.

---

## 4. Phase A — Qualify the authoritative specification

The generator SHALL NOT create acceptance criteria until the specification passes the qualification gate.

### 4.1 Parse authoritative clauses

Walk every authoritative document from top to bottom.

Extract each normative or behaviorally consequential statement, including:

- `shall`, `must`, `will`, `always`, `never`;
- exact response shape;
- error and fallback behavior;
- security and privacy constraints;
- latency, throughput, cost, and capacity limits;
- consistency and freshness requirements;
- supported and unsupported environments;
- compatibility and migration commitments;
- operational and observability requirements;
- examples that are explicitly normative.

### 4.2 Create atomic requirement records

Each requirement SHALL contain one independently decidable obligation.

```yaml
requirement_id: R-<document>-<section>-<sequence>
authority_type: specification
authority_document: DOC-01
source_location: "§3.7 paragraph 2"
source_quote: "<verbatim authoritative text>"
normalized_statement: >
  WHEN <trigger or precondition>
  THE SYSTEM SHALL <one observable obligation>.
requirement_type: functional
criticality: P1
testability: executable
status: qualified
```

Allowed `requirement_type` values:

- `functional`;
- `contract`;
- `security`;
- `privacy`;
- `data_integrity`;
- `state_consistency`;
- `resilience`;
- `compatibility`;
- `deployment`;
- `observability`;
- `performance`;
- `cost`;
- `operability`;
- `documentation`.

### 4.3 Detect non-atomic clauses

A clause containing multiple independently failing outcomes SHALL be split.

Example:

```text
"The system indexes the repository, emits progress, and completes within 60 seconds."
```

becomes separate requirements for:

1. index correctness;
2. progress reporting;
3. completion time.

### 4.4 Detect ambiguities

The generator SHALL record ambiguities such as:

- undefined terms;
- unclear units;
- missing time windows;
- unspecified consistency model;
- undefined ordering;
- absent error behavior;
- unclear supported languages or platforms;
- subjective terms without an oracle;
- contradictions between documents;
- examples that conflict with normative prose;
- requirements that depend on unspecified external behavior.

```yaml
ambiguity_id: A-014
authority_refs: [R-DOC01-3.7-02]
severity: material
problem: "'Fresh' has no maximum staleness bound."
required_resolution: "Define the maximum allowed index staleness."
```

### 4.5 Qualification outcomes

The qualification gate SHALL return exactly one of:

- `SPEC_QUALIFIED`;
- `SPEC_BLOCKED_AMBIGUOUS`;
- `SPEC_BLOCKED_CONTRADICTORY`;
- `SPEC_BLOCKED_UNTESTABLE`;
- `SPEC_BLOCKED_MISSING_AUTHORITY`.

A material ambiguity, contradiction, or unverifiable requirement SHALL block the build.

The generator SHALL NOT guess a material product decision merely to keep the autonomous loop moving.

A minor assumption may be accepted only when:

- the safe behavior is unambiguous;
- the assumption is explicitly recorded;
- the assumption does not weaken an authority;
- the assumption receives its own acceptance criterion.

---

## 5. Phase B — Build the system model and derived obligations

### 5.1 Build a minimal system model

The generator SHALL identify:

- public inputs and outputs;
- persistent stores;
- mutable state;
- state transitions;
- asynchronous jobs;
- queues and event streams;
- caches;
- external dependencies;
- trust boundaries;
- authentication and authorization boundaries;
- tenant boundaries;
- secret-bearing paths;
- irreversible or consequential actions;
- cancellation points;
- recovery and retry behavior;
- supported deployment environments;
- resource and scale dimensions.

The model exists to identify which verification dimensions apply. It SHALL not prescribe implementation details absent from the authorities.

### 5.2 Derive obligations

Derived obligations are requirements imposed by approved non-spec authorities or by unavoidable consequences of the declared architecture.

Examples:

- a tenant-scoped cache requires tenant isolation;
- a persistent schema change requires migration and rollback behavior;
- retries around a write require idempotency;
- an external request requires timeout and cancellation behavior;
- processing source code requires secret-safe logging;
- an asynchronous worker requires duplicate and out-of-order event behavior.

```yaml
obligation_id: D-SEC-009
authority_type: threat_model
authority_refs: [TM-TENANT-01, R-DOC01-4.2-03]
derivation: >
  The indexed artifact cache stores tenant-derived data and is shared by the
  service process; cache keys therefore require tenant separation.
normalized_statement: >
  WHEN two tenants contain identical repository paths
  THE SYSTEM SHALL never return one tenant's cached artifact to the other.
criticality: P0
status: qualified
```

### 5.3 Derived-obligation gate

Every derived obligation SHALL be:

- accepted into the authority bundle;
- rejected with a documented rationale; or
- escalated as a specification blocker.

There SHALL be no silent derived obligations.

---

## 6. Phase C — Assign criticality and blocking policy

### 6.1 Criticality

Each requirement, derived obligation, and criterion receives one criticality:

#### P0 — Catastrophic

Failure may cause:

- security-boundary violation;
- cross-tenant disclosure;
- secret exposure;
- irreversible destructive action;
- silent data corruption;
- material compliance violation;
- unauthorized external action.

#### P1 — Core

Failure breaks the product's principal user-visible behavior or makes results materially untrustworthy.

#### P2 — Important

Failure affects resilience, compatibility, maintainability, important edge behavior, or operational usefulness without violating a P0/P1 property.

#### P3 — Optimization

Failure affects polish or optimization and is not required for the selected release profile.

### 6.2 Blocking policy

- P0 criteria SHALL always block.
- P1 criteria SHALL always block.
- P2 criteria block by default unless the authority explicitly marks them deferred.
- P3 criteria block only when included in the selected release profile.

Every criterion SHALL also carry an explicit `blocking: true|false`. Criticality alone SHALL NOT silently change the stop condition.

### 6.3 No threshold weakening by the builder

The builder SHALL NOT:

- lower thresholds;
- remove estates;
- relabel criticality;
- mark criteria non-blocking;
- update goldens to match its output;
- increase permitted error rates;
- disable fault injections;
- exclude failing tests.

Any such change requires a new sealed Acceptance Bundle and invalidates prior evidence.

---

## 7. Phase D — Generate verification obligations by applicability

The generator SHALL evaluate each requirement against the following triggers.

A dimension is included when its trigger applies. A skipped applicable dimension is a generation defect. A non-applicable dimension does not require a criterion.

| Trigger in the system model | Required verification dimensions |
|---|---|
| Externally visible behavior | happy path, input partitions, boundary values, negative result |
| Declared API or serialized output | schema, required fields, version compatibility, malformed input |
| Shared mutable state | concurrency, ordering, atomicity, lost update, duplicate work |
| Consequential or retried write | idempotency, partial failure, rollback or reconciliation |
| Persistent data | durability, restart recovery, migration, downgrade or rollback policy |
| Cache or materialized index | freshness, invalidation, tenant scoping, stale-read labeling |
| External dependency | timeout, cancellation, retry, circuit behavior, degraded result, recovery |
| Untrusted input | injection, parsing limits, path traversal, resource abuse, log safety |
| Tenant or permission boundary | isolation, authorization, confused-deputy behavior |
| Secret-bearing data | redaction, storage, transit, logs, error messages, deletion |
| Event or queue processing | duplicate, missing, late, out-of-order, poison event |
| Unsupported input or platform | explicit rejection or labeled partial support |
| Search, inference, or incomplete knowledge | bounded claims, uncertainty, citation correctness, not-found honesty |
| Performance or cost promise | pinned workload, environment, percentile, sample protocol |
| Long-running process | resource stability, cancellation, cleanup, restart |
| Deployment surface | clean install, configuration validation, upgrade, rollback, health checks |
| Operational dependency | metrics, structured errors, audit trail, alertable failure state |
| Known escaped defect | exact regression and adjacent fault family |

### 7.1 Mandatory input partitions

For each externally visible input, identify as applicable:

- normal;
- empty;
- minimum;
- maximum;
- just below and above boundaries;
- malformed;
- duplicate;
- stale;
- extremely large;
- binary or unexpected encoding;
- unsupported type;
- unauthorized;
- cross-tenant;
- interrupted or cancelled.

### 7.2 Mandatory state analysis

For stateful features, identify:

- initial state;
- valid transitions;
- invalid transitions;
- repeated transitions;
- interrupted transitions;
- restart during transition;
- retry after uncertain completion;
- concurrent transitions;
- recovery from corrupted or partial state.

### 7.3 Negative-result honesty

Whenever the system cannot establish a complete answer, the criterion SHALL require the result to distinguish among:

- confirmed absence;
- not found within searched scope;
- unsupported;
- unresolved;
- stale;
- partial;
- dependency unavailable;
- permission denied.

The system SHALL not convert incomplete evidence into an absolute claim.

---

## 8. Phase E — Author acceptance criteria

### 8.1 Criterion granularity

Each criterion SHALL contain:

- one observable product property;
- one primary oracle;
- one final pass/fail decision;
- clearly bounded applicability;
- one or more authority references.

A criterion may require many executable tests and evidence artifacts.

Avoid criteria that:

- prescribe an implementation rather than behavior;
- combine unrelated outcomes;
- contain subjective adjectives without a rubric;
- depend on the builder's own self-report;
- use a learned evaluator when a deterministic oracle exists;
- have no failure condition;
- can pass without executing meaningful behavior.

### 8.2 Canonical machine-readable format

```yaml
criterion_id: AC-QUERY-017
name: Complete cited polyglot callers
authority_refs:
  - R-DOC01-3.7-02
  - D-HONESTY-004
criticality: P1
blocking: true

behavior:
  given: >
    A pinned polyglot repository whose golden call graph contains resolvable
    callers in Python, Java, and TypeScript plus one unresolved dynamic caller.
  when: >
    The caller query is executed for the target symbol.
  then:
    - Every statically resolvable caller is returned.
    - Every returned caller contains a repository-relative file and line citation.
    - The unresolved dynamic caller is labeled unresolved rather than absent.

applicability:
  fixtures: [fixture-polyglot-callers]
  estates: [polyglot]
  excluded_estates: []
  rationale: "The behavior crosses supported language boundaries."

primary_oracle:
  type: deterministic_golden_graph
  artifact: goldens/polyglot-callers.json

supporting_evidence:
  - schema_validation
  - citation_file_line_validation
  - real_repository_execution

thresholds:
  resolvable_recall: 1.0
  false_positive_rate: 0.0
  invalid_citations: 0
  mislabeled_unresolved: 0

test_ids:
  - T-FIXTURE-118
  - T-CONTRACT-044
  - T-REALREPO-031

fault_model_refs:
  - F-OMIT-CROSS-LANGUAGE-EDGE
  - F-FABRICATE-DYNAMIC-ABSENCE

protocol_ref: PROTO-DETERMINISTIC-01
required_evidence:
  - junit:T-FIXTURE-118
  - schema:T-CONTRACT-044
  - eval:T-REALREPO-031
```

### 8.3 Human-readable rendering

```text
### AC-QUERY-017 · Complete cited polyglot callers
Authority: R-DOC01-3.7-02, D-HONESTY-004
Criticality: P1
Blocking: yes
Applies to: fixture-polyglot-callers, estate-polyglot

GIVEN a pinned polyglot repository with a known call graph
WHEN the caller query is executed
THEN every resolvable caller is returned with a valid file:line citation
AND unresolved dynamic callers are labeled unresolved rather than absent.

Primary oracle: deterministic golden call graph
Thresholds: recall 1.0; false positives 0; invalid citations 0
Required evidence: T-FIXTURE-118, T-CONTRACT-044, T-REALREPO-031
Mandatory faults detected: omitted cross-language edge; fabricated absence
```

---

## 9. Phase F — Design executable evidence

### 9.1 Evidence classes

Allowed evidence classes include:

- `[static]`;
- `[unit-example]`;
- `[unit-property]`;
- `[model-stateful]`;
- `[contract]`;
- `[integration]`;
- `[fault-injection]`;
- `[security-adversarial]`;
- `[real-estate]`;
- `[performance]`;
- `[reliability]`;
- `[resource-stability]`;
- `[deployment]`;
- `[learned-evaluator]`;
- `[manual-required]`.

`[manual-required]` SHALL block a fully autonomous completion claim unless the release profile explicitly permits human verification.

### 9.2 One primary oracle, multiple evidence sources

A criterion SHALL define exactly one primary decision rule.

It MAY require multiple supporting checks. For example:

- primary oracle: exact golden output;
- supporting evidence: schema validation, citation existence, latency bound.

The generator SHALL NOT collapse distinct necessary evidence into one vague grader label.

### 9.3 Pre-build test validity

Before implementation begins, each P0 and P1 criterion SHALL demonstrate that its verifier can reject applicable canonical bad behaviors.

Acceptable pre-build negative targets include:

- no-op implementation;
- constant-success implementation;
- empty output;
- malformed output;
- stale output;
- cross-tenant substitution;
- omitted edge or record;
- fabricated result;
- swallowed exception;
- timeout with no cancellation;
- duplicate write;
- partial commit;
- secret written to logs;
- permissive authorization stub.

A criterion is not required to pass a reference-correct implementation when no such implementation exists.

Instead, the verifier SHALL show:

1. the oracle is executable;
2. the criterion fails against the applicable negative baseline;
3. the criterion rejects its mandatory canonical faults;
4. the criterion does not depend on private implementation details.

### 9.4 Post-build adequacy

After implementation exists:

- run source mutation on changed and high-risk code where appropriate;
- run architectural fault injections;
- classify surviving mutants and faults;
- add tests for relevant survivors;
- remove or document equivalent and irrelevant mutants;
- never treat a raw mutation percentage as self-explanatory.

### 9.5 Fault-model records

```yaml
fault_id: F-CROSS-TENANT-CACHE-KEY
category: security
criticality: P0
mechanism: hand_authored_negative_build
description: "Remove tenant ID from the artifact-cache key."
must_be_detected_by:
  - AC-TENANT-004
detection_requirement: 1.0
```

Allowed mechanisms include:

- source mutation;
- hand-authored negative implementation;
- dependency timeout or crash;
- network partition;
- disk-full condition;
- process termination;
- clock skew;
- malformed event;
- duplicate delivery;
- permission denial;
- corrupted persistent state;
- resource exhaustion.

### 9.6 Mandatory fault policy

- Every P0 criterion SHALL detect at least one canonical fault from every material fault class it protects against.
- Every P1 criterion SHALL detect at least one representative behavioral fault.
- P2/P3 fault obligations are risk-based.
- A trivial mutant unrelated to the criterion's essential behavior SHALL not satisfy the fault obligation.

---

## 10. Phase G — Define fixtures, estates, and environments

### 10.1 Fixture portfolio

Use small committed fixtures for exact, fast verification.

Fixtures SHOULD isolate:

- minimal happy path;
- boundary cases;
- malformed cases;
- supported and unsupported inputs;
- security invariants;
- known concurrency schedules;
- recovery transitions;
- escaped-defect regressions.

### 10.2 Real-estate portfolio

Define a reusable portfolio using capability tags rather than forcing every criterion onto every repository.

Example tags:

- `small-clean`;
- `large-monorepo`;
- `polyglot`;
- `deep-history`;
- `many-tiny-files`;
- `large-generated-files`;
- `submodules`;
- `lfs`;
- `no-codeowners`;
- `unsupported-language`;
- `case-collision`;
- `symlinks`;
- `planted-secrets`;
- `post-clone-mutation`;
- `restricted-permissions`;
- `multi-tenant`;
- `offline-dependency`.

### 10.3 Criterion applicability

Each criterion SHALL explicitly list:

- required estates;
- excluded estates;
- applicability rationale.

A criterion SHALL not run on an irrelevant estate merely to satisfy a universal matrix rule.

The final release portfolio SHALL still include a small number of end-to-end journeys that cover representative combinations of major estate dimensions.

### 10.4 Pinning requirements

Every estate SHALL pin:

- repository commit and submodule commits;
- fixture or fork patch hash;
- dependency locks;
- build-container digest;
- compiler and runtime versions;
- OS and architecture;
- resource limits;
- network policy;
- golden-output hash;
- setup and cleanup scripts.

A repository commit alone is not a reproducible environment.

---

## 11. Phase H — Select a protocol for each criterion

There is no universal `pass^k`.

Each criterion SHALL use a protocol appropriate to the claim.

### 11.1 Deterministic protocol

Use when the same inputs in the same environment must produce the same answer.

Requirements:

- pinned seed when randomness exists;
- exact or invariant-based oracle;
- isolated setup and cleanup;
- repeat only as needed to detect environmental flakiness;
- any divergent result is a failure unless nondeterminism is explicitly allowed.

### 11.2 Property-based protocol

Specify:

- generator domain;
- excluded invalid domain, with rationale;
- number or time budget;
- shrinking behavior;
- deterministic replay seed;
- persisted failing corpus;
- invariant;
- maximum health-check suppression.

A property test that filters away most generated inputs SHALL be rejected or rewritten.

### 11.3 Stateful and concurrency protocol

Specify:

- model state;
- actions;
- preconditions;
- invariants;
- target interleavings;
- duplicate and out-of-order schedules;
- schedule or exploration budget;
- deterministic replay of failures.

Random repeated execution alone SHALL not be the only evidence for a known critical race.

### 11.4 Reliability protocol

Specify:

- trial definition;
- target failure-rate ceiling;
- sample size or sequential-test method;
- confidence level;
- handling of censored and infrastructure-failed trials;
- allowed product failures.

### 11.5 Performance protocol

Specify:

- pinned hardware or resource class;
- warm and cold behavior separately;
- workload distribution;
- input sizes;
- concurrency;
- sample count;
- ramp and steady-state periods;
- percentile and confidence reporting;
- timeout treatment;
- measurement boundaries;
- permitted background activity.

Observability tools may collect measurements, but the protocol—not the dashboard—defines pass or fail.

### 11.6 Resource-stability protocol

Specify:

- run duration or operation count;
- leak metric;
- peak and steady-state resource limits;
- cleanup verification;
- restart behavior;
- background-task termination.

### 11.7 Learned-evaluator protocol

A learned evaluator may gate only when no credible deterministic oracle exists.

It SHALL pin:

- model and provider;
- model version or dated identifier;
- prompt and rubric hash;
- decoding settings;
- input presentation and ordering;
- calibration dataset;
- untouched holdout dataset;
- minimum sample size;
- human-human agreement baseline;
- confusion matrix;
- per-class precision and recall;
- maximum P0/P1 false-negative rate;
- recalibration triggers.

Aggregate agreement alone is insufficient.

Any change to the model, prompt, rubric, labels, output format, or calibration data SHALL invalidate previous calibration evidence.

---

## 12. Phase I — Adversarial review and bundle sealing

### 12.1 Independent review questions

The adversarial reviewer SHALL attempt to find:

- an authority with no criterion;
- a criterion with weak authority;
- a requirement atomized too coarsely;
- a material ambiguity hidden as an assumption;
- a criterion that can pass without meaningful execution;
- a circular oracle produced by the implementation under test;
- a golden copied from current output;
- a missing failure or recovery path;
- missing isolation or secret handling;
- missing compatibility or migration behavior;
- invalid statistical thresholds;
- irrelevant estate expansion;
- omitted estate combinations;
- builder-writable verifier assets;
- learned-evaluator bias or calibration gaps;
- a way to satisfy the metric while violating user intent.

### 12.2 Review disposition

Every finding SHALL be:

- fixed;
- rejected with explicit evidence and rationale; or
- converted into a specification blocker.

The reviewer is not required to claim that no possible gap exists. It must report that there are no **unresolved identified gaps**.

### 12.3 Seal the Acceptance Bundle

After qualification and review:

1. assign a semantic bundle version;
2. hash every authority, criterion, test, fixture, golden, protocol, and verifier asset;
3. emit the immutable manifest;
4. mark the bundle `SEALED`;
5. deny builder writes.

No implementation evidence generated before sealing counts toward acceptance.

---

## 13. Phase J — Execute verification in rungs

### Rung 0 — Build integrity

Run on every relevant change:

- compile or parse;
- type checking;
- formatting if normative;
- static analysis;
- dependency and configuration validation;
- prohibited-file and secret checks;
- acceptance-bundle integrity check.

### Rung 1 — Fast behavioral verification

Run on every builder iteration:

- targeted unit examples;
- property tests within the fast budget;
- schema and contract tests;
- deterministic security invariants;
- escaped-defect regressions;
- tests selected by dependency and trace impact.

### Rung 2 — Component and fault verification

Run when a component or section is candidate-green:

- integration tests;
- stateful and concurrency suites;
- dependency-failure tests;
- restart and recovery tests;
- mandatory P0/P1 fault injections;
- changed-code mutation analysis.

### Rung 3 — Real-estate and performance verification

Run when the relevant product section is candidate-green:

- applicable real-estate cases;
- pinned benchmark protocols;
- reliability trials;
- resource-stability runs;
- learned-evaluator cases.

### Rung 4 — Final release portfolio

Run before declaring completion:

- all blocking criteria;
- all applicable estates;
- representative end-to-end journeys;
- full mandatory fault portfolio;
- cross-component interactions;
- install, migration, upgrade, rollback, and clean-start paths where applicable;
- complete evidence and trace gate.

### 13.1 Failure classification

Each failed execution SHALL be classified as:

- `PRODUCT_FAILURE`;
- `VERIFIER_FAILURE`;
- `ESTATE_FAILURE`;
- `INFRASTRUCTURE_FAILURE`;
- `NONDETERMINISTIC_FAILURE`;
- `CALIBRATION_FAILURE`;
- `BUNDLE_INTEGRITY_FAILURE`.

Only verified infrastructure failures may be retried without changing the implementation.

A retry budget SHALL be defined per protocol. Exhausting it blocks acceptance and SHALL NOT be converted into a pass.

---

## 14. Phase K — Traceability and evidence gate

### 14.1 Required graph

The verifier SHALL construct and validate:

```text
authority
  -> requirement / derived obligation
  -> criterion
  -> executable evidence obligation
  -> execution
  -> artifact
```

### 14.2 Deterministic gate failures

The gate SHALL exit non-zero if any of the following is true:

- an authoritative clause has no qualified requirement or explicit disposition;
- a mandatory requirement has no blocking criterion;
- a derived obligation has no approved authority or disposition;
- a criterion has no authority;
- a blocking criterion has no primary oracle;
- required test IDs do not exist;
- a required test did not execute;
- an execution is skipped, xfailed, quarantined, or filtered without approved disposition;
- a result belongs to a different acceptance or implementation hash;
- required evidence is missing or malformed;
- a criterion failed in an applicable environment;
- a P0/P1 mandatory fault was not detected;
- a critical mutation survivor lacks disposition;
- a critical structural-coverage gap lacks disposition;
- a learned evaluator lacks current calibration;
- the builder modified a protected artifact;
- the estate or environment does not match its pinned definition.

### 14.3 Preventing stubbed tests

JUnit or equivalent execution alone does not prove meaningful execution.

For critical tests, the verifier SHOULD additionally require one or more of:

- explicit criterion and test IDs in the result;
- executed assertion count;
- expected code-region or endpoint observation;
- fault-injection detection;
- branch or path evidence;
- output artifact validation;
- no-op baseline rejection.

---

## 15. Adequacy signals

No single metric is a proof of correctness.

### 15.1 Traceability

Traceability shows that identified authorities have planned and executed evidence.

It does not prove that:

- extraction was complete;
- the oracle is correct;
- all relevant input classes were tested;
- the source specification was complete.

### 15.2 Structural coverage

Structural coverage is a diagnostic signal.

Policy:

- measure changed and high-risk code;
- require strong branch or condition coverage where meaningful;
- require every uncovered critical branch to be dispositioned;
- remove dead code rather than creating meaningless tests;
- exclude generated or unreachable code only with explicit rationale;
- do not claim that 100% branch coverage proves semantic completeness.

### 15.3 Mutation and seeded faults

Mutation testing checks whether assertions detect selected code changes.

Policy:

- prioritize changed and high-risk modules;
- use language-appropriate mutation tools;
- use hand-authored architectural faults where source mutation is inadequate;
- classify survivors as `test_gap`, `requirement_gap`, `oracle_gap`, `equivalent`, or `irrelevant`;
- require no unexplained surviving critical faults;
- do not let a trivial killed mutant satisfy a criterion's essential fault model.

### 15.4 Real-estate evidence

Real-estate cases demonstrate behavior on selected real systems.

They do not establish universal correctness. The bundle SHALL state the limits of its estate portfolio.

---

## 16. Generator emission gate

The criteria generator may emit a sealed Acceptance Bundle only when:

- the specification qualification result is `SPEC_QUALIFIED`;
- every authority clause has a requirement or explicit disposition;
- every derived obligation is accepted, rejected, or blocking;
- every P0/P1 requirement has at least one blocking criterion;
- every criterion has approved authority;
- every criterion has one primary oracle;
- every blocking criterion has executable evidence obligations;
- P0/P1 criteria have valid negative baselines and mandatory fault models;
- estate applicability is explicit;
- every probabilistic claim has a statistical protocol;
- every learned evaluator has a calibration plan;
- the adversarial review has no unresolved identified gaps;
- protected artifacts can be sealed and isolated from the builder.

If these conditions are not met, the generator SHALL emit a structured blocked report instead of an acceptance set.

---

## 17. Final stop condition

The autonomous build loop stops with `ACCEPTED` only when all of the following are true:

1. The sealed Acceptance Bundle has no unresolved specification blocker.
2. The bundle's integrity hash is valid and no protected artifact was modified by the builder.
3. Every mandatory authority is traced through a requirement or derived obligation to at least one blocking acceptance criterion.
4. Every blocking criterion has sufficient executed evidence under every environment to which it applies.
5. Every P0 and P1 criterion passes with zero unapproved violations.
6. Every blocking P2/P3 criterion meets its declared threshold.
7. Every probabilistic, reliability, performance, and resource claim meets its pinned statistical protocol.
8. Every mandatory P0/P1 seeded fault is detected.
9. Every surviving critical mutation or injected fault is dispositioned, and no unresolved `test_gap`, `requirement_gap`, or `oracle_gap` remains.
10. Every critical structural-coverage gap is tested, proven unreachable, or otherwise explicitly dispositioned.
11. Every gating learned evaluator has current calibration and meets its per-class false-negative limits.
12. Every evidence artifact matches the exact acceptance bundle, implementation, verifier, dependency, fixture, and environment hashes.
13. The final end-to-end release portfolio is green.
14. No result is accepted solely because it was skipped, quarantined, retried until lucky, or manually relabeled.

Possible terminal decisions are:

- `ACCEPTED`;
- `REJECTED_PRODUCT_FAILURE`;
- `BLOCKED_SPECIFICATION`;
- `BLOCKED_VERIFIER`;
- `BLOCKED_ENVIRONMENT`;
- `BLOCKED_NONDETERMINISM`;
- `BLOCKED_CALIBRATION`;
- `BLOCKED_BUNDLE_INTEGRITY`.

The verifier SHALL never weaken, delete, or reinterpret a criterion to obtain `ACCEPTED`.

---

## 18. Acceptance Bundle directory layout

```text
acceptance/
  manifest.yaml
  assurance-limits.yaml
  authorities/
    authority-index.yaml
  requirements/
    requirements.yaml
    ambiguities.yaml
    dispositions.yaml
  obligations/
    derived-obligations.yaml
  models/
    system-model.yaml
    trust-boundaries.yaml
    state-models.yaml
    risk-register.yaml
  criteria/
    criteria.yaml
    criteria.md
  protocols/
    deterministic.yaml
    property.yaml
    concurrency.yaml
    reliability.yaml
    performance.yaml
    learned-evaluator.yaml
  estates/
    estates.yaml
    repositories.lock
    containers.lock
  faults/
    fault-model.yaml
    negative-builds/
  verifier/
    tests/
    evaluators/
    verify.sh
  calibration/
    rubrics/
    calibration-set/
    holdout-set/
  evidence/
    evidence-manifest.yaml
    executions/
    reports/
```

---

## 19. Minimum manifest

```yaml
bundle:
  id: ACCEPTANCE-DOC01
  version: 2.0.0
  status: SEALED
  created_at: "<UTC timestamp>"
  bundle_hash: "<sha256>"

release_profile:
  name: autonomous-build-complete
  blocking_criticalities: [P0, P1, P2]

integrity:
  authority_hash: "<sha256>"
  requirements_hash: "<sha256>"
  criteria_hash: "<sha256>"
  verifier_hash: "<sha256>"
  fixtures_hash: "<sha256>"
  goldens_hash: "<sha256>"
  protocols_hash: "<sha256>"
  estates_hash: "<sha256>"
  faults_hash: "<sha256>"

counts:
  authority_clauses: 0
  requirements: 0
  derived_obligations: 0
  blocking_criteria: 0
  tests_and_evaluations: 0
  mandatory_faults: 0

qualification:
  status: SPEC_QUALIFIED
  material_ambiguities: 0
  unresolved_contradictions: 0
  untestable_blocking_requirements: 0

review:
  unresolved_identified_gaps: 0
  reviewer_identity: "<agent/config hash>"
  review_artifact_hash: "<sha256>"
```

---

## 20. Generator instruction summary

The generator SHALL:

1. qualify the authority bundle before generating criteria;
2. block rather than guess material ambiguities;
3. atomize every normative obligation;
4. derive necessary security, resilience, data, and operational obligations from approved authorities;
5. model interfaces, state, dependencies, trust boundaries, and consequential actions;
6. select verification dimensions by applicability and risk;
7. author one observable property and one primary oracle per criterion;
8. map criteria to one or more executable evidence obligations;
9. validate P0/P1 verifiers against canonical bad behaviors before building;
10. use mutation and fault injection as adequacy evidence, not universal proof;
11. bind criteria only to applicable estates;
12. use criterion-specific statistical protocols instead of universal repetition;
13. independently review the bundle for weak or missing verification;
14. seal the complete package against builder modification;
15. let only the external verifier issue the stop decision;
16. report assurance limits honestly.

This is the acceptance system. Passing tests inside the builder's repository is not sufficient. Completion exists only when the independent verifier emits `ACCEPTED` for the exact sealed bundle and implementation hashes.
