#!/usr/bin/env ruby
# frozen_string_literal: true

require "digest"
require "csv"
require "json"
require "pathname"
require "yaml"

ROOT = Pathname.new(__dir__).parent
PACKAGE = ROOT.join("benchmark-data/rhaii-3.5-flow-control")
README = PACKAGE.join("README.md")
RESULTS = PACKAGE.join("results.json")
ANALYSIS = PACKAGE.join("pd-flow-control/analysis.json")
RECIPE = PACKAGE.join("pd-flow-control/configuration/selected-recipe.yaml")
SOFT_PT_ANALYSIS = PACKAGE.join("soft-pt/analysis.json")
MANIFEST = PACKAGE.join("manifest.json")
GETTING_STARTED_FILES = [
  PACKAGE.join("examples/getting-started/01-two-priority-scored-routing.yaml"),
  PACKAGE.join("examples/getting-started/02-slo-deadline-ordering.yaml"),
  PACKAGE.join("examples/getting-started/03-same-priority-fairness.yaml"),
  PACKAGE.join("examples/getting-started/04-priority-standard-batch.yaml"),
  PACKAGE.join("examples/getting-started/05-soft-reflective-scored-routing.yaml"),
  PACKAGE.join("examples/getting-started/06-prefill-decode-hybrid.yaml")
].freeze
REPRODUCTION_FILES = [
  PACKAGE.join("examples/benchmark-reproduction/01-capacity-request-concurrency.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/02-four-scenario-request-detector.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/03-two-replica-random-baseline.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/04-slo-deadline-ordering.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/05-fixed-priority-holdback.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/06-soft-reflective-ceilings.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/07-request-cost-metadata.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml"),
  PACKAGE.join("examples/benchmark-reproduction/09-soft-pt-serving-policy.yaml")
].freeze
EXAMPLE_FILES = (GETTING_STARTED_FILES + REPRODUCTION_FILES).freeze

STUDY_RESULTS = {
  "capacity-envelope" => "capacity_envelope",
  "request-concurrency" => "request_concurrency_cap",
  "production-scenarios" => "four_scenario_reproduction",
  "slo-deadline-ordering" => "slo_deadline_ordering",
  "priority-usage-limit-policies" => "priority_usage_limit_policies",
  "batch-dispatch" => "batch_dispatch",
  "batch-eviction" => "batch_eviction",
  "request-cost-metadata" => "request_cost_metadata",
  "routing-scale" => "routing_scale",
  "stability-replay" => "long_replay"
}.freeze

