# Services & Docs — changelog

Summary
- Updated frontend services and hooks to match backend API responses.
- Added normalization helpers in `transport-frontend/src/services/transportApi.js` and `liveUpdates.js`.
- Updated `useTransportData` hook and `home-page.jsx` adapters for backward compatibility.
- Added `routePlanner` adapter returning normalized planner shape used by hooks/components.
- Added developer artifacts: `docker-compose.yml`, Dockerfiles for `transport-backend` and `transport-frontend`, `.env.example`, and `DEV_SETUP.md`.

Details
- Frontend changes:
  - Normalize `/journey/plan` responses into `{ success, legs, _meta, routeGeometries }`.
  - Normalize live update messages (STOMP/WebSocket) into canonical `vehicle`/`alert` objects.
  - Prefer POST when sending lat/lon to `/api/bus_live` and fall back to GET when not available.
  - Hooks (`useTransportData`) consume normalized shapes and expose stable fields to components.

- Backend integration notes:
  - Backend endpoints inspected: `/health`, `/search/stops`, `/bus/live/{operator}`, `/journey/plan`.
  - `routeGeometries[*].coords` is used by the frontend to draw polylines.
  - OSRM integration is optional for walking fallback (compose includes a placeholder service).

How to create the branch, push, and open a PR locally

Run these commands from the repository root:

```powershell
# create and switch to a new branch
git checkout -b jj/update-services-docs

# stage all changes and new files
git add -A

# commit
git commit -m "chore: update frontend services/hooks and add dev docs (services_docs.md)"

# push branch to origin
git push -u origin jj/update-services-docs

# (optional) create a PR with GitHub CLI
gh pr create --title "Update frontend services & docs" --body "Changelog: services_docs.md" --base main
```

If `gh` is not installed, open a PR on GitHub by visiting:
`https://github.com/<owner>/<repo>/compare/main...jj/update-services-docs` and create the PR in the web UI.

Notes
- If push fails due to authentication, ensure your git credentials or SSH key are configured.
- I attempted to push and open a PR from the environment; if you'd like, I can try now and report the results.
