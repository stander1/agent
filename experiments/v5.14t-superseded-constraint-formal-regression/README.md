# v5.14t Superseded Constraint Formal Regression

This Provider-backed formal regression reuses the exact v5.14r tasks, Agent
configurations, model, temperature, turn limit, judges, and acceptance
thresholds after the v5.14s rules-first constraint correction.

It adds two observations without changing runtime prompts:

- generated managed artifacts that contain superseded numeric constraints;
- managed tasks rejected by the numeric upper-bound guard.

At least one superseded constraint must be observed and no false rejection is
allowed. All inherited cost, quality, delivery, memory, state, and protocol
gates remain active.
