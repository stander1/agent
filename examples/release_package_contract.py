from __future__ import annotations


PACKAGE_NAME = "multi-agent-collaboration-runtime"
DIST_INFO_PREFIX = "multi_agent_collaboration_runtime-"
REQUIRED_LICENSE_EXPRESSION = "Apache-2.0"
REQUIRED_LICENSE_FILE = "LICENSE"
REQUIRED_PROJECT_URLS = {
    "Source": "https://github.com/stander1/agent",
    "Issues": "https://github.com/stander1/agent/issues",
}
REQUIRED_CLASSIFIERS = {
    "Development Status :: 4 - Beta",
    "Programming Language :: Python :: 3",
    "Operating System :: OS Independent",
}

REQUIRED_WHEEL_MEMBERS = {
    "agent_runtime/__init__.py",
    "agent_runtime/cli.py",
    "agent_runtime/launcher.py",
    "agent_runtime/bootstrap/sitecustomize.py",
    "agent_runtime/bootstrap/startup.py",
    "agent_runtime/bridge/state_memory_bridge.py",
    "agent_runtime/core/kernel.py",
    "agent_runtime/core/models.py",
    "agent_runtime/core/runtime.py",
    "agent_runtime/drivers/autogen.py",
    "agent_runtime/drivers/autogen_codec.py",
    "agent_runtime/drivers/autogen_shp.py",
    "agent_runtime/memory/claim_extractor.py",
    "agent_runtime/memory/conflict_resolver.py",
    "agent_runtime/memory/memory_store.py",
    "agent_runtime/memory/schema_registry.py",
    "agent_runtime/memory/semantic_disambiguator.py",
    "agent_runtime/reliability/final_delivery_guard.py",
    "agent_runtime/reliability/typed_events.py",
    "agent_runtime/state/state_pool.py",
    "web_monitor/__init__.py",
    "web_monitor/parser.py",
    "web_monitor/server.py",
    "web_monitor/demo/index.html",
    "web_monitor/demo/agentlite-live.js",
}

REQUIRED_SDIST_MEMBERS = REQUIRED_WHEEL_MEMBERS | {
    REQUIRED_LICENSE_FILE,
    "README.md",
    "pyproject.toml",
    "MANIFEST.in",
    "docs/planning/version-implementation-mapping.md",
    "docs/planning/v5.15w-package-release-hardening.md",
    "docs/planning/v5.15x-release-candidate-freeze.md",
    "docs/planning/v5.15y-installed-sdist-delivery-readiness.md",
    "docs/planning/v0.5.15-final-release.md",
    "docs/competition/v0.5.15-delivery-guide.md",
    "docs/competition/v0.5.15rc2-delivery-guide.md",
    "docs/release/v0.5.15-final-release-notes.md",
    "docs/release/v5.15x-release-candidate-notes.md",
    "docs/release/v5.15y-release-candidate-notes.md",
    "examples/build_release_artifacts.py",
    "examples/release_package_contract.py",
    "examples/run_package_release_gate.py",
    "examples/run_release_gate.py",
    "examples/autogen_team_benchmark_app.py",
    "experiments/v5.15w-package-release-hardening/run_openeuler.sh",
    "experiments/v5.15x-release-candidate-freeze/README.md",
    "experiments/v5.15x-release-candidate-freeze/run_openeuler.sh",
    "experiments/v5.15x-release-candidate-freeze/verify_acceptance.py",
    "experiments/v5.15y-installed-sdist-delivery-readiness/README.md",
    "experiments/v5.15y-installed-sdist-delivery-readiness/run_openeuler.sh",
    "experiments/v5.15y-installed-sdist-delivery-readiness/verify_acceptance.py",
    "tests/test_launcher.py",
    "tests/test_runtime.py",
    "tests/test_release_gate_evidence.py",
    "tests/test_v515w_package_release_hardening_acceptance.py",
    "tests/test_v515x_release_candidate_freeze_acceptance.py",
    "tests/test_v515y_installed_sdist_delivery_readiness.py",
}

FORBIDDEN_DISTRIBUTION_PREFIXES = (
    ".agentlite-exp/",
    ".git/",
    ".venv/",
    "analysis/",
    "artifacts/",
    "build/",
    "dist/",
    "exports/",
    "logs/",
    "runs/",
)

APACHE_LICENSE_MARKERS = (
    "Apache License",
    "Version 2.0, January 2004",
    "TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION",
    "1. Definitions.",
    "9. Accepting Warranty or Additional Liability.",
    "END OF TERMS AND CONDITIONS",
    "APPENDIX: How to apply the Apache License to your work.",
)


def normalize_license_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n"


def apache_license_text_valid(text: str) -> bool:
    normalized = normalize_license_text(text)
    return all(marker in normalized for marker in APACHE_LICENSE_MARKERS)
