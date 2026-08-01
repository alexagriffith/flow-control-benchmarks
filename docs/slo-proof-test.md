# SLO proof test

This file is the singular entrypoint for the SLO proof plan.

- Detailed test design: [`slo-proof-tests.md`](slo-proof-tests.md)
- Ordered execution plan: [`slo-proof-execution-plan.md`](slo-proof-execution-plan.md)

Use the strong claim only after the relevant test passes:

> Under `<load>`, on `<hardware/model/scope>`, flow control kept premium within
> `<SLO>` while preserving `<success-rate>` success.

Until then, use the narrower claim:

> Flow control provides priority admission under saturation: higher-priority work
> is served ahead of lower-priority work, and deferrable work can be queued
> instead of rejected.
