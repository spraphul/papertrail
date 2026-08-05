# Research Group “View All” Interaction

Date: 2026-08-04
Status: Approved design

## Problem

PaperTrail’s organization API already returns every paper assigned to each problem
neighborhood. The dashboard overview intentionally previews three papers, while the dedicated
Research Groups route currently renders only the first eight. Because that route has no expansion
control, remaining members are inaccessible from the UI even though they are present in the
response.

## Design

The dashboard overview remains unchanged: it previews up to three papers per neighborhood and
links to Research Groups.

On the Research Groups route, each neighborhood initially renders up to eight papers. When a
group has more members, the card shows a button beneath the preview:

```text
View all 27 papers
```

Selecting it expands only that card and renders every paper already present in `group.papers`.
The control changes to `Show less`, which collapses the card back to eight members. Groups with
eight or fewer papers have no expansion control.

Expansion is client-side and does not make another API request. The existing response remains the
source of truth, so the displayed total must equal `group.paper_count` and the expanded list must
preserve the API’s membership order.

## Interaction details

- Expansion state is keyed by stable `cluster_id`, allowing several groups to be expanded at once.
- Re-rendering after starring a paper preserves the current expansion state.
- Navigating away and returning during the same page session preserves expansion state; a full
  browser reload resets groups to their compact form.
- The button uses a native `button` element with `aria-expanded` and an `aria-controls` target.
- Every revealed member retains its paper-reader link, canonical source link, new-paper badge, and
  favourite control.
- The control displays the total membership count rather than the number hidden, making it clear
  that it reveals the complete neighborhood.

## Failure and scale behavior

If a group reports a larger `paper_count` than the number of returned members, the control uses
the returned member count and does not claim inaccessible papers are available. Normal PaperTrail
responses return complete membership, so this is defensive behavior for malformed or older API
payloads.

Papers beyond the first eight are not inserted into the DOM until expansion. This keeps the
initial Research Groups render compact even for large snapshots.

## Verification

Automated tests cover groups below, at, and above the eight-paper threshold; button labels and
ARIA state; complete expansion in API order; collapse; and expansion-state preservation after a
favourite update.

Browser verification uses a representative large neighborhood and confirms that:

1. the initial card shows eight papers and `View all N papers`;
2. clicking reveals all N members and changes the control to `Show less`;
3. revealed paper and favourite actions remain interactive;
4. collapsing returns to eight members;
5. no console errors or application overlay appear.
