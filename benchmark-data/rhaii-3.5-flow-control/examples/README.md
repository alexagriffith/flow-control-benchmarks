# Red Hat AI Inference 3.5 examples

These examples turn the campaign configurations into complete Kubernetes
manifests. Each file includes the `LLMInferenceService`, its
`InferenceObjective` resources, and the Gateway objects needed by that example.

## Start here

| Goal | Example | Why choose it |
| --- | --- | --- |
| Protect one workload while two model replicas share traffic | [`01-two-priority-scored-routing.yaml`](getting-started/01-two-priority-scored-routing.yaml) | Uses priority and round-robin fairness for service protection, then selects the eligible replica with the shortest queue. |
| Give requests with tighter TTFT objectives earlier queue positions | [`02-slo-deadline-ordering.yaml`](getting-started/02-slo-deadline-ordering.yaml) | Uses the campaign's tested SLO ordering policy inside the protected priority band. |
| Keep two equally important applications from starving each other | [`03-same-priority-fairness.yaml`](getting-started/03-same-priority-fairness.yaml) | Gives each fairness ID alternating dispatch turns, then selects the eligible replica with the shortest queue. |
| Share a model across protected, standard, and retryable Batch work | [`04-priority-standard-batch.yaml`](getting-started/04-priority-standard-batch.yaml) | Reserves progressively more capacity for higher priorities and bounds the negative-priority queue. |
| Preserve more lower-priority progress as load changes | [`05-soft-reflective-scored-routing.yaml`](getting-started/05-soft-reflective-scored-routing.yaml) | Uses the tested soft-reflective ceiling policy with scored multi-replica routing. |
| Protect a prefill/decode service from both prompt-heavy and generation-heavy pressure | [`06-prefill-decode-hybrid.yaml`](getting-started/06-prefill-decode-hybrid.yaml) | Uses the accepted hybrid detector, separate prefill and decode profiles, and scored endpoint selection. |
| Reproduce the campaign's one-versus-two-replica routing baseline | [`03-two-replica-random-baseline.yaml`](benchmark-reproduction/03-two-replica-random-baseline.yaml) | Preserves the exact predictor-free routing policy used to isolate replica scaling. This is evidence reproduction, not the recommended scored-routing starting point. |

## Before applying an example

Change the namespace, Gateway reference, model, accelerator requests, and image
pull settings for your cluster. The numerical limits came from GPT-OSS 20B on
H100 GPUs and must be recalibrated for another model or topology.

Apply the file, wait for the `LLMInferenceService` to become ready, and send
the matching objective and fairness headers with each request:

```text
x-gateway-inference-objective: protected
x-gateway-inference-fairness-id: application-a
```

The Gateway can derive these values from authentication in a production
deployment. The explicit headers make the behavior easy to verify during a
controlled test.

## Evidence boundary

The SLO, random-routing, and P/D profiles reproduce configurations used in
accepted campaign runs. The general scored-routing examples combine tested
flow-control policies with the Red Hat AI Inference 3.5 queue scorer and
maximum-score picker. These are operational starting points; the campaign's
replica-scale and priority-policy comparisons used random selection to isolate
the setting under test.
