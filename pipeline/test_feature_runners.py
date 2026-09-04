import argparse
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))

import benchmark
import pd_stage_sampler
import slo_scenario_runner


EXAMPLES = PIPELINE / "examples" / "rhaii35-feature-scenarios"


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return kwargs


class SloScenarioRunnerTests(unittest.TestCase):
    def test_slo_scenario_loads_in_canonical_runner(self):
        path = EXAMPLES / "slo-deadline-ordering.json"
        definitions, windows = benchmark.load_scenario_file(str(path))
        self.assertEqual(definitions[0][0], "same_flow_slo_deadline_ordering")
        self.assertEqual(len(definitions[0][1]), 3)
        self.assertEqual([item["name"] for item in windows[definitions[0][0]]], [
            "baseline", "overload", "recovery"
        ])

    def test_slo_extensions_are_validated(self):
        specs = slo_scenario_runner.load_tenant_specs(
            EXAMPLES / "slo-deadline-ordering.json"
        )
        self.assertEqual(specs["slo-class-250ms"]["slo_ttft_ms"], 250)
        self.assertIsNone(specs["slo-class-without-deadline"]["slo_ttft_ms"])
        self.assertEqual(
            slo_scenario_runner.effective_slo_ms(specs["slo-class-250ms"], "equal"),
            500,
        )

    def test_header_session_uses_shared_flow_and_slo_deadline(self):
        session = FakeSession()
        tenant = SimpleNamespace(
            flow_fairness_id="shared-flow",
            slo_ttft_ms=250,
        )
        result = slo_scenario_runner.HeaderSession(session, tenant).post(
            "http://example.test",
            headers={
                "x-llm-d-inference-fairness-id": "class-id",
                "x-llm-d-inference-objective": "priority-100",
            },
        )
        self.assertEqual(
            result["headers"]["x-llm-d-inference-fairness-id"], "shared-flow"
        )
        self.assertEqual(
            result["headers"]["x-llm-d-slo-ttft-ms"], "250"
        )
        self.assertEqual(
            result["headers"]["x-llm-d-inference-objective"], "priority-100"
        )
        self.assertNotIn("x-gateway-inference-fairness-id", result["headers"])
        self.assertNotIn("x-gateway-inference-objective", result["headers"])

    def test_header_session_omits_absent_deadline(self):
        session = FakeSession()
        tenant = SimpleNamespace(
            flow_fairness_id="shared-flow",
            slo_ttft_ms=None,
        )
        result = slo_scenario_runner.HeaderSession(session, tenant).post(
            "http://example.test",
            headers={"x-llm-d-slo-ttft-ms": "250"},
        )
        self.assertNotIn("x-llm-d-slo-ttft-ms", result["headers"])

    def test_header_evidence_uses_shared_flow_id(self):
        tenants = [
            SimpleNamespace(flow_fairness_id="shared-flow", priority=100),
            SimpleNamespace(flow_fairness_id="shared-flow", priority=100),
        ]
        metrics = (
            'llm_d_epp_flow_control_requests_total'
            '{fairness_id="shared-flow",priority="100"} 8\n'
        )
        report = slo_scenario_runner.shared_flow_header_evidence(
            benchmark, metrics, tenants
        )
        self.assertTrue(report["valid"])
        self.assertEqual(report["expected"], {"shared-flow": [100]})

    def test_class_ids_are_replaced_in_metric_filter(self):
        specs = slo_scenario_runner.load_tenant_specs(
            EXAMPLES / "slo-deadline-ordering.json"
        )
        self.assertEqual(
            slo_scenario_runner.flow_ids(specs, "fallback"),
            {"slo-ordering-shared-flow"},
        )

    def test_invalid_slo_deadline_fails_before_traffic(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "scenarios": [{
                    "tenants": [{
                        "fairness_id": "class-a",
                        "deadline_class": "invalid",
                        "slo_ttft_ms": 0,
                    }]
                }],
            }))
            with self.assertRaisesRegex(ValueError, "must be positive"):
                slo_scenario_runner.load_tenant_specs(path)

    def test_schedule_is_generated_once_for_the_same_seed(self):
        calls = []

        def schedule(_phases, _duration, seed):
            calls.append(seed)
            return [float(seed)]

        cached = slo_scenario_runner.build_schedule_cache(schedule)
        phases = [{"start_s": 0, "duration_s": 5, "rate_rps": 1}]
        self.assertEqual(cached(phases, 5, 42), [42.0])
        self.assertEqual(cached(phases, 5, 42), [42.0])
        self.assertEqual(calls, [42])


