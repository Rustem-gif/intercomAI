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

1. QA reviews `kb-v41-pass-audit` (80 chats) with Agree or Re-score. This measures false PASSes.
2. QA confirms or corrects points 1–5. Re-measure with `accuracy_ab.py --set holdout` after any change.
3. QA presses Agree on reviewed chats going forward, so the Accuracy page tracks the live number.
