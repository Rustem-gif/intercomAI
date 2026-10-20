# Where QA's own verdicts disagree — questions for QA

The AI can only be as consistent as the answer key. These are the cases where QA failed one
chat and passed another of the same kind, or passed a chat that the escalation list QA gave us
says should fail. Each row needs one decision from QA; the AI then follows it. Chat ids are
Intercom conversation ids; "QA" is the score QA gave on re-score.

Sources: the 157 re-scores of kb-v41 grades (6–20 Oct 2026), the 1,391 re-scores under the
previous ruleset (Jul–Oct), and the 8 Oct calibration feedback.

## 1. A request to close, block or self-exclude the account that is not visibly carried out

| QA failed | QA passed |
|---|---|
| `215561189196770` — "Close my account as i no longer want it open"; QA: *missing escalation* (75) | `215561184842420` — permanent self-exclusion, block on re-registration and a marketing stop asked; the agent confirmed a plain closure only; QA: *no mistakes* (100) |
| `215561195031197` — QA: *important to inform the player that SE limits were set* (75) | `215561192205083` — "Close my account permanently please"; the agent said the VIP manager will contact them; QA: *responses felt robotic* (89) |
| | `215561199807690` — closure request neither carried out nor forwarded; QA: *no mistakes* (100) |

**Now:** `cannot_determine` → manual review. **Decision needed:** is an un-actioned closure /
self-exclusion request a Gate 2A failure (action-escalation-missed), or is a handover to the
VIP manager / a plain closure enough?

## 2. An escalation told to the player, with no internal note

| QA failed | QA passed |
|---|---|
| `215561184443114` — "I have sent your request to the payment department"; QA: *I don't see the escalation to the payment department* (75) | `215561197954391` — "your VIP manager will discuss it with you", no note; QA 97 (*tag missing* only) |
| `215561202895250` — a vague "forwarded to our specialists" repeated to a player asking for the status; QA 72 | |

**Now:** `cannot_determine` → manual review (the AI cannot see Slack or the back office).
QA's planned mandatory escalation note in the chat settles this: with the note, pass; without
it, fail.

## 3. Withdrawal pending / rejected / status unclear → Finance (escalation case 3 on QA's list)

QA passed all three, although none was passed to Finance:

- `215561198888771` — withdrawal unpaid for a year; the agent: "the support department has no authority to advise you on matters like this", write to email. QA: *no mistakes* (100).
- `215561190354551` — withdrawal blocked; the agent: "the tech team is currently working to fix the issue". QA: *no mistakes* (100).
- `215561186471927` — withdrawal rejected; the agent explained verification, deposit and the 3× wager. QA: *sla* only (95).

**Decision needed:** when is a withdrawal case *not* a Finance escalation — a redirect to the
email channel, a known outage, an explanation of the rejection reason? Case 3 needs that
carve-out in writing, or the AI keeps failing chats QA passes.

## 4. A game error with money at stake → Tech (case 8), or a complaint the KB cannot settle → supervisor (case 9)

- `215561201129424` — a win credited short, the player: "how can a person prove that they got scammed"; the agent denied the problem and pointed to email. QA: *all good!* (100).

**Decision needed:** does case 8/9 apply to a disputed win the agent cannot see, or is a denial
plus the email route correct handling?

## 5. A demand for a manager after a refusal by the terms (case 1)

| QA failed | QA passed |
|---|---|
| `215561080455543` (№ 132 of the 180 sample) — "Vip manager request … are there any free spins"; the agent answered about free spins and closed; QA: *75 / FAIL, mandatory escalation missed* | `215561190474521` — deposit below the minimum, refused by the terms; the player demanded management; QA: *no mistakes* (100) |

**Decision needed:** does a demand for a manager that follows a correct refusal by the terms
still have to be passed on? Case 1 as written says yes.

## 6. The duplicate-account rule as the answer to a bonus question

