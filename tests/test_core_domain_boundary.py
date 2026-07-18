from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class CoreDomainBoundaryTest(unittest.TestCase):
    def test_agent_runtime_contains_no_question_specific_markers(self) -> None:
        forbidden = (
            "莫干山",
            "三个候选项",
            "首日十点后出发",
            "travel_preference",
            "travel_destination_decision",
            "security_audit",
            "AGENTLITE_DELIVERY_POLICY",
            "experiments.profiles",
            'task_id.startswith("A")',
            'task_id.startswith("B")',
        )
        violations: list[str] = []
        for path in (ROOT / "agent_runtime").rglob("*.py"):
            content = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in content:
                    violations.append(f"{path.relative_to(ROOT)}: {marker}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
