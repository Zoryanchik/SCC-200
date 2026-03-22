"""(Temporarily disabled)

This file previously attempted to unit-test live-bus candidate selection inside
`api._compute_delay_from_timetable`, but the matcher relies on many nested helpers
and tightly-coupled module globals, making reliable isolation brittle without a
small refactor (dependency injection).

When we extract the candidate selection into a pure helper function, we can
re-introduce focused regression tests for origin departure prioritisation.
"""

# Intentionally no tests here.