ALLOWED_FILES = %w[
  README.md
  assets/capacity-slo-envelope.svg
  assets/slo-deadline-ordering.svg
  batch-dispatch/README.md
  batch-dispatch/analysis.json
  batch-dispatch/control-summary.csv
  batch-dispatch/request-results.csv
  batch-dispatch/run-config.json
  batch-dispatch/run-evidence.csv
  batch-dispatch/scenario.json
  batch-dispatch/summary.csv
  batch-dispatch/system-metrics.csv
  batch-dispatch/traffic-samples.csv
  batch-eviction/README.md
  batch-eviction/analysis.json
  batch-eviction/paired-effects.csv
  batch-eviction/run-config.json
  batch-eviction/run-evidence.json
  batch-eviction/summary.csv
  capacity-envelope/README.md
  capacity-envelope/analysis.json
  capacity-envelope/request-results.csv
  capacity-envelope/run-config.json
  capacity-envelope/run-evidence.csv
  capacity-envelope/scenario.json
  capacity-envelope/summary.csv
  capacity-envelope/system-metrics.csv
  capacity-envelope/traffic-samples.csv
  examples/README.md
  examples/benchmark-reproduction/01-capacity-request-concurrency.yaml
  examples/benchmark-reproduction/02-four-scenario-request-detector.yaml
  examples/benchmark-reproduction/03-two-replica-random-baseline.yaml
  examples/benchmark-reproduction/04-slo-deadline-ordering.yaml
  examples/benchmark-reproduction/05-fixed-priority-holdback.yaml
  examples/benchmark-reproduction/06-soft-reflective-ceilings.yaml
  examples/benchmark-reproduction/07-request-cost-metadata.yaml
  examples/benchmark-reproduction/08-prefill-decode-hybrid.yaml
  examples/benchmark-reproduction/09-soft-pt-serving-policy.yaml
  examples/getting-started/01-two-priority-scored-routing.yaml
  examples/getting-started/02-slo-deadline-ordering.yaml
  examples/getting-started/03-same-priority-fairness.yaml
  examples/getting-started/04-priority-standard-batch.yaml
  examples/getting-started/05-soft-reflective-scored-routing.yaml
  examples/getting-started/06-prefill-decode-hybrid.yaml
  manifest.json
  pd-flow-control/README.md
  pd-flow-control/analysis.json
  pd-flow-control/configuration/selected-recipe.yaml
  pd-flow-control/detector-screens.csv
  pd-flow-control/eviction-pairs.csv
  pd-flow-control/priority-repeats.csv
  pd-flow-control/request-results.csv
  pd-flow-control/run-config.json
  pd-flow-control/run-evidence.csv
  pd-flow-control/scenario.json
  pd-flow-control/summary.csv
  pd-flow-control/system-metrics.csv
  pd-flow-control/traffic-samples.csv
  priority-usage-limit-policies/README.md
  priority-usage-limit-policies/analysis.json
  priority-usage-limit-policies/request-results.csv
  priority-usage-limit-policies/run-config.json
  priority-usage-limit-policies/run-evidence.csv
  priority-usage-limit-policies/scenario.json
  priority-usage-limit-policies/summary.csv
  priority-usage-limit-policies/system-metrics.csv
  priority-usage-limit-policies/traffic-samples.csv
  production-scenarios/README.md
  production-scenarios/analysis.json
  production-scenarios/request-results.csv
  production-scenarios/run-config.json
  production-scenarios/run-evidence.csv
  production-scenarios/scenario.json
  production-scenarios/summary.csv
  production-scenarios/system-metrics.csv
  production-scenarios/traffic-samples.csv
  request-concurrency/README.md
  request-concurrency/analysis.json
  request-concurrency/request-results.csv
  request-concurrency/run-config.json
  request-concurrency/run-evidence.csv
  request-concurrency/scenario.json
  request-concurrency/summary.csv
  request-concurrency/system-metrics.csv
  request-concurrency/traffic-samples.csv
  request-cost-metadata/README.md
  request-cost-metadata/analysis.json
  request-cost-metadata/request-results.csv
  request-cost-metadata/run-config.json
  request-cost-metadata/run-evidence.json
  results.json
  routing-scale/README.md
  routing-scale/analysis.json
  routing-scale/request-results.csv
  routing-scale/run-config.json
  routing-scale/run-evidence.csv
  routing-scale/scenario.json
  routing-scale/summary.csv
  routing-scale/system-metrics.csv
  routing-scale/traffic-samples.csv
  slo-deadline-ordering/README.md
  slo-deadline-ordering/analysis.json
  slo-deadline-ordering/request-results.csv
  slo-deadline-ordering/run-config.json
  slo-deadline-ordering/run-evidence.csv
  slo-deadline-ordering/scenario.json
  slo-deadline-ordering/summary.csv
  slo-deadline-ordering/system-metrics.csv
  slo-deadline-ordering/traffic-samples.csv
  soft-pt/README.md
  soft-pt/analysis.json
  soft-pt/paired-effects.csv
  soft-pt/policy-summary.csv
  soft-pt/request-results.csv
  soft-pt/run-config.json
  soft-pt/run-evidence.csv
  soft-pt/scenario.json
  soft-pt/summary.csv
  soft-pt/system-metrics.csv
  soft-pt/traffic-samples.csv
  stability-replay/README.md
  stability-replay/analysis.json
  stability-replay/request-results.csv
  stability-replay/run-config.json
  stability-replay/run-evidence.csv
  stability-replay/scenario.json
  stability-replay/summary.csv
  stability-replay/system-metrics.csv
  stability-replay/traffic-samples.csv
].freeze

