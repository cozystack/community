<!-- Copy this file to design-proposals/<proposal>/decisions/NNNN-short-slug.md,
     taking the next free number for that proposal. Numbering is per proposal,
     so two proposals both having an 0001 is expected. -->
# NNNN. State the decision here, as a fact

- **Number:** `NNNN`
- **Date:** `YYYY-MM-DD`
- **Status:** Accepted | Superseded by `NNNN` | Reverted
- **Deciders:** `@your-github-handle, @codecider`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** `cozystack/community#NNNN` — the pull request or issue where it was argued
- **Implemented in:** `cozystack/cozystack#NNNN` — or `not yet`

<!-- Date is when the decision was made, not when it was written up.

     Deciders are the people who made the call — typically the author of the
     change and the maintainer who approved it. Someone whose objection forced
     the decision belongs in Context, credited by name, not in Deciders.

     Proposal is required. A decision with no proposal to amend is a proposal;
     write that instead.

     Decided in is required, and it is the field that makes this record
     checkable. Point it at the thread where the argument actually happened.

     Title the decision, not the topic: "storageClass is immutable after
     creation", not "storageClass immutability". -->

## Context

<!-- What was true when we decided. The forces in play: the state of the
     code, constraints we were under, what had already shipped.

     Write it in the past tense and leave it there — this is a snapshot,
     and it is not updated later when the world moves on. Give a reader
     who was not present enough to see why the decision was reasonable. -->

## Decision

<!-- One paragraph. Imperative and specific: "ComputePlane ships as …".
     State what was decided, not the discussion that produced it. -->

## Why not the alternatives

<!-- One short bullet per alternative: what it was, and the concrete reason
     it lost. This is the section that keeps the question from being
     reopened every six months, so spend your words here.

     Source each one — link the comment, review or pull request it came
     from. An alternative written from memory is where inaccuracy gets in,
     and a claim nobody can trace back cannot be checked for accuracy.

     Prefer the argument that survives a refactor. If an option lost
     because of how something is packaged today, and it would also lose on
     a structural fact about the API, give the structural reason.

     If an alternative lost on a judgement call rather than a hard fact,
     say so — it tells a future reader how firm this decision really is. -->

## Consequences

<!-- What this now costs or constrains, in both directions: what got
     easier, and what we have to live with. Any follow-up work it creates.

     Be honest about the downsides. A record that lists no costs reads as
     advocacy, and gets trusted accordingly. -->

## Revisit if

<!-- The trigger that would justify reopening this: an upstream feature
     landing, a scale threshold crossed, a constraint lifted. If there is
     no plausible trigger, say "no foreseeable trigger" — that is useful
     information too. -->

---

<!--
Format follows Michael Nygard's architecture decision records
(https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
and MADR (https://adr.github.io/madr/).

Once merged, the header block above is maintained (Status, Superseded by,
Implemented in must track reality) and the prose below it is frozen.
-->
