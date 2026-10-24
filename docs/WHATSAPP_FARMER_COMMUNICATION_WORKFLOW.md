# WhatsApp farmer communication workflow

How an operator sends a stored recommendation to a farmer, and what the system
can and cannot know about it afterwards.

---

## 1. What this is

**One button on a recommendation, and one record saying it was pressed.**

SaltLens can produce a recommendation for a salt pan. This workflow lets an
operator put that recommendation in front of the farmer as a WhatsApp message
that the farmer can read and reply to, without leaving the operator's phone.

The mechanism is a **click-to-chat link**. SaltLens builds a URL:

```
https://wa.me/<farmer's number>?text=<the exact message>
```

The operator's browser opens that link, WhatsApp opens on their own device with
the message pre-filled, and **the operator presses send there**. SaltLens is not
in the conversation. It sends no message itself and receives no receipt.

That last sentence is the whole of what this workflow knows. Everything below is
consequence.

### What is not here, on purpose

| Not present | Why |
| --- | --- |
| WhatsApp Business API | No account, token or webhook. Nothing is transmitted server-side. |
| Delivery receipts | WhatsApp is not on the conversation. No read receipt exists. |
| Farmer reply capture | A reply lands in the operator's WhatsApp, not in SaltLens. |
| Operator agreement on the recommendation | A send is not an approval. See `OperatorDecisionPanel`. |
| Physical-action or field-validation state | Sending text does not move brine. |

Sending a message and recording an operator decision are separate acts with
separate records. A send **never** creates or modifies a
`Recommendation.operator_decision_id`, `verification_state`, an outcome, or any
field-validation state.

---

## 2. Recording a farmer on a pan

The number lives on the pan, not on the recommendation. A recommendation is a
statement about a pan at a moment; the farmer who works that pan is a property
of the pan, and re-deriving it from the recommendation is what stops one pan's
recommendation being sent to another pan's farmer.

At `/pans/new` or `/pans/{panId}/edit`:

| Field | Rule |
| --- | --- |
| Farmer name | Optional, ≤ 128 characters |
| WhatsApp number | Optional. Must be E.164: `+` then 8–15 digits |

Formatting is accepted and normalised on the way in, because that is how numbers
are written down and read aloud:

| Typed | Stored |
| --- | --- |
| `+919876543210` | `+919876543210` |
| `+91 98765-43210` | `+919876543210` |
| `+91 (987) 654.3210` | `+919876543210` |
| `09876543210` | **refused** — no country code |
| `+9876` | **refused** — too short |

**The number is never displayed again.** Every read path returns a mask such as
`+919•••••••10`. The edit form shows that mask as a hint and leaves the input
blank: submitting an unchanged blank field keeps the stored number, so opening
a pan to correct its name cannot silently erase its contact.

Clearing a contact is deliberate. An explicit `null` on update clears it; an
omitted field leaves it alone.

---

## 3. Sending

### Step 1 — the panel loads a preview

`GET /api/decisions/{recommendation_id}/whatsapp-preview`

The operator sees the recipient mask, the farmer's name if recorded, and the
exact message. Nothing is written.

### Step 2 — the operator presses the button

`POST /api/decisions/{recommendation_id}/whatsapp-send`

The body carries `operator_id` and **nothing else**:

```json
{ "operator_id": "op-sach" }
```

There is no message, recipient or number field to send, because any of them
would be a way to record a communication the stored recommendation does not
support. A body containing `message`, `pan_id` or a number is rejected with 422.

### Step 3 — order of operations on the server

This order is the security property:

1. **Load the recommendation.** Absent, or one that named no action → `409`.
2. **Derive the pan from it.** The caller never names a pan.
3. **Read and normalise the contact from that pan.**
4. **Build the message** from stored pan and recommendation fields.
5. **Build the click-to-chat URL** from that same message.
6. **Write exactly one audit record.**
7. **Return the URL.**

