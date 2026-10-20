# AI ↔ QA agreement — getting v4.1 PASS/FAIL to ≥ 95%

The client's bar: the AI's PASS/FAIL agrees with QA's on at least 95% of chats.

## Where it stood (prod, 2026-10-20)

- **Grades and overturns:** 1,750 kb-v41 grades, of which the AI failed 162. QA reviewed 146 of those 162 and overturned **130 to PASS**.
- **Direction:** human re-scores were higher on 135 of 151 chats, so the AI is too strict. False PASSes are not visible, because QA rarely opens an AI-PASS chat.
- **Population agreement:** at most **91.8%** (130 known disagreements out of 1,750).
- **Measurement gap:** overrides record only disagreements. A QA review that agreed left no trace.

## What changed

| Change | Where |
|---|---|
| QA-practice rules: what QA does not count as a failure (list below) | `rules/qa_system_prompt_kb_v41.txt` (= `qa/kbv41_prompt.py`) |
| Fail verifier: a second look at every grade heading for FAIL; an unproven Gate 2/3 fail becomes pass, and a critical is never lifted | `qa/fail_verifier.py`, `QA_FAIL_VERIFIER=on` |
| "Agree" button, so agreement is recorded and not only disagreement | grade panel; `grades.human_confirmed_*` |
| Population-weighted agreement on the Accuracy page | `qa/agreement.py`, `/api/accuracy` |
| Measurement tools | `scripts/accuracy_ab.py`, `scripts/agreement_report.py`, `scripts/pass_audit_sample.py` |

