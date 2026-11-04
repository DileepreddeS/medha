"""Isolated verifier. Only the gate may touch holdout tests and the judge."""

# NOTE: intentionally empty. Importing medha.verify must not pull in the
# firewalled modules (holdout, judge) as a side effect.
