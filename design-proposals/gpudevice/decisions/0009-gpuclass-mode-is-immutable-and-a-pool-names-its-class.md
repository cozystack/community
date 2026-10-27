# 0009. `GPUClass.mode` never changes, and a pool names its class

- **Number:** `0009`
- **Date:** `2026-10-23`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99#pullrequestreview-5616164700)
- **Implemented in:** not yet

## Context

Section 1 froze a class's `mode` only while its finalizer was present, that is while claims of the class were `Bound`, `Attached` or `Lost`. A worker pool selected claims by labels alone, and a selector's claims had to share a class anyway. The mode the pool's workers needed, passthrough or vGPU, came from a separate pool field, `kairos.gpuMode`, which the chart set as a `gpu.cozystack.io/mode` label on the worker VMI template, and the webhook refused a claim whose class mode differed from that label. The implementation accepted a pool reference on a VMI without the label, so the text and the code disagreed.

@kvaps asked in review of #99 and [#101](https://github.com/cozystack/community/pull/101#pullrequestreview-5616165668) for `mode` to be immutable always and for the selector to name the class, so that a pool does not state its mode at all.

## Decision

`GPUClass.mode` cannot change after the class is created, whether or not claims of it exist, and neither can the other fields that define the offer: `vendor`, `model`, `profile`, `memory` and the kind of `source`. A pool's class entry names its class, and in step 2 a `claimSelector` carries `className` next to its label selector and matches only claims of that class. A pool states no mode: the chart and admission read it from the named class. The mode label and the mode check at VMI admission are dropped.

## Why not the alternatives

- **Keep the pool's mode field and the label check.** Two fields say the same thing, one of them can be wrong, and the webhook has to refuse the mismatch at every worker start instead of the chart getting it right once. A VMI without the label was a third state nobody had defined.
- **Freeze `mode` only while claims exist, as section 1 had it.** A class without claims could switch mode, and a pool that names it would then render workers for the other mode, so the chart could not rely on the name. The hoster loses nothing by the stricter rule: a different mode is a new class with a new name, which a tenant has to order against anyway because budgets and prices are per class.

## Consequences

- A class name stands for one mode for the class's lifetime, so a pool's worker template follows from the class it names.
- A hoster who created a class in the wrong mode deletes it and creates another, once the class is not in use (section 9).
- A selector cannot span classes, which section 9 requires.
- A pool rendered against a class keeps that class's worker image until it renders again, so a hoster who deletes a class does not create another class of the same name in the other mode. Nothing in the platform enforces this; the hoster documentation states it.

## Revisit if

A class needs to serve both modes, for example if one resource name ever covers whole GPUs and vGPUs on the same node.