APPROVED_SVGS = %w[
  assets/capacity-slo-envelope.svg
  assets/slo-deadline-ordering.svg
].freeze

REQUIRED_RESULT_GROUPS = %w[
  batch_dispatch
  batch_eviction
  capacity_envelope
  four_scenario_reproduction
  long_replay
  pd_flow_control
  priority_usage_limit_policies
  request_concurrency_cap
  request_cost_metadata
  routing_scale
  slo_deadline_ordering
  soft_pt
].freeze

def fail_check(message)
  warn "FAIL: #{message}"
  exit 1
end

def assert(condition, message)
  fail_check(message) unless condition
end

def relative_files
  PACKAGE.glob("**/*", File::FNM_DOTMATCH)
         .select(&:file?)
         .map { |path| path.relative_path_from(PACKAGE).to_s }
         .sort
end

files = relative_files
assert(files == ALLOWED_FILES.sort,
       "package contents differ from the public allowlist: #{files.inspect}")

readme = README.read
results = JSON.parse(RESULTS.read)
analysis = JSON.parse(ANALYSIS.read)
recipe = YAML.safe_load(RECIPE.read, permitted_classes: [], aliases: false)
soft_pt_analysis = JSON.parse(SOFT_PT_ANALYSIS.read)
manifest = JSON.parse(MANIFEST.read)

EXAMPLE_FILES.each do |path|
  documents = YAML.load_stream(path.read)
  kinds = documents.map { |document| document.fetch("kind") }
  assert(kinds.count("Namespace") == 1, "#{path.basename} must include one Namespace")
  assert(kinds.count("Gateway") == 1, "#{path.basename} must include one Gateway")
  assert(kinds.count("LLMInferenceService") == 1,
         "#{path.basename} must include one LLMInferenceService")
  assert(kinds.count("InferenceObjective") >= 2,
         "#{path.basename} must include at least two InferenceObjectives")

  service = documents.find { |document| document["kind"] == "LLMInferenceService" }
  objectives = documents.select { |document| document["kind"] == "InferenceObjective" }
  service_name = service.dig("metadata", "name")
  pool_name = "#{service_name}-inference-pool"
  objectives.each do |objective|
    assert(objective.dig("spec", "poolRef", "name") == pool_name,
           "#{path.basename} objective points to the wrong InferencePool")
  end

  inline = service.dig("spec", "router", "scheduler", "config", "inline")
  assert(inline&.fetch("featureGates", [])&.include?("flowControl"),
         "#{path.basename} does not enable flow control")
  plugins = inline.fetch("plugins")
  plugin_names = plugins.map { |plugin| plugin["name"] || plugin.fetch("type") }
  profile_refs = inline.fetch("schedulingProfiles").flat_map do |profile|
    profile.fetch("plugins").map { |entry| entry.fetch("pluginRef") }
  end
  profile_refs.each do |ref|
    assert(plugin_names.include?(ref), "#{path.basename} has unresolved plugin reference #{ref}")
  end
  inline.fetch("schedulingProfiles").each do |profile|
    picker_refs = profile.fetch("plugins").map { |entry| entry.fetch("pluginRef") }
                         .grep(/picker\z/)
    assert(picker_refs.length == 1,
           "#{path.basename} profile #{profile.fetch('name')} must select one picker")
  end

  configured_priorities = inline.dig("flowControl", "priorityBands")
                                .map { |band| band.fetch("priority") }.sort
  objective_priorities = objectives.map { |objective| objective.dig("spec", "priority") }.uniq.sort
  assert(configured_priorities == objective_priorities,
         "#{path.basename} priorities differ between flow control and objectives")
