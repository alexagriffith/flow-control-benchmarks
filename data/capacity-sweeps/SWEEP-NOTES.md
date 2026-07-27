# 2026-07-21 — GPT-OSS 20B input/output variation pass

Purpose: test whether the flow-control saturation point changes when request
shape changes.

This is separate from the main 512 input / 128 output benchmark and separate
from the rejection probe. The goal is not to retell the full four-scenario
story for every shape. The goal is to find which request shapes create queueing
earlier or later.

## Planned sweep matrix

| Shape | Input tokens | Output tokens | Why it matters |
|---|---:|---:|---|
| Short interactive | 256 | 64 | Fast chat-like baseline. |
| Current baseline | 512 | 128 | Direct comparison to the main report. |
| Longer prompt | 1024 | 128 | More prefill pressure with the same generation length. |
| Longer generation | 512 | 512 | More decode occupancy; should create queueing earlier. |
| Heavier mixed | 2048 | 256 | More enterprise-style prompt size with moderate generation. |

## Run shape

- Model: `openai/gpt-oss-20b`
- Service: `LLMInferenceService/llm-test/gpt-oss-20b-fc`
- Flow-control config: normal benchmark config, not rejection-probe config
- Sweep only: `--skip-scenarios`
- Sweep points: 8, 16, 24, 32, 48, 64, 96, 128, 160
- Initial duration target: 20 seconds per point
- Prompt pool target: 96 prompts per shape

## Question to answer

Where does each shape first show waiting requests, and how do P95 time to first
token and mean endpoint-picker queue time move as the input/output lengths
change?

## Results

All five shapes first showed vLLM waiting requests at concurrency 160 in this
20-second sweep. The saturation knee did not move earlier at these tested
points, but the cost of being at the knee changed substantially.

| Shape | First waiting concurrency | Requests/s at 160 | P95 TTFT at 160 | Max waiting at 160 | Mean endpoint-picker queue at 160 |
|---|---:|---:|---:|---:|---:|
| 256 input / 64 output | 160 | 165.05 | 568.5 ms | 27 | 46.7 ms |
| 512 input / 128 output | 160 | 88.35 | 688.6 ms | 28 | 102.2 ms |
| 1024 input / 128 output | 160 | 84.85 | 760.7 ms | 32 | 90.7 ms |
| 512 input / 512 output | 160 | 27.20 | 5990.7 ms | 32 | 55.6 ms |
| 2048 input / 256 output | 160 | 40.00 | 2406.5 ms | 32 | 119.1 ms |

Interpretation: same concurrency is not the same workload. The short
interactive shape cleared far more requests per second. The longer-generation
shape created much worse user-facing P95 time to first token at the same
concurrency, even though the first waiting point stayed at 160. For the blog,
this is a strong teaching point: concurrency tells us how many requests are in
flight, but input/output token shape tells us how heavy those requests are.

Follow-up: if we want one heavier full scenario rerun, use 512 input / 512
output. It produced the sharpest latency penalty and would show priority
behavior under a more painful workload.
