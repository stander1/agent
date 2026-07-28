# v5.15c Real Provider Semantic Preflight

This preflight exercises the optional v5.15b semantic proposal path against a
real OpenAI-compatible Provider. It is deliberately smaller than a formal
Native/Observed/Managed experiment.

The scenario file must remain outside the repository and be authored after the
implementation commit. It contains unrelated domains, expected canonical
semantics, and no Provider credentials. The preflight:

- sends only deterministic-extraction misses to the control model;
- records the complete control-model response and Provider usage without
  prompts, headers, or credentials;
- resolves exact source quotes locally;
- routes accepted candidates through normal schema, conflict, and memory
  admission;
- reports Provider, control, retry, communication, retrieval, and end-to-end
  costs without double counting;
- scores semantic fields and delivery/admission quality.

Required environment:

```bash
export OPENAI_BASE_URL='https://provider.example/v1'
export OPENAI_MODEL='model-name'
export OPENAI_API_KEY='provided outside the command line'
export AGENTLITE_V515C_SCENARIO_FILE=/external/path/scenarios.json
bash experiments/v5.15c-real-provider-semantic-preflight/run_openeuler.sh
```

Passing this preflight means the real control path is usable and fully costed
on the preregistered cross-domain cases. It does not establish formal
Native/Observed/Managed Token savings.
