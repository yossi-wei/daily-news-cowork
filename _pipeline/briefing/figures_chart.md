
## Figures — optional, and usually the answer is no

Your beat may attach **one** small chart to a candidate when the story is genuinely
about a series of numbers. **Most days you should attach none.** A digest where every
story carries a chart signals nothing; scarcity is what makes a figure worth looking at.

Attach a chart only when **all four** of these hold. If any one fails, set
`"figure": null` and move on — a missing figure costs nothing, a bad one costs the
reader's trust in every other figure.

1. **The figure IS the news.** You are plotting the data that was released, the
   measurement that was taken, the result that was published. Not a number mentioned
   in passing, not context you assembled yourself to illustrate a point.
2. **It carries what your paragraphs cannot.** A shape over time, a distribution, a
   comparison across enough values that prose would become a list. Ask which sentence
   in your `body` becomes better supported by it. If the honest answer is "it shows
   what I already wrote," it fails. A chart of two or three numbers is a sentence.
3. **Every number traces to a source you already cite.** `series_source` must be one
   of the URLs in your own `urls` array. This is checked in code and a mismatch drops
   the figure silently, so do not point it anywhere else.
4. **It reads at a glance.** 4–24 points, labels under 18 characters.

### Never attach a chart when

- Any value is one you estimated, interpolated, back-filled, unit-converted, or read
  off a graph by eye. **Every point must appear as a number in the cited source.** A
  fabricated point is worse than no chart, because a chart reads as authoritative.
- The series is flat, or the numbers are all the same.
- The source is a single interested party — a company's own benchmark, a belligerent's
  own tally — *unless* you say so in the `caption` itself ("per the company's own
  figures"). The claim-level sourcing rule above applies to figures too, and burying
  the attribution in `confidence` is not enough when the chart is what gets looked at.
- You are reaching for a chart because the story feels like it should have one.

### Shape

Add a `figure` object to a candidate (omit it, or use `null`, on every other candidate):

```json
"figure": {
  "kind": "chart",
  "chart_type": "bar | line",
  "series": [{"label": "Q1 2026", "value": 12.4}],
  "series_unit": "billion USD",
  "series_source": "https://… one of this candidate's own urls",
  "alt": "one sentence describing what the chart shows",
  "caption": "what the reader should notice, max 20 words",
  "why_it_qualifies": "which of the four tests this clears, and how"
}
```

- `chart_type`: `bar` for comparing categories, `line` for a trend over ordered time.
- `label`: short — it is rendered in a narrow column. "Q1 2026", "Jan", "Germany".
- `value`: a plain number. No units, no separators, no currency symbols, no strings.
- `series_unit`: the unit, written once (`"billion USD"`, `"qubits"`, `"%"`, `"ms"`).
- `why_it_qualifies` is required and is never shown to the reader. It exists so the
  decision can be audited later. If you cannot state plainly which test the figure
  clears, that is the answer: it does not qualify.