The URL is built before the write, so an unusable number cannot leave a record
behind. A refusal at any step writes nothing at all, so a failed attempt leaves
no trace that could later be mistaken for a send.

### Step 4 — the browser opens WhatsApp

The frontend calls `window.open(url, "noopener,noreferrer")` **after** the record
exists. Opening the link is the send; SaltLens has already written down that the
operator did so.

If the browser blocks the popup, the same text is offered for copying.

---

## 4. The message

Rendered by `backend/app/services/whatsapp.py` from stored values only. It
recomputes nothing: not the salinity, not the action, not the threshold, not the
simulation, not any rule outcome. If the message and the dashboard disagreed, the
message would be the thing a farmer acted on.

```
SaltLens DSS - Salt-pan recommendation

Pan: WhatsApp verification pan
Date: 5 October 2026

Recommendation: HARVEST NOW
Priority: Medium

Predicted salinity after rain (simulated): 247.61 g/L
Configured harvest threshold: 280 g/L
Rainfall scenario: 25 mm (request.rainfall_mm)
Recommendation rule: Operator-configured harvest and flood rule v2

This is a decision-support recommendation, not a measured field outcome. Please
verify the current field conditions before taking any physical action.
```

Rules applied:

- Optional sections (priority, rainfall, threshold, rule) are **omitted** when
  the recommendation has no value for them, rather than printed as "N/A".
- The disclaimer is always present. It is the sentence that stops a simulated
  figure being read as a measurement.
- **The ML probability is deliberately absent.** A probability is a model
  statement about risk, and putting one in a farmer's message invites it being
  read as "the salt pan is 40% bad" or as a decision threshold. It stays in the
  risk card where its meaning is defined.
- Each render carries a `message_sha256` and `message_character_count`. The hash
  is not a security hash: its job is to answer "was this the same message as the
  one sent?" months later, given the recommendation is stored and re-derivable.
  A builder change shows up as a changed hash even when the recommendation did
  not change.

---

## 5. What the record says

One document in `recommendation_deliveries` per press.

| Field | Value |
| --- | --- |
| `channel` | `whatsapp` |
| `action` | `SEND_INITIATED` |
| `delivery_status` | `not_confirmed` |
| `delivery_confirmed` | `false` |
| `farmer_response_recorded` | `false` |
| `physical_action_confirmed` | `false` |
| `farmer_contact_reference` | masked, e.g. `+919•••••••10` |
| `farmer_name` | as recorded on the pan, or `null` |
| `message_sha256`, `message_character_count` | as rendered |
| `specification_hash` | copied from the recommendation |
| `operator_id` | self-reported; see §7 |

The three `*_recorded: false` flags exist so a client has no excuse for inferring
otherwise from their absence. The model's validators refuse a document that
claims delivery, a farmer response or a completed physical action — the
collection cannot hold that claim even if a future caller tries to write it.

**The collection is append-only.** Pressing the button twice writes two records.
That is deliberate: collapsing them into one would report a single reach-out for
two attempts. The panel shows the history for a recommendation, newest first.

`GET /api/decisions/{recommendation_id}/whatsapp-sends` returns
`found: false` with an empty list for a recommendation that has never been sent.
A recommendation with no sends is the normal case, not an error.

### Where the full number appears

| Location | Full number |
| --- | --- |
| Pan document | yes — the send needs it |
| Pan read API, preview, history, audit | **no**, masked only |
| Send response `click_to_chat_url` | **yes** — it is the recipient address |

The URL necessarily contains the digits; that is what `wa.me` resolves. It is not
logged, not stored in the audit, and not written to the delivery record.

---

## 6. Refusals

Each is a 409 with a code a client branches on, matching the refusal convention
used by the operator-decision and feedback modules.

| Code | Meaning | Operator can fix it? |
| --- | --- | --- |
| `recommendation_not_found` | No such recommendation | no |
| `recommendation_has_no_action` | The stored recommendation names no action | no |
| `pan_not_found` | The pan the recommendation points at is gone | no |
| `farmer_contact_not_configured` | That pan has no number recorded | **yes** — the panel offers "Open the pan" |
| `farmer_contact_invalid` | The stored number no longer normalises | **yes** — re-enter it |

