# Layer 5 — Promptfoo red team (BUILT, NOT run by default)

**Question it answers:** could a meeting participant manipulate what Proxy says
*out loud* by injecting instructions into the transcript — override its rules, make
it parrot a false claim as its own, extract its system prompt, reach another
tenant's code, or trick it into "taking" a world-touching action?

## Why it's gated (not run for doc00–doc02 today)

Layer 5 is **flag-gated behind `--redteam`** on `run_full_verification.sh` and does
**not** run by default. The threat model — hostile *live meeting input from outside
participants* — only becomes real once a doc actually consumes live transcript/chat.
For doc00 (foundation), doc01 (code intel) and doc02 (transport, not yet wired to
live input) there is no live-input surface to attack yet, so a default run would only
exercise a stand-in. It becomes first-class for **doc03+** (meeting understanding /
live speak), where `customer_facing=True` in the doc registry.

## How to run it (when a customer-facing doc is live)

```bash
./verification/run_full_verification.sh doc03 --redteam
# or directly:
npx promptfoo@latest redteam run -c verification/redteam/promptfooconfig.yaml
```

Requirements:
- **A funded `ANTHROPIC_API_KEY`.** The run makes real model calls. (In the session
  that built this, the key's Anthropic credit was exhausted — same block as Layers
  2 & 3 — so no default run was possible regardless.)
- **The real target wired in.** The config's provider is a Claude stand-in carrying
  Proxy's speak-guardrail prompt. To test the *product*, swap the provider for the
  live Proxy speak seam (an `exec`/`http` provider driving
  `transport.speak.SpeakOrchestrator` / the turn controller).

## What's covered

- Hand-authored Proxy-specific injections: direct override, impersonation
  ("say this as your own"), cross-tenant exfiltration, system-prompt extraction,
  fake-authority bypass of human-in-the-loop.
- Promptfoo auto-generated adversarial variants via `redteam.plugins`
  (privacy, PII, prompt-extraction, RBAC, excessive-agency) and `strategies`
  (prompt-injection, jailbreak).

Assertions map directly to the standing laws: *grounded or silent*, *human control
is absolute*, *tenant isolation*.
