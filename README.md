# Jev ticket triage test

A small experiment to see whether [Jev](https://typesafe.ai), TypeSafe AI's "System One" model, can route customer support tickets accurately, quickly and cheaply, and whether its confidence scores can be trusted.

Jev doesn't write text. You send it some content plus typed questions, and it returns a decision with a probability for each option and a confidence score. It has three question types:

- **Choice:** pick one option from a list
- **Score:** rate on an ordered scale
- **Noul:** the probability that a yes/no statement is true

## What we tested

We wrote 100 synthetic support tickets and labelled each with the answer we considered correct:

| Team | Tickets |
|---|---|
| billing | 22 |
| bug | 24 |
| account | 20 |
| feature_request | 18 |
| spam | 16 |

Each ticket also has an urgency label from 1 to 5, and 10 are marked as the customer threatening to cancel. A few tickets are deliberately ambiguous or tricky: phishing disguised as an invoice, a fake "URGENT" domain-renewal scam, and a broken invoice link that could be billing or a bug.

Each ticket was sent to Jev once, with three questions in the same request:

| Question | Type | Asks |
|---|---|---|
| `team` | Choice | Which support team should handle this ticket? |
| `urgency` | Score | How urgent is it, from 1 (low) to 5 (critical)? |
| `churn_risk` | Noul | Is the customer threatening to cancel, leave or switch to a competitor? |

## Results

Run on 27 September 2026 against `jev-1.13.0`, with 100 of 100 tickets answered.

| Measure | Result |
|---|---|
| Team routing | **98 / 100** matched our label |
| Urgency | 90% within ±1 of our label, 56% exact |
| Churn threat | 98 / 100 correct: caught 8 of 10 real threats, no false alarms |
| Response time | 304 ms median, 430 ms for 90% of requests, 4.2 s for all 100 (8 in parallel) |
| Cost | $0.0025 for the run (about 600 input tokens per ticket), about $0.025 per 1,000 tickets |

### Findings

**1. The confidence score is reliable enough to automate on.** Every ticket where Jev's team confidence was 0.9 or higher matched our label: 90 of 90. Both disagreements came at 0.80–0.83.

| Rule | Routed automatically | Accuracy on those |
|---|---|---|
| Accept everything | 100 | 98% |
| Confidence ≥ 0.8 | 94 | 98% |
| Confidence ≥ 0.9 | 90 | **100%** |

With a 0.9 threshold, Jev would route 90% of tickets on its own with no errors and send the other 10 to a person.

**2. Both routing disagreements were on genuinely ambiguous tickets.**

| Ticket | Our label | Jev | Why it's ambiguous |
|---|---|---|---|
| T016: "The coupon code SPRING24 … says 'invalid' at checkout" | billing | bug (84%, billing 16%) | Billing if the coupon is expired or restricted; a bug if checkout is broken |
| T082: "Please let admins restrict who can create new projects" | feature_request | account (87%) | A new feature, or an existing permissions setting |

We kept our original labels rather than changing them to match Jev, which would have made the test meaningless. Neither label is clearly wrong. The fair reading is 98 of 100, with both disagreements on tickets that could reasonably go either way. Under the 0.9 rule, both would have gone to a person.

**3. Urgency is the weakest answer.** On average Jev rated tickets 0.4 points more urgent than we did. The largest misses were scam emails written to sound urgent: "account suspended in 24 hours" and "domain expires TODAY" scored about 3 out of 5. Jev did put those tickets in spam; the urgency question is asked separately and doesn't know that. The fix belongs in code (spam always gets urgency 1), not in the model.

**4. Indirect churn threats were missed.** Jev caught every explicit threat, such as "we'll look elsewhere", "moving to a competitor" and "cancel everything". It missed two indirect ones:
- T050, a GDPR request to delete the account: 16%
- T071, "we can't renew without audit logs": 40%

Rewording the question to cover signs that a customer is leaving, not only explicit threats, would likely catch these.

### Caveats

- **Clean test data.** 100 tickets we wrote ourselves are cleaner and more clear-cut than a real support inbox. Results on real tickets will likely be lower.
- **One labeller.** The correct answers are one person's judgement, and borderline tickets have no single right answer.
- **Speed includes the network.** TypeSafe advertises about 0.11 s per task. Our 0.3 s median includes the round trip from a laptop to the API, so the two aren't directly comparable.
- **One run.** We didn't test whether repeated runs give the same answers.

## Running it yourself

Requires Python 3.9 or later. It uses only the standard library, so there's nothing to install.

1. Get an API key from [console.typesafe.ai/keys](https://console.typesafe.ai/keys).
2. Copy the template and paste your key into it:
   ```bash
   cp .env.example .env        # then edit .env: TYPESAFE_API_KEY=your-key
   ```
   A `TYPESAFE_API_KEY` already set in your shell takes priority over `.env`.
3. Run it:
   ```bash
   python3 triage.py --limit 5    # quick check on 5 tickets
   python3 triage.py              # all 100 tickets
   open report.html
   ```

Options:
- `--mock`: fake answers so you can preview the report without a key. The report shows a "SIMULATED DATA" banner.
- `--workers N`: number of requests in parallel (default 8).
- `--limit N`: only run the first N tickets.

## The report

`report.html` is a single self-contained file:

- **Summary tiles:** accuracy for each question, response time and cost.
- **Confidence routing:** a threshold slider showing how many tickets would be routed automatically and how accurate those would be. Beside it, a chart of accuracy for each confidence range.
- **Confusion matrix:** our label against Jev's pick, showing which teams get mixed up.
- **Ticket table:** one row per ticket with Jev's probability for each team, its urgency spread and its churn probability. You can filter to disagreements, tickets below the threshold or churn flags, and click a row for the exact numbers.

In the table, **Expected** is our label and **Jev pick** is Jev's answer. A ✗ means Jev's answer doesn't match our label, not that Jev rejected that option.

## Files

| File | Purpose |
|---|---|
| `tickets.json` | The 100 labelled synthetic tickets |
| `triage.py` | Sends tickets to Jev and writes `results.json` and `report.html` |
| `report_template.html` | Report layout; the script fills in the data |
| `results.json` | Raw Jev responses from the last run (git-ignored) |
| `report.html` | Generated report (git-ignored) |
| `.env.example` | Template for the API key file; the real `.env` is git-ignored |

## Possible next steps

- **Test on real tickets:** run a sample of real support tickets to see how Jev handles messy input.
- **Accept more than one answer:** let ambiguous tickets count more than one team as correct, and flag them in the report.
- **Compare with an LLM:** run the same tickets through an LLM such as Claude to compare accuracy, speed and cost. Also test a hybrid: Jev handles tickets at 0.9 confidence or higher and the LLM handles the rest.
- **Check consistency:** run the set several times to see whether answers and confidence stay the same.
