# Research Group “View All” Implementation Plan

Status: Superseded by the dedicated `#/groups/{cluster_id}` route documented in the unified
personalization implementation.

1. Add stable client-side expansion state keyed by `cluster_id` in
   `src/papertrail/web/app.js`.
2. Extend `groupCard` with an opt-in expandable mode while preserving the three-paper dashboard
   preview.
3. Render an accessible `View all N papers` / `Show less` button only for groups with more than
   eight returned members.
4. Bind expansion controls after every route render so favourite updates retain expanded state.
5. Add focused button styling in `src/papertrail/web/styles.css`.
6. Run the Python suite, Ruff, compilation, and whitespace checks.
7. Verify compact, expanded, and collapsed states in Chromium against the populated local
   dashboard; check paper actions and application errors.
8. Commit, push public `main`, and leave the updated dashboard running locally.
