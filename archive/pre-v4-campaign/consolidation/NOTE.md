Three-phase consolidation run on the second endpoint, 2026-07-27. One uncounted
stabilization repeat plus three counted 300 s repeats, all present in summary.csv
as repeats 1 through 3. Premium A runs alone, Premium B joins at 100 s, and
Standard A joins at 200 s, so per-phase numbers come from the client samples
rather than from summary.csv, which aggregates the whole repeat.

This run uses the corrected runner, which stops latency timing at the OpenAI
stream terminator. No sample falls in the 12.4 to 12.5 s band that the earlier
runner produced.
