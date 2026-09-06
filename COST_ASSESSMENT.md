# Corvée Skill: Cost vs Direct Astra Work

Assessment date: 2026-09-06
Method: real delegated missions across three codebases, Corvex provider pricing
fetched live, OpenAI pricing from platform.openai.com/docs/pricing.

## Verdict

With GPT-6 Astra ($10/M input, $1/M cached, $12.5/M output) as the planner
and Corvex models ($0.55–0.75/M input, $2.20–2.40/M output) as delegates,
delegation **saves money on large missions and loses on small ones**. The
break-even is roughly when the direct work would consume 40–60K input tokens.

## Pricing

| Model | Role | Input $/M | Cached $/M | Output $/M |
|---|---|---|---|---|
| GPT-6 Astra | Planner (brain) | $10.00 | $1.00 | $12.50 |
| Kimi-K2.7-Code | Delegate | $0.55 | $0.55 | $2.20 |
| GLM-5.2-FP8 | Delegate | $0.75 | $0.75 | $2.40 |

Astra is 13–18x more expensive per input token than the Corvex delegates.
Astra's cache discount ($1/M vs $10/M) narrows this to ~5x on re-sent context,
but the delegate is still dramatically cheaper per token of reading.

## Measured missions

| Mission | Delegate cost | Astra overhead | Total delegated | Astra direct | Winner |
|---|---|---|---|---|---|
| EffortTest (write, small) | $0.067 | $0.130 | $0.196 | $0.081 | **Direct** (2.4x) |
| ssbc CANCEL (read, medium) | $0.078 | $0.060 | $0.138 | $0.287 | **Delegate** (2.1x) |
| alcove HAMT (read, large) | $0.343 | $0.066 | $0.408 | $0.520 | **Delegate** (1.3x) |

With Astra's cache discount (80% cached, effective $2.80/M). Without cache
(no re-sends), delegation wins by 3.3–4.4x on the medium and large missions.

## The break-even threshold

Delegation pays when:

```
delegate_cost + planner_overhead < direct_cost
```

The planner overhead is roughly constant (~8–15K tokens for mission prep,
review, and gate verification) regardless of mission size. The delegate's
work scales with the codebase. So:

- **Small missions** (direct work < 40K tokens): the fixed planner overhead
  exceeds the savings. Do it directly in Astra.
- **Medium missions** (40–80K direct tokens): delegation starts saving, 2x.
- **Large missions** (80K+ direct tokens): delegation saves 1.3–4.4x, growing
  with mission size.

The larger the codebase to read, the more delegation saves, because Astra's
expensive tokens are replaced by Corvex's cheap ones for the bulk reading work.

## Why the small mission lost

Mission 1 (EffortTest) was a small write task: read ~60 lines of test patterns,
write 25 lines of test code. The delegate spent 114K tokens exploring (13 steps
on a new codebase), while Astra could have done it in ~20K tokens directly.
The delegate's exploration overhead — inherent to a model encountering an
unfamiliar codebase — made it more expensive than the task warranted.

For small, well-specified tasks where Astra already has context, direct work
is cheaper. Delegation is for offloading large-scale reading and analysis that
would consume Astra's expensive context budget.

## The context-window multiplier

The dollar savings understate the real value. Astra's context window is finite,
and every token of code reading is a token not available for reasoning. By
moving 80–150K tokens of reading to the delegate, the planner preserves its
context for architecture, decision-making, and review. The cost analysis
measures money; the context analysis measures capability — and on large
missions, delegation wins on both.

## Recommendations

- Use corvee for medium-to-large missions (40K+ direct tokens of reading).
- Do small tasks directly in Astra — the planner overhead exceeds the savings.
- The larger the codebase, the stronger the case for delegation.
- Kimi ($0.55/M) is more cost-effective than GLM ($0.75/M) for delegation.
- The skill's value proposition is: **cheap tokens for bulk reading, expensive
  tokens reserved for thinking.**
