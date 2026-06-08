Conversation Start

Greeting — The agent greeted the player at the beginning of the conversation.
Use of name — The agent addressed the player by name at least once.
Request Type Detection 3. Clarifying questions — The agent asked clarifying questions to correctly identify the request type. If the player selected a category before the chat started, this point is counted automatically. If no category was selected, or the player chose the wrong one, this item is mandatory. 4. Request specified — The agent confirmed and specified the player's request before proceeding. Same condition as above: auto-counted if a correct category was pre-selected; mandatory otherwise.
Providing Correct Information 5. Information accuracy — The information provided was correct and factually accurate. 6. Compliance with internal regulations — The information provided complied with internal regulations and policies. 7. Informativeness — The response was complete and clear enough for the player to fully understand.
Customer Focus 8. Friendliness and willingness to help — Throughout the conversation, the agent was friendly and made a genuine effort to assist the player. 9. Grammar and spelling — The agent's messages were free of grammatical and spelling errors.
First Contact Resolution 10. First Contact Resolution — The agent made every effort to resolve the player's request within this conversation.
Timing 11. First response time ≤ 1 minute — The agent sent the first reply within one minute. 12. Time between replies ≤ 3 minutes — The interval between the agent's replies did not exceed three minutes. 13. Delay warning — If resolving the request required more time, the agent proactively warned the player — and the delay did not exceed five minutes.
Chat Closure 14. "Anything else?" check — Once the dialogue was logically complete, the agent asked whether the player had any further questions before closing. 15. Farewell — The agent thanked the player for reaching out and said goodbye.

## Responsiveness

- **id: resp-first-reply** — The agent's first human reply is timely and relevant to the
  customer's question (not a generic deflection).
- **id: resp-no-ghost** — The agent does not leave the customer's direct question
  unanswered; every explicit question gets addressed.

## Resolution Quality

- **id: res-understand** — The agent correctly understands the customer's actual problem
  before proposing a solution (asks clarifying questions when needed).
- **id: res-actionable** — The agent gives clear, actionable steps or a concrete answer,
  not vague guidance.
- **id: res-accuracy** — Information the agent provides is accurate and consistent; no
  contradictory or obviously wrong statements.

## Process & Compliance

- **id: proc-escalation** — When the issue is outside the agent's scope, the agent
  escalates or routes appropriately instead of guessing.
- **id: proc-no-promises** — The agent does not over-promise (e.g. guaranteed refunds,
  fixed dates) beyond what policy allows.
- **id: proc-data-care** — The agent does not ask for or expose sensitive data
  unnecessarily (passwords, full card numbers, etc.).

## Closing

- **id: close-confirm** — Before closing, the agent confirms the customer's issue is
  resolved or offers further help.
- **id: close-courtesy** — The agent closes courteously (thanks the customer / invites
  them back if needed).

---

### Scoring guidance for the QA agent

- Start from 100 and deduct for violations, weighted by severity
  (process/accuracy issues weigh more than a missing greeting).
- Mark a rule `n/a` when the conversation genuinely gave no opportunity to apply it
  (e.g. no closing happened because the conversation is still open).
- Always cite brief evidence (a short quote) for any `fail`.
