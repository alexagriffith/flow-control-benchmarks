#!/usr/bin/env ruby
# frozen_string_literal: true

require "digest"
require "json"
require "pathname"
require "yaml"

ROOT = Pathname.new(__dir__).parent
PACKAGE = ROOT.join("benchmark-data/rhaii-3.5-flow-control")
README = PACKAGE.join("README.md")
RESULTS = PACKAGE.join("results.json")
ANALYSIS = PACKAGE.join("features/pd-flow-control/analysis.json")
RECIPE = PACKAGE.join("features/pd-flow-control/configuration/selected-recipe.yaml")
SOFT_PT_ANALYSIS = PACKAGE.join("features/soft-pt/analysis.json")
MANIFEST = PACKAGE.join("manifest.json")

ALLOWED_FILES = %w[
  README.md
  assets/capacity-slo-envelope.svg
  assets/slo-deadline-ordering.svg
  features/pd-flow-control/analysis.json
  features/pd-flow-control/configuration/selected-recipe.yaml
  features/soft-pt/analysis.json
  manifest.json
  results.json
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

assert(results.fetch("results").keys.sort == REQUIRED_RESULT_GROUPS.sort,
       "normalized result inventory differs from the reviewed promotion set")

assert(readme.scan(/(?:!\[[^\]]*\]\(|<img\s+[^>]*src=")[^)"]*\.svg/).length == 2,
       "README must embed exactly the two approved SVGs")
APPROVED_SVGS.each do |svg|
  assert(readme.include?(svg), "README does not embed approved visual #{svg}")
end

%w[Review\ outline planned\ artifact Planned\ public TODO TBD].each do |phrase|
  assert(!readme.match?(/#{Regexp.escape(phrase)}/i),
         "README contains unfinished publication language: #{phrase}")
end

link_targets = readme.scan(/\[[^\]]*\]\(([^)]+)\)/).flatten +
               readme.scan(/<img\s+[^>]*src="([^"]+)"/).flatten
link_targets.reject { |target| target.match?(%r{\A(?:https?://|#)}) }.each do |target|
  clean_target = target.split("#", 2).first
  assert(README.dirname.join(clean_target).exist?, "missing README target #{target}")
end

text_files = files.select { |path| path.end_with?(".md", ".json", ".yaml", ".svg") }
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
       "features/pd-flow-control/analysis.json", "results record has wrong P/D analysis link")
assert(results.dig("results", "pd_flow_control", "configuration") ==
       "features/pd-flow-control/configuration/selected-recipe.yaml",
       "results record has wrong P/D recipe link")

assert(results.dig("results", "soft_pt", "analysis") ==
       "features/soft-pt/analysis.json", "results record has wrong soft PT analysis link")
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
