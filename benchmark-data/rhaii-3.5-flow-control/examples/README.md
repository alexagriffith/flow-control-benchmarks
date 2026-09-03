# Red Hat AI Inference 3.5 examples

Use `getting-started` for common deployment patterns and
`benchmark-reproduction` for the configurations used in the campaign. Each
file includes the Gateway, `LLMInferenceService`, and `InferenceObjective`
resources for that example.

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
comparison. Random selection isolates the setting under test when queue and
cache scores are outside the comparison.

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

The cluster must provide the `istio` GatewayClass, the
`inference-gateway-config` ConfigMap, and the Gateway API, KServe, and llm-d
custom resources. Update the namespace, Gateway reference, model, accelerator,
and image-pull settings for the target cluster. Recalibrate the numerical
limits for the model and topology.

Each file includes a Gateway so it can stand alone. Create that Gateway once,
or point `spec.router.gateway.refs` to an existing inference Gateway.

Set the Gateway namespace before applying a file. Red Hat AI Inference commonly
uses `redhat-ods-applications`:

```bash
export GATEWAY_NAMESPACE=redhat-ods-applications
envsubst '$GATEWAY_NAMESPACE' < getting-started/01-two-priority-scored-routing.yaml | kubectl apply -f -
```

The P/D example adds ServiceMonitors for the Endpoint Picker and vLLM workers.
The soft-PT example records the serving policy and classifier interface; run
the trusted classifier as a separately reviewed service.

After the `LLMInferenceService` is ready, send the matching objective and
fairness headers with each request:

```text
x-llm-d-inference-objective: pd-protected
x-llm-d-inference-fairness-id: workflow-a
```

The Gateway can also derive these values from authenticated identity.

## Evidence boundary

The reproduction files preserve tested configurations. The getting-started
files use the same policies with queue-aware endpoint selection where it adds a
meaningful choice.