end

GETTING_STARTED_FILES.each do |path|
  service = YAML.load_stream(path.read).find { |document| document["kind"] == "LLMInferenceService" }
  profiles = service.dig("spec", "router", "scheduler", "config", "inline",
                         "schedulingProfiles")
  profiles.each do |profile|
    refs = profile.fetch("plugins").map { |entry| entry.fetch("pluginRef") }
    assert(refs.include?("queue-scorer") && refs.include?("max-score-picker"),
           "#{path.basename} must score queues and select the maximum score")
  end
end

soft_path = PACKAGE.join("examples/getting-started/05-soft-reflective-scored-routing.yaml")
soft_service = YAML.load_stream(soft_path.read).find do |document|
  document["kind"] == "LLMInferenceService"
end
soft_inline = soft_service.dig("spec", "router", "scheduler", "config", "inline")
assert(soft_inline.dig("flowControl", "usageLimitPolicyPluginRef") == "soft-reflective",
       "soft-reflective example must select the tested usage-limit policy")
assert(soft_inline.fetch("plugins").any? do |plugin|
  plugin["name"] == "soft-reflective" && plugin["type"] == "soft-reflective-ceiling-policy"
end, "soft-reflective example must declare its tested policy plugin")

pd_path = PACKAGE.join("examples/getting-started/06-prefill-decode-hybrid.yaml")
pd_documents = YAML.load_stream(pd_path.read)
pd_service = pd_documents.find { |document| document["kind"] == "LLMInferenceService" }
pd_inline = pd_service.dig("spec", "router", "scheduler", "config", "inline")
pd_plugins = pd_inline.fetch("plugins").to_h do |plugin|
  [plugin["name"] || plugin.fetch("type"), plugin]
end
assert(pd_service.dig("spec", "prefill", "replicas") == 1 && pd_service.dig("spec", "replicas") == 1,
       "P/D example must create one prefill and one decode worker")
assert(pd_documents.count { |document| document["kind"] == "ServiceMonitor" } == 2,
       "P/D example must monitor both the Endpoint Picker and vLLM workers")
assert(pd_plugins.dig("concurrency-detector", "parameters") == {
  "concurrencyMode" => "hybrid",
  "inFlightLoadProducerName" => "inflight-load",
  "maxConcurrency" => 64,
  "maxTokenConcurrency" => 80_000,
  "headroom" => 0.1
}, "P/D example detector differs from the accepted recipe")
assert(pd_inline.dig("flowControl", "usageLimitPolicyPluginRef") == "priority-holdback-050",
       "P/D example must select the accepted priority holdback")
assert(pd_inline.fetch("schedulingProfiles").map { |profile| profile.fetch("name") } ==
       %w[prefill decode], "P/D example must define separate prefill and decode profiles")

random_example = PACKAGE.join("examples/benchmark-reproduction/03-two-replica-random-baseline.yaml")
random_baseline = YAML.load_stream(random_example.read).find do |document|
  document["kind"] == "LLMInferenceService"
end.dig("spec", "router", "scheduler", "config", "inline", "schedulingProfiles", 0, "plugins")
               .map { |entry| entry.fetch("pluginRef") }
assert(random_baseline == %w[concurrency-detector random-picker],
       "replica baseline must preserve the tested predictor-free profile")

slo_path = PACKAGE.join("examples/benchmark-reproduction/04-slo-deadline-ordering.yaml")
slo_inline = YAML.load_stream(slo_path.read).find do |document|
  document["kind"] == "LLMInferenceService"
end.dig("spec", "router", "scheduler", "config", "inline")
slo_bands = slo_inline.dig("flowControl", "priorityBands").to_h do |band|
  [band.fetch("priority"), band.fetch("orderingPolicyRef")]
