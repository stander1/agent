# v5.14z Active Fact and Terminal Delivery Formal Regression

This formal regression returns the v5.14y mechanism to the unchanged real
MiMo Provider experiment:

- A1-A10 and B1-B10 frozen sequential tasks;
- Native, Observed, and Managed groups;
- unchanged capability-based Agent configurations;
- temperature 0, at most nine turns, and the same anonymous quality judges;
- unchanged Provider-token, collaboration-token, delivery, quality, memory,
  review-governance, structured-state, and protocol-hygiene thresholds.

The audit retains every v5.14x gate and additionally binds the run to the
v5.14y mechanism acceptance. The regression focus covers the v5.14x managed
delivery failures, wrong-memory hits, terminal artifact resolution, and
required-evidence fallback quality.

`start_background.sh` reads the Provider key from standard input (or a hidden
interactive prompt), keeps it out of the command line and logs, and returns the
experiment ID, PID, and log path after a detached launch.
