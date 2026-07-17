# Doc 00 goldens — mechanically derived answer keys

Doc 00 is INFRASTRUCTURE: the overwhelming majority of its criteria are settled by
cost-free structural/contract oracles whose "answer key" is the spec constant itself
(exact Pydantic field sets, Literal member sets, canonical column sets, required
config tokens). Those are asserted directly in the test bodies against the
`source_quote` / CANONICAL values — there is no room to hand-invent a wrong answer.

The one place a *correctness* answer could be hand-invented is the pre-build
validation spike (AC-BLD-001, §16): `get_dependents` / `who_writes` blast-radius on a
real code graph. That answer key is derived MECHANICALLY here, with a toolchain
deliberately DIFFERENT from the product's (stdlib `ast`, not tree-sitter/SCIP/LSP) so
there is no shared-bug blindness (maker ≠ checker applied to the eval data).

## `spike_blast_radius.json`

Derived by `tools/derive_goldens.py` (stdlib-ast reverse-import graph) over the
committed hermetic fixture package `spike_fixture/src/orm_app/`:

    orm_app.models   <- (imported by) orm_app.repo        # direct dependent (writes the users table)
    orm_app.repo     <- orm_app.service                    # transitive
    orm_app.service  <- orm_app.api                        # transitive
    orm_app.unrelated                                       # NOT in the blast radius (control)

Regenerate deterministically:

    python tools/derive_goldens.py staging/doc00/goldens/spike_fixture src orm_app.models

Golden semantics: "If `orm_app.models` changes, which modules import it (directly =
depth 1, transitively = closure)?" The spike test (`test_m14_bld.py::test_bld_001_*`)
grades the product's `get_dependents` answer against `direct_importers` by set-recall
and allows a transitive superset — exactly the grader the criterion names.

`orm_app.unrelated` is the negative control: a product that returns the whole repo
(no real graph) is rejected because `unrelated` must be absent from the direct set.
