"""Choosing which candidates are worth fitting a transform to.

Verification is the expensive half of a query -- 22 ms per candidate on the
deployment box, 100 candidates, 22% of the whole thing -- and almost all of it
is RANSAC (2.89 ms of a 3.27 ms verification at 45 buttons). Most of those
candidates are hopeless, retrieved on a handful of shared tokens and rejected
the moment anyone tries to fit a transform through them.

So verify the top `CFG.fuse.verify_top_m` by **tier-1 score** and skip the
rest. Retrieval depth and verification depth are different questions that were
the same number by accident: `index.top_n` controls how many records get a
look, this controls how many get a fit.

Nothing here is a decision about the answer. The geometric verifier still
decides; this only chooses what it looks at, which is why it is allowed to be
approximate -- and why anything it drops must be genuinely unlikely.

## A cheap geometric score was tried here first, and is worse than tier 1

The intuition was that tier 1 is a bag of tokens with no geometry in it, so a
cheap layout comparison -- button-count ratio, coarse colour histogram, and a
radial-distance profile of the keypad -- ought to rank candidates better for
microseconds. Measured over the sample's eight known query/answer pairs, as
the rank of the correct answer among everything tier 1 retrieved:

    tier-1 score      1, 1, 1, 1, 1, 1,  6, 16
    geometric score   1, 1, 2, 3, 5, 7, 13, 16
    both, averaged    1, 1, 1, 1, 1, 1,  6, 17

Tier 1 puts the answer first six times out of eight. The geometric score
demotes three of those (`Sony_RM-PJ20_big_0` from 1 to 13) and improves none,
and averaging the two is no better than tier 1 alone. Shipped at
`verify_top_m = 5` it cost recall@1 8/8 -> 6/8.

The reason is worth keeping, because it applies to any cheap proxy anyone
tries next: **the features that are cheap are the ones that break when
detection recall differs between the two sides**, and differing detection
recall is the normal case for a phone photograph against a catalogue shot. The
worst pair here is `MR-18B_0_0` vs `MR-18B_0_1`, where one side extracts 4
buttons and the other 22 -- a count ratio of 0.18 and a radial profile built
from four points. Handling exactly that is what RANSAC-with-coverage is *for*.

A landmark version -- power, volume, channel -- fails the same way and adds a
second problem: identifying which detection is the power button is unreliable
(many are grey, and the label comes from OCR, which may never be required for
a match), so a wrong identification silently mis-ranks.
"""
from __future__ import annotations

from app.config import CFG


def choose(tier1: dict[str, float], top_m: int,
           keep: set[str] | None = None) -> set[str]:
    """Record ids worth verifying: the best `top_m` by tier-1 score.

    `keep` is exempt and always included -- it carries the model-code hits,
    and that path is near-100% precise, so a candidate agreeing on the code has
    better evidence behind it than a token overlap and must not be ranked out
    by one. Note those arrive with a tier-1 score of 0.0 when tier 1 did not
    surface them at all, so without the exemption they would sort last.

    `top_m <= 0` disables the cut and verifies everything.
    """
    keep = {r for r in (keep or set()) if r in tier1}
    if top_m <= 0 or len(tier1) <= top_m:
        return set(tier1)

    # rid in the sort key so ties break identically on every run. An unstable
    # ordering here would move which candidates get scored, and therefore the
    # answer, for no traceable reason -- the same discipline as RANSAC's fixed
    # seed.
    ordered = sorted(tier1, key=lambda r: (-tier1[r], r))
    room = max(0, top_m - len(keep))
    return keep | set(ordered[:room])