class PdScenarioTests(unittest.TestCase):
    def test_pd_scenarios_load_in_canonical_runner(self):
        paths = sorted(EXAMPLES.glob("pd-*.json"))
        self.assertEqual(len(paths), 4)
        for path in paths:
            with self.subTest(path=path.name):
                definitions, windows = benchmark.load_scenario_file(str(path))
                self.assertEqual(len(definitions), 1)
                self.assertEqual(len(definitions[0][1]), 2)
                self.assertEqual(
                    [item["name"] for item in windows[definitions[0][0]]],
                    ["dominant-solo", "contention", "drain"],
                )

    def test_metric_parser_selects_only_required_metrics(self):
        text = "\n".join([
            "# HELP ignored ignored",
            'vllm:num_requests_running{model_name="x"} 4',
            "unrelated_metric 9",
        ])
        rows = list(pd_stage_sampler.parse_rows(
            text, pd_stage_sampler.VLLM_METRICS
        ))
        self.assertEqual(rows, [
            ("vllm:num_requests_running", 'model_name="x"', 4.0)
        ])


class PdStageSamplerTests(unittest.IsolatedAsyncioTestCase):
    def args(self, root: Path) -> argparse.Namespace:
        return argparse.Namespace(
            prefill_url="http://prefill/metrics",
            decode_url="http://decode/metrics",
            endpoint_picker_url="http://endpoint-picker/metrics",
            token_file="",
            output=str(root / "pd-stage-metrics.csv"),
            interval=0.001,
            duration=0.003,
            stop_file="",
            request_timeout=1.0,
            insecure_https=False,
        )

    async def test_sampler_writes_valid_contract_for_all_three_sources(self):
        async def fake_fetch(_session, source, _url, _token, _insecure_https):
            if source == "endpoint-picker":
                return source, "llm_d_epp_flow_control_pool_saturation 0.95\n"
            return source, "vllm:num_requests_running 4\n"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(pd_stage_sampler, "fetch", side_effect=fake_fetch):
                status = await pd_stage_sampler.main_async(self.args(root))
            self.assertEqual(status, 0)
            contract = json.loads(
                (root / "pd-stage-metrics.csv.contract.json").read_text()
            )
            self.assertTrue(contract["valid"])
            self.assertGreater(contract["metricRows"], 0)
            self.assertTrue((root / "pd-stage-metrics.csv.errors.json").is_file())

    async def test_sampler_propagates_repeated_source_failure(self):
        async def fake_fetch(_session, source, _url, _token, _insecure_https):
            if source == "decode":
                raise RuntimeError("decode metrics unavailable")
            if source == "endpoint-picker":
                return source, "llm_d_epp_flow_control_pool_saturation 0.95\n"
            return source, "vllm:num_requests_running 4\n"

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(pd_stage_sampler, "fetch", side_effect=fake_fetch):
                status = await pd_stage_sampler.main_async(self.args(root))
            self.assertEqual(status, 1)
            contract = json.loads(
                (root / "pd-stage-metrics.csv.contract.json").read_text()
            )
            self.assertFalse(contract["valid"])
            self.assertGreaterEqual(
                contract["maxConsecutiveFailuresBySource"]["decode"], 2
            )


if __name__ == "__main__":
    unittest.main()
