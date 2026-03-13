# GitHub Issue Drafts

Open backlog from `fix_plan.md`, trimmed into short GitHub-ready drafts.

## 1. Audit milestone deliverables
Labels: planning, documentation, high priority

Compare the current repo against milestone requirements and identify anything missing.

Done when:
- each deliverable is marked complete, partial, or missing
- evidence is mapped to the repo
- follow-up gaps are added back to the backlog

## 2. Write milestone report
Labels: documentation, high priority

Write the milestone report using current features, tests, and known limits.

Done when:
- the report accurately reflects implemented work
- screenshots or test evidence are included where needed
- open risks are called out clearly

## 3. Add train delay handling
Labels: backend, rail, medium priority

Expose rail delay status and expected departure times consistently.

Done when:
- delayed and on-time services are distinguishable
- the response shape is usable by the frontend
- tests cover delayed and normal cases

## 4. Add timetable filtering by time and service
Labels: backend, frontend, medium priority

Allow timetable and route results to be filtered by service and time window.

Done when:
- users can filter by time and service
- defaults still work when no filter is given
- tests cover filtered and unfiltered behavior

## 5. Select origin and destination from the map
Labels: frontend, map, ux, medium priority

Let users tap a stop on the map and set it as origin or destination.

Done when:
- a tapped stop can be assigned to either field
- the current search form updates correctly
- typed search still works unchanged

## 6. Show estimated arrival times at stations
Labels: frontend, backend, rail, medium priority

Display live or estimated station arrival times where available.

Done when:
- estimated times appear in the relevant views
- fallback behavior is clear when live data is missing
- tests cover both states

## 7. Add recent and frequent routes feature
Labels: frontend, product, medium priority

Surface recent or frequent route searches so users can rerun them quickly.

Done when:
- suggestions can be reused from the UI
- desktop and mobile layouts both support the feature
- the journey planner flow stays intact

## 8. Refine desktop and mobile layout
Labels: frontend, ux, responsive, medium priority

Clean up spacing, overflow, and control layout across major frontend views.

Done when:
- core pages work cleanly on desktop and mobile
- obvious layout regressions are removed
- accessibility regressions are avoided

## 9. Implement GET /bus/times/{stopCode}
Labels: backend, api, bus, medium priority

Add the missing bus times endpoint used by the frontend.

Done when:
- the endpoint no longer returns 404
- the response matches frontend expectations
- tests cover valid, empty, and invalid stop codes

## 10. Add road-following bus route geometry
Labels: backend, frontend, map, osrm, medium priority

Replace straight stop-to-stop lines with road-following geometry where available.

Done when:
- rendered lines follow roads instead of direct segments
- fallback behavior exists when geometry is unavailable
- performance remains acceptable on the map

## 11. Add frontend tests for map route modules
Labels: frontend, tests, medium priority

Add or update tests for `BusStopLayer`, `RouteLineLayer`, `useRouteLine`, and `routeLineApi`.

Done when:
- toggle and fallback behavior are covered
- data mapping assumptions are tested
- the tests pass reliably

## 12. Add backend tests for GET /routes/line/{line}
Labels: backend, tests, medium priority

Test route-line responses, stop ordering, and error handling.

Done when:
- valid and missing line cases are covered
- ordering and variant assumptions are verified
- the suite passes locally and in CI

## 13. Harden production backend config
Labels: backend, security, devops, low priority

Close obvious deployment gaps around config, auth, rate limits, and public API safety.

Done when:
- production settings are env-driven
- auth or access controls are defined
- rate limiting or equivalent protection is in place

## 14. Add analytics endpoint for frequent routes
Labels: backend, analytics, low priority

Track successful journey planning requests and expose top recent or frequent routes.

Done when:
- successful planning requests are logged
- an analytics endpoint returns useful aggregated data
- the response is stable enough for frontend use

## 15. Finish developer setup docs and compose flow
Labels: documentation, devops, low priority

Complete developer setup docs and ensure the compose-based dev workflow is accurate.

Done when:
- the stack can be started from the documented flow
- compose coverage matches backend, frontend, and OSRM needs
- required environment assumptions are documented