end
assert(slo_bands == {
  100 => "slo-deadline-ordering-policy",
  50 => "fcfs-ordering-policy",
  0 => "fcfs-ordering-policy",
  -10 => "fcfs-ordering-policy"
}, "SLO reproduction configuration differs from the accepted ordering graph")

fixed_path = PACKAGE.join("examples/benchmark-reproduction/05-fixed-priority-holdback.yaml")
fixed_inline = YAML.load_stream(fixed_path.read).find do |document|
  document["kind"] == "LLMInferenceService"
end.dig("spec", "router", "scheduler", "config", "inline")
fixed_plugin = fixed_inline.fetch("plugins").find { |plugin| plugin["name"] == "priority-holdback" }
assert(fixed_inline.dig("flowControl", "usageLimitPolicyPluginRef") == "priority-holdback" &&
       fixed_plugin.fetch("parameters") == {
         "domain" => "rank", "shape" => "linear", "minCeiling" => 0.5, "maxCeiling" => 1
       }, "fixed-holdback reproduction configuration differs from the accepted policy")

reflective_path = PACKAGE.join("examples/benchmark-reproduction/06-soft-reflective-ceilings.yaml")
reflective_inline = YAML.load_stream(reflective_path.read).find do |document|
  document["kind"] == "LLMInferenceService"
end.dig("spec", "router", "scheduler", "config", "inline")
assert(reflective_inline.dig("flowControl", "usageLimitPolicyPluginRef") == "soft-reflective" &&
       reflective_inline.fetch("plugins").any? do |plugin|
         plugin["name"] == "soft-reflective" && plugin["type"] == "soft-reflective-ceiling-policy"
       end, "soft-reflective reproduction configuration differs from the accepted policy")

cost_path = PACKAGE.join("examples/benchmark-reproduction/07-request-cost-metadata.yaml")
cost_inline = YAML.load_stream(cost_path.read).find do |document|
  document["kind"] == "LLMInferenceService"
end.dig("spec", "router", "scheduler", "config", "inline")
cost_reporter = cost_inline.fetch("plugins").find do |plugin|
  plugin["name"] == "total-tokens-cost-reporter"
end
cost_attribute = cost_reporter.dig("parameters", "attributes", 0)
assert(cost_attribute.dig("key", "namespace") == "envoy.lb" &&
       cost_attribute.dig("key", "name") == "x-gateway-inference-request-cost" &&
       cost_attribute["expression"] == "usage.prompt_tokens + usage.completion_tokens",
       "request-cost reproduction configuration differs from the accepted reporter")

pt_path = PACKAGE.join("examples/benchmark-reproduction/09-soft-pt-serving-policy.yaml")
pt_documents = YAML.load_stream(pt_path.read)
pt_inline = pt_documents.find do |document|
  document["kind"] == "LLMInferenceService"
end.dig("spec", "router", "scheduler", "config", "inline")
pt_detector = pt_inline.fetch("plugins").find { |plugin| plugin["name"] == "concurrency-detector" }
pt_policy = JSON.parse(pt_documents.find do |document|
  document["kind"] == "ConfigMap" && document.dig("metadata", "name") == "soft-pt-classifier-policy"
end.dig("data", "policy.json"))
assert(pt_detector.fetch("parameters").slice("concurrencyMode", "maxConcurrency", "headroom") == {
  "concurrencyMode" => "requests", "maxConcurrency" => 28, "headroom" => 0
}, "soft-PT serving detector differs from the accepted configuration")
assert(pt_inline.dig("flowControl", "usageLimitPolicyPluginRef") == "static-usage-limit",
       "soft-PT serving policy must preserve the accepted static ceiling")
assert(pt_policy.dig("tested_request", "estimated_normalized_tokens") == 895 &&
       pt_policy.dig("entitlement", "rate_normalized_tokens_per_second") == 4475 &&
       pt_policy.dig("entitlement", "burst_normalized_tokens") == 8950,
       "soft-PT classifier policy differs from the accepted values")

