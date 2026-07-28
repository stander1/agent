# v5.15a Generic Semantic Bridge Acceptance

This is a deterministic mechanism gate. It does not run Question A/B and does
not use fixed Agent roles or an LLM Provider.

The gate requires an external JSON holdout authored after the implementation
commit. The holdout is copied into the evidence archive with its SHA256. This
keeps the distinction between implementation examples and post-implementation
evaluation explicit.

Required environment:

```bash
export AGENTLITE_V515A_HOLDOUT_FILE=/path/to/external-holdout.json
export AGENTLITE_V515A_EXP_ID="$(date +%Y%m%d-%H%M%S)"
bash experiments/v5.15a-generic-semantic-bridge-acceptance/run_openeuler.sh
```

The holdout schema is `agentlite.v515a.holdout.v1`:

```json
{
  "schema_version": "agentlite.v515a.holdout.v1",
  "holdout_id": "post-commit-unique-id",
  "authored_after_commit": "git-commit",
  "claim_cases": [
    {
      "case_id": "external-1",
      "text": "open_field: <= 7 unit",
      "subject": "external:one",
      "expected_claims": [
        {
          "predicate": "open_field",
          "operator": "le",
          "value": "7",
          "unit": "unit"
        }
      ]
    }
  ],
  "conflict_cases": [
    {
      "case_id": "external-conflict-1",
      "claims": [],
      "expected_status": "resolved",
      "expected_active_claim_ids": []
    }
  ]
}
```

The actual holdout should use predicates, units, domains, and actor names that
were not used to implement the bridge.

The holdout being authored after the implementation commit proves that its
cases were not visible during implementation. It is not described as an
independent third-party evaluation unless a separate evaluator actually
authors it.