| QA failed | QA passed |
|---|---|
| `215561192220655` — duplicate account, free spins asked; QA: *the free spins could still have been granted* (75) | `215561188755910` — "Do I get my deposit bonus now?" answered with "do not make duplicate accounts"; QA: *no mistakes* (100) |

**Decision needed:** is "you have a duplicate account" a complete answer to a bonus question?
(Internal bonus policy may decide this; the AI cannot see it.)

## 7. A question skipped while the main request was handled, player went silent

| QA failed (resp-no-ghost kept) | QA passed (info-completeness or nothing) |
|---|---|
| `215561142097586` (74) — *ignored the player's question about the reg bonus*; `215561160954258` (73) | `215561172301058` (92) — *no answer to player's last questions*; `215561146131802` (88) — *reg bonus was ignored* |
| `215561053744978` (64), `215561069792702` (70) — resp-no-ghost kept as a failed criterion | `215561191720078` (95) — *the question about Free Spins was left unanswered*; `215561184478348` (92); `215561188849783` (100) |

**Now:** a skipped secondary request the player did not repeat is info-completeness (−5); a
question in the player's last messages is resp-no-ghost. **Decision needed:** confirm that line,
or say when an ignored question is a Gate 2A failure.

## 8. Churn: what counts as "leaving"

- `215561187062279` — "I am NEVER coming back to this site" inside a complaint about a delay; QA: churn n/a, *tone is very dry* (91).
- History: `215561149030106` — *the player's reply about leaving the casino was not acknowledged*, scored 90; `215561106973718` — *no attempt to de-escalate the conflict or reassure the player*, 82.

The 9 Oct definition QA agreed to counts "I'm leaving / close my account / I'll play elsewhere"
as a churn signal that must get a reaction (Gate 2A). **Decision needed:** keep that, or
narrow it to a request to close the account / a stated move to another casino.

## 9. The inactivity close without a check-in

- `215561197712175` — the agent asked a question and closed 3m46s later with no check-in; QA: *no clarifying question before closing* (75, FAIL).
- History: 15 comments "no clarifying question before closing" scored 66–97 — sometimes a FAIL, sometimes −3.

**Decision needed:** is closing without a check-in a Gate 2A failure (res-no-fake-close) or a
small deduction?

## 10. Small deductions applied unevenly (history, old ruleset)

- **Missed SLA:** noted but scored 100 in `215561134947257`, `215561134975050`, `215561134945329`, `215561129571908`, `215561126242263`; −5 in `215561143423236`, `215561136991308`.
- **Missing tag:** 0 points in `215561110251320`, `215561126279157` (31 Aug–2 Sep); −2/−3 elsewhere.
- **Telling a VIP player to write to their VIP manager:** `215561173517204` scored 84 (*should not have simply advised the player to contact their VIP Manager*); the same advice was passed at 100 in other chats (open question: list them with QA).

The AI applies fixed deductions (resp-sla −5, tag-chat −3, ownership-effort −6), so it will
disagree with whichever side QA takes case by case. Not a Gate 2A matter; it moves scores, not
PASS/FAIL, unless the chat is near 85.

## 11. Leniency the AI cannot know about (not a contradiction, but a rule only QA can apply)

- The 15 Oct scam-deposit shift: `215561197770412`, `215561197771902`, `215561197772272`, `215561197772525`, `215561197773452` — all passed "considering the amount of similar scam chats during this shift".
- The power outage: `215561196729613`, `215561196735896` — "I won't deduct points, as I'm aware that there was a power outage". The AI now routes an agent-side outage note to manual review.
- 2 Sep (agent Felix): `215561126258456` — the player waited with 4 messages; the agent closed 11 s after "no access to your profile due to technical difficulties"; QA: *no errors as the chat is empty, missed sla, wrong tag* (92).

**Proposal:** mark shift incidents (date, shift, reason) somewhere the pipeline can read — a tag
on the chats, or a list — and exclude those chats from the agreement measurement.
