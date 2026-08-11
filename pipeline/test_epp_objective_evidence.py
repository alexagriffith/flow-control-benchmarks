import json
import tempfile
import unittest
from pathlib import Path

import epp_objective_evidence as evidence


class EppObjectiveEvidenceTests(unittest.TestCase):
    def test_expected_pairs_reads_selected_scenario(self) -> None:
        payload = {
            "scenarios": [
                {
                    "name": "test",
                    "tenants": [
                        {"objective": "premium", "priority": 100},
                        {"objective": "batch", "priority": -10},
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scenarios.json"
            path.write_text(json.dumps(payload))
            self.assertEqual(
                evidence.expected_pairs(str(path), "test"),
                {("premium", 100), ("batch", -10)},
            )

    def test_analyze_requires_every_expected_pair(self) -> None:
        lines = "\n".join(
            [
                json.dumps({"msg": "LLM request assembled", "objectiveKey": "premium", "priority": 100}),
                json.dumps({"msg": "LLM request assembled", "objectiveKey": "batch", "priority": -10}),
            ]
        )
        report = evidence.analyze(lines, {("premium", 100), ("batch", -10)})
        self.assertTrue(report["valid"])
        self.assertEqual(len(report["observed"]), 2)

        missing = evidence.analyze(lines, {("premium", 100), ("standard", 0)})
        self.assertFalse(missing["valid"])
        self.assertEqual(missing["missing"], [{"objective": "standard", "priority": 0}])

    def test_analyze_rejects_wrong_priority_for_expected_objective(self) -> None:
        line = json.dumps(
            {"msg": "LLM request assembled", "objectiveKey": "premium", "priority": 0}
        )
        report = evidence.analyze(line, {("premium", 100)})
        self.assertFalse(report["valid"])
        self.assertEqual(report["mismatched"][0]["observed_priorities"], [0])


if __name__ == "__main__":
    unittest.main()
