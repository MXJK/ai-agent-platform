# Source and adaptation

Reference: the user-supplied local `mewcode-python/mewcode/hooks/` implementation.
Its source headers attribute it to 公众号@小林coding (xiaolincoding.com,
xiaolinnote.com, jianli.xiaolinnote.com). `conditions.py` is reused with import
and validation changes; the model names, condition syntax, matching order,
once/reject and prompt semantics are adapted from that implementation.

The platform loader, synchronous executors and durable engine are adapted for
Cogent Run storage, recovery and process-owned configuration. The supplied source
snapshot contains no LICENSE file; this provenance record does not grant a license
or claim original authorship of the reused implementation.
