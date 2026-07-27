# v5.14u Continuity, Identity, and Memory Resolution

This mechanism acceptance verifies three generic runtime corrections:

- current-task identity is inferred from the user task sequence, not only the
  current message's labels;
- explicit interaction ordinals are guarded without treating ordinary numeric
  counts as task identities;
- a compact reviewer approval resolves the immediately preceding valid
  artifact, preserves its explicit decisions, and promotes that artifact
  rather than the approval sentence into shared memory.

The chronology view selects semantic facts before truncation. It considers only
the latest two upstream messages and does not expose complete history to every
agent.

The acceptance is domain-neutral. It does not load Question A/B fixtures or
depend on planner, writer, or reviewer role names.