assert(results.fetch("results").keys.sort == REQUIRED_RESULT_GROUPS.sort,
       "normalized result inventory differs from the reviewed promotion set")

STUDY_RESULTS.each do |directory, result_id|
  study_readme = PACKAGE.join(directory, "README.md").read
  study_analysis = JSON.parse(PACKAGE.join(directory, "analysis.json").read)
  assert(study_readme.lines.first&.start_with?("# "),
         "#{directory} README must open with its study question")
  assert(study_readme.include?("**Takeaway:**"),
         "#{directory} README must state its takeaway")
  assert(study_analysis["result_id"] == result_id,
         "#{directory} analysis has the wrong result_id")
  assert(study_analysis["result"] == results.dig("results", result_id),
         "#{directory} analysis differs from results.json")
end

assert(readme.scan(/(?:!\[[^\]]*\]\(|<img\s+[^>]*src=")[^)"]*\.svg/).length == 2,
       "README must embed exactly the two approved SVGs")
APPROVED_SVGS.each do |svg|
  assert(readme.include?(svg), "README does not embed approved visual #{svg}")
end

%w[Review\ outline planned\ artifact Planned\ public TODO TBD].each do |phrase|
  assert(!readme.match?(/#{Regexp.escape(phrase)}/i),
         "README contains unfinished publication language: #{phrase}")
end

PACKAGE.glob("**/README.md").each do |readme_path|
  content = readme_path.read
  assert(!content.match?(/^## Business question$/i),
         "#{readme_path.relative_path_from(PACKAGE)} uses a generic Business question heading")
  assert(!content.match?(/\bnot\b/i),
         "#{readme_path.relative_path_from(PACKAGE)} contains reactionary 'not' wording")

  link_targets = content.scan(/\[[^\]]*\]\(([^)]+)\)/).flatten +
                 content.scan(/<img\s+[^>]*src="([^"]+)"/).flatten
  link_targets.reject { |target| target.match?(%r{\A(?:https?://|#)}) }.each do |target|
    clean_target = target.split("#", 2).first
    assert(readme_path.dirname.join(clean_target).exist?,
           "missing target #{target} in #{readme_path.relative_path_from(PACKAGE)}")
  end
end

public_text = files.select { |path| path.end_with?(".md", ".json", ".yaml") }
                   .map { |path| PACKAGE.join(path).read }.join("\n")
assert(public_text.include?("x-llm-d-inference-objective") &&
       public_text.include?("x-llm-d-inference-fairness-id"),
       "examples must document the current llm-d objective and fairness headers")
assert(!public_text.match?(/x-gateway-inference-(?:objective|fairness-id)/),
       "package contains deprecated Gateway objective or fairness headers")

text_files = files.select { |path| path.end_with?(".md", ".json", ".yaml", ".svg", ".csv") }
sanitization_patterns = {
  "local user path" => %r{/(?:Users|home)/[^/\s]+/},
  "private IPv4 address" => /\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b/,
  "cloud-internal hostname" => /\b(?:compute\.internal|cluster\.local)\b/i,
  "private key" => /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/,
  "bearer token" => /\bBearer\s+[A-Za-z0-9._~-]{16,}/i,
  "common secret assignment" => /\b(?:client_secret|access_token|refresh_token|password)\s*[:=]\s*["']?[^\s"']{8,}/i,
  "Slack token" => /\bxox[baprs]-[A-Za-z0-9-]{10,}/,
  "AWS access key" => /\bAKIA[0-9A-Z]{16}\b/
}.freeze

text_files.each do |relative|
  content = PACKAGE.join(relative).read
  sanitization_patterns.each do |label, pattern|
    assert(!content.match?(pattern), "#{relative} contains #{label}")
  end
end

RUN_LEVEL_STUDIES = {
  "capacity-envelope" => 4,
  "request-concurrency" => 6,
  "production-scenarios" => 36,
  "slo-deadline-ordering" => 6,
  "priority-usage-limit-policies" => 6,
  "batch-dispatch" => 9,
  "soft-pt" => 9,
  "pd-flow-control" => 26,
  "routing-scale" => 6,
  "stability-replay" => 1
}.freeze

RUN_LEVEL_STUDIES.each do |study, expected_runs|
  summary = CSV.read(PACKAGE.join(study, "summary.csv"), headers: true)
  evidence = CSV.read(PACKAGE.join(study, "run-evidence.csv"), headers: true)
  requests = CSV.read(PACKAGE.join(study, "request-results.csv"), headers: true)
  traffic = CSV.read(PACKAGE.join(study, "traffic-samples.csv"), headers: true)
  metrics = CSV.read(PACKAGE.join(study, "system-metrics.csv"), headers: true)
  assert(summary.length == expected_runs, "#{study} has the wrong run-summary count")
  assert(evidence.length == expected_runs, "#{study} has the wrong validation-record count")
  assert(requests.length.positive?, "#{study} has no request-level evidence")
  assert(traffic.length.positive?, "#{study} has no traffic evidence")
  assert(metrics.length.positive? || study == "stability-replay",
         "#{study} has no system-metric evidence")
  assert(evidence.all? { |row| row["accepted"] == "true" },
         "#{study} includes a run that was not accepted")
end

cost_rows = CSV.read(PACKAGE.join("request-cost-metadata/request-results.csv"), headers: true)
assert(cost_rows.length == 220, "request-cost metadata must contain 220 request outcomes")
positive_cost_rows = cost_rows.select { |row| !row["expected_request_cost"].to_s.empty? }
negative_cost_rows = cost_rows.select { |row| row["expected_request_cost"].to_s.empty? }
assert(positive_cost_rows.length == 200 &&
       positive_cost_rows.all? do |row|
         row["expected_request_cost"] == row["observed_request_cost"] &&
           row["cost_present"] == "true"
       end, "usage-bearing request cost does not match the observer")
assert(negative_cost_rows.length == 20 &&
       negative_cost_rows.all? { |row| row["cost_present"] == "false" },
       "negative-control requests unexpectedly contain request cost")

eviction_evidence = JSON.parse(PACKAGE.join("batch-eviction/run-evidence.json").read)
assert(eviction_evidence.length == 2 &&
       eviction_evidence.all? { |record| record.fetch("pairs").length == 12 },
       "Batch eviction must contain twelve matched pairs at each reserve")

assert(recipe["apiVersion"] == "llm-d.ai/v1alpha1", "unexpected recipe API version")
assert(recipe["kind"] == "EndpointPickerConfig", "unexpected recipe kind")
assert(recipe.fetch("featureGates").include?("flowControl"), "flowControl feature gate missing")

flow = recipe.fetch("flowControl")
assert(flow["enableEviction"].nil?, "base recipe must not enable in-flight eviction")
assert(flow["usageLimitPolicyPluginRef"] == "priority-holdback-050",
       "selected priority holdback does not match accepted recipe")
assert(flow.fetch("priorityBands").map { |band| band.fetch("priority") } == [100, 0, -10],
       "priority bands must match accepted recipe")

plugins = recipe.fetch("plugins")
plugin_by_ref = plugins.to_h { |plugin| [plugin["name"] || plugin.fetch("type"), plugin] }
refs = []
refs << flow.dig("saturationDetector", "pluginRef")
refs << flow["usageLimitPolicyPluginRef"]
refs << flow.dig("defaultPriorityBand", "fairnessPolicyRef")
refs << flow.dig("defaultPriorityBand", "orderingPolicyRef")
refs << flow.dig("defaultNegativePriorityBand", "fairnessPolicyRef")
refs << flow.dig("defaultNegativePriorityBand", "orderingPolicyRef")
flow.fetch("priorityBands").each do |band|
  refs << band["fairnessPolicyRef"]
  refs << band["orderingPolicyRef"]
end
recipe.dig("requestHandler", "parsers").each { |entry| refs << entry["pluginRef"] }
recipe.fetch("schedulingProfiles").each do |profile|
  profile.fetch("plugins").each { |entry| refs << entry["pluginRef"] }
end
refs.compact.uniq.each do |ref|
  assert(plugin_by_ref.key?(ref), "unresolved plugin reference #{ref}")
end

detector = plugin_by_ref.fetch("concurrency-detector").fetch("parameters")
assert(detector == {
  "concurrencyMode" => "hybrid",
  "inFlightLoadProducerName" => "inflight-load",
  "maxConcurrency" => 64,
  "maxTokenConcurrency" => 80_000,
  "headroom" => 0.1
}, "detector parameters do not match accepted recipe")

producer = plugin_by_ref.fetch("inflight-load").fetch("parameters")
assert(producer["addEstimatedOutputTokens"] == false,
       "accepted recipe uses addEstimatedOutputTokens: false")

holdback = plugin_by_ref.fetch("priority-holdback-050").fetch("parameters")
assert(holdback == {
  "domain" => "rank",
  "shape" => "linear",
  "minCeiling" => 0.5,
  "maxCeiling" => 1
}, "priority holdback parameters do not match accepted recipe")

handler = plugin_by_ref.fetch("disagg-profile-handler")
assert(handler.fetch("parameters") == {
  "deciders" => { "prefill" => "always-disagg-pd-decider" }
}, "P/D handler includes fields that were not in the accepted configuration")

selected = analysis.fetch("selected_recipe")
assert(selected["max_concurrency"] == detector["maxConcurrency"],
       "analysis max_concurrency differs from recipe")
assert(selected["max_token_concurrency"] == detector["maxTokenConcurrency"],
       "analysis max_token_concurrency differs from recipe")
assert(selected["headroom"] == detector["headroom"],
       "analysis headroom differs from recipe")
assert(selected.dig("priority_ceiling_policy", "minimum") == holdback["minCeiling"],
       "analysis minimum priority ceiling differs from recipe")
assert(selected.dig("priority_ceiling_policy", "maximum") == holdback["maxCeiling"].to_f,
       "analysis maximum priority ceiling differs from recipe")

assert(results.dig("results", "pd_flow_control", "analysis") ==
       "pd-flow-control/analysis.json", "results record has wrong P/D analysis link")
assert(results.dig("results", "pd_flow_control", "configuration") ==
       "pd-flow-control/configuration/selected-recipe.yaml",
       "results record has wrong P/D recipe link")

assert(results.dig("results", "soft_pt", "analysis") ==
       "soft-pt/analysis.json", "results record has wrong soft PT analysis link")
assert(soft_pt_analysis.dig("test_design", "accepted_runs") == 9,
       "soft PT analysis must contain nine accepted runs")
assert(soft_pt_analysis["selected_policy"] == "classifying_quota",
       "soft PT analysis must select classifying quota")
assert(soft_pt_analysis.dig("batch_completion", "completed_in_all_runs") == true,
       "soft PT analysis must preserve Batch completion evidence")

manifest_files = manifest.fetch("files")
expected_manifest_files = (ALLOWED_FILES - ["manifest.json"]).sort
assert(manifest_files.keys.sort == expected_manifest_files,
       "manifest file list differs from package allowlist")
manifest_files.each do |relative, expected_sha|
  actual_sha = Digest::SHA256.file(PACKAGE.join(relative)).hexdigest
  assert(actual_sha == expected_sha, "SHA-256 mismatch for #{relative}")
end

puts "RHAII 3.5 public package validation passed (#{files.length} allowlisted files)."
