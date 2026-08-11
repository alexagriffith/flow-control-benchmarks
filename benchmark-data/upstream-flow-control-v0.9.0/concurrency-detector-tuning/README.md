# Concurrency detector tuning

This package measures how `maxConcurrency` changes latency under one fixed
traffic pattern on Endpoint Picker v0.9.0. The run metrics record build commit
`5f4e762f341a5196393ce79f8a57c3e1900c4a6b`. The sweep used GPT-OSS 20B,
512 input tokens, 128 output tokens, prefix caching off, and two 120-second
repeats at each cap.

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

Use this package to tune the request-concurrency cap within v0.9.0. It does not
compare v0.9.0 with RHAII 3.4 because those campaigns used different traffic
and detector configurations. A direct implementation comparison requires the
same deterministic traffic, model image, engine settings, and at least three
counterbalanced repeats.