Every one of these writes nothing.

A missing contact is not an error state in the interface: it is a task. The panel
renders "Open the pan" rather than a dead end. A transient fault renders "Try
again", which re-reads in place rather than reloading the page.

---

## 7. Authentication: the same gap as everywhere else

**No route in this backend resolves a user, a token or a role.** Every endpoint
has the same property, so this one inherits it rather than inventing a mechanism
nothing else uses.

`operator_id` is therefore a **caller-supplied string**, stored as "the operator
who said they were this operator". Adding a dependency to this single route
would leave the other twenty unguarded and make the system harder to reason
about than it is today. This is recorded rather than papered over.

---

## 8. Verifying it by hand

```
# 1. A pan with a farmer, and a recommendation on it
curl -X POST localhost:8000/api/pans/ -H 'Content-Type: application/json' -d '{
  "pan_id":"wa-verify-01","pan_name":"WhatsApp verification pan",
  "location":"Port Shepstone","area_m2":9000,"normal_depth_cm":16.0,
  "target_harvest_salinity_g_l":280.0,
  "farmer_name":"Ravi Patel","farmer_whatsapp":"+91 98765-43210"}'

# 2. Read it back — masked, never the digits
curl localhost:8000/api/pans/wa-verify-01     # farmer_whatsapp_masked: "+919•••••••10"

# 3. Preview without sending, and without writing
curl localhost:8000/api/decisions/<rec>/whatsapp-preview

# 4. Send. One record, URL returned, nothing else touched
curl -X POST localhost:8000/api/decisions/<rec>/whatsapp-send \
     -H 'Content-Type: application/json' -d '{"operator_id":"op-sach"}'

# 5. History, newest first
curl localhost:8000/api/decisions/<rec>/whatsapp-sends
```

Then in the browser at `/dashboard`: the panel appears on the recommendation, the
button is enabled only when a number exists, the refusal paths render their own
links, and pressing send opens WhatsApp with the pre-filled text.

Checks worth making by eye:

- The message in the preview is byte-identical to the one the send returns.
- No response body other than the URL contains the unmasked digits.
- The recommendation's verification state and operator decision are unchanged
  after a send.

---

## 9. Tests

| Suite | Covers |
| --- | --- |
| `backend/tests/test_whatsapp.py` | 66 tests: message rendering, URL encoding, refusals, audit contents, masking, append-only behaviour, body-field rejection |
| `backend/tests/test_salt_pans_api.py` | E.164 normalisation, contact add/replace/clear, pan read masking |
| `frontend/src/lib/whatsapp.test.ts` | Button states, refusal wording, outcome vocabulary |
| `frontend/src/lib/whatsappApi.test.ts` | Request paths, bodies, error mapping |
| `frontend/src/components/dashboard/whatsappSendPanel.test.tsx` | 30 tests: every UI state, popup, fallback, history |
| `frontend/src/lib/pans.test.ts` | Form validation, round-tripping, masked number never re-shown |

Run them:

```
backend/.venv/bin/python -m pytest tests/test_whatsapp.py -q
cd frontend && npx vitest run src/components/dashboard/whatsappSendPanel.test.tsx src/lib/pans.test.ts
```

---

## 10. What this workflow cannot tell you

State plainly, because the failure mode is a record being read as more than it
is:

- **That the message was delivered.** The record is a click, not a receipt.
- **That the farmer read it.**
- **That the farmer replied**, or agreed, or disagreed.
- **That the recommended action was taken.** That is `physical_action_confirmed`,
  which this record sets to `false` and the model refuses to let anyone set true.
- **Who the operator was**, absent an authenticated identity.
- **That the farmer acted on good information.** The disclaimer is in the
  message; whether it was read is unknown.
