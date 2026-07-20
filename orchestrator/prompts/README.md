# orchestrator/prompts/ — the STABLE-FIRST convention (Task 6, prompt caching)

Anthropic prompt caching only activates on a **byte-identical prefix** across repeated calls. So every
prompt here is ordered **large STABLE content FIRST, small VARIABLE content LAST**:

- **STABLE (first, caches):** the role, the method/instructions, the seam-architecture context, the
  `mock_boundary` rules, references to AGENTS.md / GENERATOR.md, and — where a prompt reads them — the
  spec text and `dependency_manifest.yaml`. Identical every call within (and often across) a doc.
- **VARIABLE (last, never caches):** the specific `<DOC>`/`<SPEC>`, the specific criterion under
  judgment, the specific failure being debugged. Placed in a trailing `## THIS RUN` / `## CONTEXT`
  block so nothing above it changes between calls.

Why it matters most for the hot loops: the reality/negative **critics** run per-criterion × up to 4
rounds, and the extraction-count gate runs per-doc — those two (`reality_critic.md`,
`extraction_count.md`) keep the entire method above the single trailing criterion/spec line, so the
method caches across every criterion in a doc. Generation/build/plan prompts run ~once per doc, so
their caching win is smaller, but they follow the same shape so the convention is uniform and any
future doc (03–09) inherits it.

**Rule for new prompts:** never put `<DOC>`, a criterion body, or a failure trace above the method.
If you must reference the doc mid-method, keep the reference generic (“this doc’s bundle”) and bind
the concrete value only in the trailing block.
