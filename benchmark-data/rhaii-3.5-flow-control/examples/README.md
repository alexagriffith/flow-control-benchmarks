# Red Hat AI Inference 3.5 examples

These examples turn the campaign configurations into Kubernetes manifests.
Each file includes the `LLMInferenceService`, its
`InferenceObjective` resources, and the Gateway objects needed by that example.
The soft-PT composition also records the classifier policy interface; package
the trusted classifier as its own reviewed service.

## Start here

| Goal | Example | Why choose it |
| --- | --- | --- |
| Protect one workload while two model replicas share traffic | [`01-two-priority-scored-routing.yaml`](getting-started/01-two-priority-scored-routing.yaml) | Uses priority and round-robin fairness for service protection, then selects the eligible replica with the shortest queue. |
| Give requests with tighter TTFT objectives earlier queue positions | [`02-slo-deadline-ordering.yaml`](getting-started/02-slo-deadline-ordering.yaml) | Uses the campaign's tested SLO ordering policy inside the protected priority band. |
| Keep two equally important applications from starving each other | [`03-same-priority-fairness.yaml`](getting-started/03-same-priority-fairness.yaml) | Gives each fairness ID alternating dispatch turns, then selects the eligible replica with the shortest queue. |
| Share a model across protected, standard, and retryable Batch work | [`04-priority-standard-batch.yaml`](getting-started/04-priority-standard-batch.yaml) | Reserves progressively more capacity for higher priorities and bounds the negative-priority queue. |
| Preserve more lower-priority progress as load changes | [`05-soft-reflective-scored-routing.yaml`](getting-started/05-soft-reflective-scored-routing.yaml) | Uses soft-reflective priority ceilings, then chooses an eligible replica from its queue score. |
| Protect a prefill/decode service from both prompt-heavy and generation-heavy pressure | [`06-prefill-decode-hybrid.yaml`](getting-started/06-prefill-decode-hybrid.yaml) | Uses the accepted hybrid detector, separate prefill and decode profiles, and scored endpoint selection. |

## Reproduce a campaign configuration

These files preserve the Endpoint Picker graph used for each controlled
comparison. Random selection appears where the benchmark intentionally removed
queue and cache scores so that it could isolate the setting under test.

| Campaign study | Configuration | What it isolates |
| --- | --- | --- |
| Capacity and request-cap calibration | [`01-capacity-request-concurrency.yaml`](benchmark-reproduction/01-capacity-request-concurrency.yaml) | One replica, request detection, and predictor-free selection |
| Four production scenarios | [`02-four-scenario-request-detector.yaml`](benchmark-reproduction/02-four-scenario-request-detector.yaml) | Priority, Batch isolation, consolidation, and peer fairness with request detection |
| One-versus-two-replica routing | [`03-two-replica-random-baseline.yaml`](benchmark-reproduction/03-two-replica-random-baseline.yaml) | Replica count with neutral endpoint selection |
| SLO deadline ordering | [`04-slo-deadline-ordering.yaml`](benchmark-reproduction/04-slo-deadline-ordering.yaml) | Queue ordering while endpoint selection remains neutral |
| Fixed priority holdback | [`05-fixed-priority-holdback.yaml`](benchmark-reproduction/05-fixed-priority-holdback.yaml) | Rank-based fixed ceilings |
| Soft-reflective ceilings | [`06-soft-reflective-ceilings.yaml`](benchmark-reproduction/06-soft-reflective-ceilings.yaml) | Dynamic lower-priority progress under competing demand |
| Request-cost metadata | [`07-request-cost-metadata.yaml`](benchmark-reproduction/07-request-cost-metadata.yaml) | Completed-token metadata reporting through Envoy |
| Prefill/decode flow control | [`08-prefill-decode-hybrid.yaml`](benchmark-reproduction/08-prefill-decode-hybrid.yaml) | Stage-aware hybrid admission and separate prefill/decode selection |
| Soft provisioned-throughput composition | [`09-soft-pt-serving-policy.yaml`](benchmark-reproduction/09-soft-pt-serving-policy.yaml) | Exact serving-side priorities plus the sanitized classifier policy contract |

## Before applying an example

The examples create a Namespace, Gateway, `LLMInferenceService`, and matching
`InferenceObjective` resources. The P/D example also creates ServiceMonitors
for the Endpoint Picker and vLLM workers.

The cluster must already provide the `istio` GatewayClass, the
`inference-gateway-config` ConfigMap, and the Gateway API, KServe, and llm-d
custom resources. Install and configure those cluster-owned components first.

The Namespace label `inference-gateway-access: "true"` matches the Gateway's
`allowedRoutes` selector and permits routes from that namespace to attach to
the Gateway. Authentication and flow control remain separate settings.

Change the namespace, Gateway reference, model, accelerator requests, and image
pull settings for your cluster. The numerical limits came from GPT-OSS 20B on
H100 GPUs and must be recalibrated for another model or topology.

Apply the file, wait for the `LLMInferenceService` to become ready, and send
the matching objective and fairness headers with each request:

```text
x-llm-d-inference-objective: pd-protected
x-llm-d-inference-fairness-id: workflow-a
```

The Gateway can derive these values from authentication in a production
deployment. The explicit headers make the behavior easy to verify during a
controlled test.

## Evidence boundary

The `benchmark-reproduction` files preserve controlled campaign
configurations. The `getting-started` files turn the same tested policies into
operational examples with queue-aware maximum-score selection where it adds a
meaningful endpoint choice. Recalibrate capacity values before deployment.