**Old chats are never re-graded.** The new rules and the verifier apply only to chats graded
after the merge; every existing grade keeps its score (PR #47, `GradesStore.is_current`). The live
agreement number therefore moves as new chats are graded and reviewed.

## Measured

**Method.** All runs were Sonnet 5.5 at high effort, in batch, on a copy of the prod DB; no grade was saved. The rules were written from the **dev** split only, and the 35-chat **holdout** was checked afterwards.

**Agreement with QA on the 151 QA-reviewed chats** (the hard set — 146 are chats the stored grade failed):

| Configuration | Dev (116) | Holdout (35) | All (151) |
|---|---|---|---|
| PR #43 rules (`d5a9f6c8c713`) | 42.2% | 42.9% | 42.4% |
| #43 rules + verifier (simulated) | 51.7% | 48.6% | 51.0% |
| QA-practice rules (`78889e130313`), run 1 | 69.8% | 65.7% | — |
| QA-practice rules + verifier, run 1 | 71.5% | 77.1% | — |
| QA-practice rules, run 2 | 64.7% | 60.0% | 63.6% |
| **QA-practice rules + verifier, run 2** | **70.7%** | **80.0%** | **72.9%** |

**Other measurements:**
- **Run-to-run stability (same result in both runs, 151 chats):** 90.7% from the rules alone, 94.7% with the verifier.
- **New FAILs among chats the stored grades passed** (80 random, sample `kb-v41-pass-audit`): 2 of 80 from the rules alone, both runs; with the verifier, 1 of 80 and then 0 of 80.
- **Cost:** a verified chat costs ~$0.013 extra (it reads the cached prompt), and only ~1 chat in 10 is verified.

**Projected population agreement** (`agreement_report.py`): **96.3–96.6%**. It is computed as:
- the FAIL side at 72–76% agreement, × 162 chats;
- the PASS side at 98.8% agreement, × 1,588 chats.

> ⚠️ The PASS side assumes QA agrees with the AI's PASSes. Nobody has checked that yet. If 2% of AI PASSes are wrong, the projection drops to about 94.5%. **QA reviewing the 80-chat audit sample is what turns the projection into a measured number.**

## For QA to confirm (these change how the manual is applied)

1. **Accuracy.** A difference between the agent's standard wording and the Help Center is not an accuracy failure. Examples:
   - "Crypto E-wallets up to 24 hours";
   - "withdrawals are approved within 24 hours";
   - "Support does not add bonuses in live chat";
   - bonus amounts.

   An accuracy failure needs the transcript itself to show the statement wrong. Help Center differences are flagged as `kb-discrepancy: …` so the Help Center can be updated. *(QA marked ~17 dev chats like this "no mistakes".)*
2. **severe-business-trust.** The standard "reviewed within up to 24 hours", hedged estimates ("likely", "should") and a status the agent reports seeing are not invented deadlines.
3. **resp-no-ghost.** A request counts as answered when the reply deals with it indirectly:
   - the reason it can't be done (duplicate account closed, verification first, not eligible, a T&C reference);
   - the step that must come first;
   - a handover to the VIP manager.

   A brief answer is info-completeness at most.
4. **Inactivity close.** The standard message "I see no reply from your side and I will close the chat for now", sent after the player stopped replying, is not a fake close.
5. **The verifier.** A second look may turn an unproven Gate 2/3 fail into a pass. A critical is never lifted; a disputed critical goes to manual review.

## Open questions for QA (the AI cannot know these from the chat)

- **Scam-deposit shift:** on one night shift (chats 215561197770412, 215561197771902, 215561197772272, 215561197772525, 215561197773452), QA passed agent closes without reply "considering the amount of similar scam chats". Should the AI recognise this pattern? That would need a rule for it.
- **Outside events:** power outages and similar events QA knows about.
- **Back-office facts:** e.g. "the agent claimed to close the duplicate account but didn't", or "South Africa is allowed".
- **Escalation claimed with no internal note:** QA failed some of these and passed others. Today the AI says cannot_determine and sends the chat to manual review.
- **Churn:** "I'll look for another casino" was treated by QA as a tone issue only (2 chats), which differs from the 10-09 churn rule.

## Next steps

1. Run `scripts/pass_audit_sample.py` on prod. QA opens the 80 chats
   (`/api/conversations?sample=kb-v41-pass-audit`) and presses Agree or Re-score on the stored
   grade. This measures false PASSes. No model run is needed, and nothing is re-graded.
2. QA confirms or corrects points 1–5. Re-measure with `accuracy_ab.py --set holdout` (on a DB
   copy; it never writes grades) after any change.
3. QA presses Agree on reviewed chats going forward, so the Accuracy page tracks the live number.

## Round 2 — learning from QA's corrections (2026-10-21)

**What was read** (by Claude Code within the subscription — no API calls, nothing graded):
- the 34 dev-split chats where the round-1 rules + verifier still disagreed with QA (23 false FAILs, 11 false PASSes), with transcripts, the AI's evidence and QA's comment;
- the 6 newest re-scores (20 Oct);
- 1,391 historical re-scores under the old ruleset (Jul – 7 Oct) — the 400 with a real comment, a lowered score or a criterion QA failed — and 68 agent disputes.

The 35-chat holdout was **not** read, so it still measures the change honestly. The 180-chat
document (`Vybirka_180_Chativ_King_Billy`) carries no QA verdicts — every row is "Не розпочато".

**Of the 34 + 6 disagreements:** about 20 have a general rule fix; about 8 are the AI misreading
a rule it already had (fixed by sharper wording, mainly in the verifier); about 10 depend on
knowledge the AI cannot see (the 15 Oct scam-deposit shift, a power outage, the back office,
internal bonus policy) or contradict QA's own verdicts on similar chats.

**Rule changes** (prompt `rules/qa_system_prompt_kb_v41.txt` = `qa/kbv41_prompt.py`, and `BOUNDARIES` in `qa/fail_verifier.py`):

| # | Change | Chats it explains (estimate, not measured) | Direction |
|---|---|---|---|
| 1 | A secondary request skipped while the main one was handled, not repeated and not in the last messages, is info-completeness — even about money | 6 | false FAIL |
| 2 | Bot-menu clicks before the agent joins only route the chat; the requests are what the player writes. Since rules `c5454a028a30` the transcript marks them itself — `CUSTOMER … (bot-menu choice): My deposit did not come` (`Conversation.menu_choices`, from Intercom's `quick_reply` parts) — so the model no longer has to guess which lines were buttons. **Not yet measured.** | 4 | false FAIL |
| 3 | The player only repeating a demand already answered/refused with a reason and an alternative is not ghosting; a rule reminder answers the question; a security refusal to open a link/file is correct | 4 | false FAIL |
| 4 | Accuracy: a contradiction needs two statements that cannot both be true and change what the player does (not: general rule vs this player's exception, two wordings of one term, the player's denial, an unsure internal note) | 5 | false FAIL |
| 5 | Leverage churn threat ("bonus or I leave") answered with a polite refusal + help is a reaction | 1 | false FAIL |
| 6 | Internal notes are never judged for tone/language; an agent-side outage note → cannot_determine (manual review) | 2 + history | false FAIL |
| 7 | Escalation not due yet (reward not due by the promo timeline; waiting on the player's step); a remark about one's manager is not a request for one | 2 | false FAIL |
| 8 | **Still failures:** the player corrects the agent and gets the same answer; a vague "forwarded" repeated to a player asking for status | 2 | false PASS |
| 9 | **Still a failure:** the inactivity close after the player's last message went unanswered, or after re-asking for something already given | 2 + history | false PASS |
| 10 | An explicit close / block / self-exclude request not visibly carried out or forwarded → **cannot_determine, manual review** (rules `47e9ee024019`; it was an automatic failure for one run, but QA passed 3 such chats and failed 2 — their call) | 2 | manual review |
| 11 | **Still a failure:** denying service on eligibility (country) when the Help Center says the player is accepted | 1 | false PASS |
| 12 | Verifier boundaries that excused real defects were narrowed: templated replies to a visibly upset player (comm-tone), a refusal with no reason or next step (info-completeness), asking for what the player already gave (ownership-effort) | history (~75 corrections) | false PASS |

Rules version `66c36e68e8c0`; every change applies to chats graded from then on only.

**Measured (2026-10-20, Sonnet 5.5 high effort, batch, DB copy, $3.93; nothing saved):**

| Configuration | Dev (116) | Holdout (35) | All (151) | False FAIL / false PASS |
|---|---|---|---|---|
| Round-1 rules + verifier (`78889e130313`) | 70.7% | 80.0% | 72.9% | 30 / 11 |
| Round-2 rules alone (`66c36e68e8c0`) | 75.0% | 71.4% | 74.2% | 32 / 7 |
| **Round-2 rules + verifier** | **77.6%** | **80.0%** | **78.1%** (95% CI 71–84%) | 25 / 8 |
| Round-2 rules + bot-menu marker + verifier (`c5454a028a30`) | 78.5% | 74.3% | 77.5% | 25 / 9 |

- **The marker run** (third run, $2.99) agrees with the second on 87.4% of chats: 9 chats fixed — both bot-menu chats among them — and 10 broken, none of the 10 on a menu line. The ±3 points between the runs is the model's own variance on this set, so a single run cannot show a 2–3-chat improvement. Five of the ten were escalation calls the model flips between runs (withdrawal status unclear → Finance; account closure), which is where the next gain is: QA's decision on those cases, and a majority vote on borderline chats.
- **Pass audit** (80 chats the stored grades passed): **0 new FAILs** (round 1: 1–2). Manual review 25 of 80, as before — escalations told to the player with no internal note.
- **The holdout did not move:** 7 disagreements both times; on 35 chats the interval is 64–90%. The gain is on dev, where the rules were written, so part of it may be fit to those chats.
- Projected population agreement 97.9%, under the same unverified assumption that QA agrees with the AI's PASSes.
- The 33 remaining disagreements: 7 are the scam shift and the power outage; 6 are escalation FAILs QA passed with "no mistakes"; 7 are resp-no-ghost FAILs QA passed with "no mistakes"; 8 are false PASSes, half of them back-office or internal-policy knowledge.

**For QA to confirm** (items that change how the manual reads): 1 (money-related secondary
request is not a Gate 2 failure), 7 (manager remark), 10 (account closure as a required action),
11 (country eligibility vs the Help Center).

**History says QA wants, but the AI cannot judge without data:**
- VIP players: a goodwill/bonus request, closure or data change must go to vip_cs; telling the player to write to their VIP manager is a miss (~28 corrections). Needs the player's VIP status in the transcript header.
- Shift events (scam waves, outages) — exclude those chats from calibration, or mark them.
- Tag taxonomy, the PG quiz for RG, internal bonus eligibility (B/C tags), German/Polish support since 2026-09-17 (comm-language).

## QA lessons — the weekly loop

`qa/lessons.py`, the **QA lessons** page. Every `QA_LESSONS_DAYS` (7) the web app collects QA's
re-scores since the last sync, groups the disagreements by criterion and, for each criterion
corrected ≥ `QA_LESSONS_MIN_CASES` (3) times, asks Sonnet for one general rule (~$0.1 a week).
Drafts wait on the page; an admin edits, approves or rejects. Approval adds the rule to the
prompt's QA PRACTICE section and (NOT-A-FAIL rules) to the verifier — for new chats only.
`intercom-summary lessons sync --no-draft` reports the week's corrections without a model call.

## QA feedback in writing → reference labels

QA's written feedback on a calibration run (Slack, 8 Oct: four chats of the 180 sample with the
score they expect) is kept as `docs/qa-feedback/<date>-<sample>.csv` and loaded as reference
labels: `python scripts/import_calibration_sample.py docs/qa-feedback/2026-10-08-calibration-180.csv --id kb-v41-180 --labels`.
From then on every run of the sample is checked against them in the Calibration tab, and the
weekly QA-lessons sync reads them too. Those four chats have graded as QA expects since the
9 Oct rules (runs `5cec4875c67e` onward); the 8 Oct run they were written about was `52bd706b3797`.

**Narrative vs verdict** (QA's point 1): the story and the verdict are produced separately. When
the story says the player was left unanswered over a PASS, or "no mistakes" over a FAIL, the
grade now goes to manual review with the contradicting phrase as the reason
(`gated_rules.narrative_conflict`). The verdict itself is never changed on wording alone.
