# V1 UI Design

## Design Principles

- Evidence before opinion: every RAG claim leads to a visible source.
- One primary task per page: scan trends, inspect a game, or ask a question.
- Explain uncertainty: show empty, loading, failed, and insufficient-evidence states explicitly.
- Use accessible, responsive UI; never rely on colour alone to convey sentiment.

## Information Architecture

| Route | Purpose |
| --- | --- |
| `/` | Dashboard: overall news intelligence overview |
| `/games/:slug` | Game detail: trends, sentiment, latest articles |
| `/news` | Searchable, filterable article explorer |
| `/news/:id` | Article detail and original source link |
| `/ask` | Evidence-grounded RAG analysis |

## Shared Application Shell

Desktop: left navigation with Dashboard, Games, News, Ask; persistent top bar with global date range. Mobile: compact header and collapsible navigation. Main content must remain readable at 320px width.

Global filters apply only where explicitly indicated. Page-specific filters must be visible and must not silently modify RAG results.

## Dashboard

1. Summary row: today’s articles, selected-period volume, negative ratio.
2. Volume trend: daily article counts.
3. Negative trend: daily negative article counts and rate.
4. Popular games: ranked by selected-period article count.
5. Latest news: title, game tags, sentiment badge, source, date.

Charts require labelled axes, visible date range, keyboard-readable data table alternative, and tooltips. Positive, neutral, and negative must additionally use text labels or distinct icons/patterns.

## Game Detail

- Header: game name, aliases, selected period, total article count.
- Sentiment distribution and time trend.
- Recent events: V1 labels, not automatic event clusters.
- News table with source, date, topic relevance, sentiment, and original-link action.
- Empty state explains that no indexed articles match the active filters.

## Ask

Input includes a question, optional game selector, optional date range, and submit action. The answer view contains:

1. Answer text with inline citation markers.
2. Claim cards with linked citation IDs.
3. Source list showing title, publisher, date, excerpt, and external URL.
4. Query and retrieval disclosure: topic, date range, hybrid retrieval, source count.
5. Evidence warning when `insufficient_evidence` is true.

Never display raw model chain-of-thought. Show only concise, user-facing retrieval metadata.

## Component and State Rules

- Loading: use skeletons for known layouts; disable duplicate submit actions.
- Error: show a clear message and retry action; retain user filters and question text.
- Empty: distinguish no data, no matching filters, and insufficient RAG evidence.
- External links: open with `target="_blank"` and `rel="noreferrer"`.
- Dates: display in the user locale but send UTC ISO 8601 values to the API.

## Accessibility and Quality

Meet WCAG 2.1 AA contrast requirements. Use semantic headings, buttons, labels, native focus order, visible focus styles, keyboard-operable filters, and `aria-live="polite"` for RAG status updates. Run Lighthouse accessibility checks before release.