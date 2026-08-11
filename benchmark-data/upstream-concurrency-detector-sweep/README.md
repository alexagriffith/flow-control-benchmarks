# Upstream concurrency-detector sweep

This package measures how `maxConcurrency` changes latency under one fixed
traffic pattern. The sweep used GPT-OSS 20B, 512 input tokens, 128 output
tokens, prefix caching off, and two 120-second repeats at each cap.

| `maxConcurrency` | Premium p95 TTFT | Standard p95 TTFT |
|---:|---:|---:|
| 32 | 568 ms | 12,722 ms |
| 48 | 461 ms | 7,808 ms |
| 64 | 582 ms | 5,207 ms |
| 96 | 761 ms | 3,714 ms |
| 128 | 907 ms | 2,697 ms |

The result is a tuning curve: tighter admission reduced premium latency while
standard traffic waited longer. The lowest premium p95 TTFT in this sweep was
461 ms at `maxConcurrency=48`.

The exact upstream Endpoint Picker version was not recorded in these run
artifacts. Use this package to understand the cap tradeoff, not as a direct
RHAII 3.4 versus upstream image comparison. A version comparison requires the
same deterministic traffic, detector settings, model image, engine settings,
and at least three counterbalanced repeats.
