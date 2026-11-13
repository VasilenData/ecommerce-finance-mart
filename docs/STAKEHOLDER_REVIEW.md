# A real review starts here

**Status: ready to request review; no stakeholder review, approval or adoption has been recorded.**

This is a handoff for the synthetic subscription product, not evidence that a finance team used it. A generated request, passing tests or AI-assisted code review cannot establish independent human review. The historical retail workbook and its measured release remain separate.

## Review the actual decision

Open the [read-only close explorer](https://vasilendata.github.io/ecommerce-finance-mart/subscriptions/). Select a knowledge cutoff. Its close-review section covers the **whole reporting window**, even when the top reporting-month filter changes.

1. Inspect the definitions and source revisions. Confirm whether a refund should leave an invoice obligation open. Check whether complete subscription history is a reasonable source assertion.
2. Compare gross unpaid invoice amounts with credits. Do not net different invoices or customers into a collection recommendation. Due dates, enforceability and a credit-note workflow are absent, so “unpaid” does not mean “overdue” or “collectible.”
3. Try each proposed triage threshold. Every changed penny, at least £25, and at least £25 **or** 5% are illustrative review policies, not approved accounting materiality. Boundaries are inclusive; the relative rule uses the absolute original value. A nonzero change from zero has no meaningful percentage and requests review under the combined policy.
4. Download the selected request. It fixes the source hash, baseline and candidate report hashes, restatement audit hash, cutoff, window and policy. All requests stay `pending_human_review`. The content digest detects accidental changes; it is not a digital signature or identity verification.
5. Send the request to a real reviewer yourself. Ask them to challenge one definition, reproduce one number and propose one change. Record their actual response separately, retaining the original request unchanged. Do not fill in a reviewer, date or decision on their behalf.

### What the default exercise should reveal

At the 4 April cutoff compared with the 1 April close, eight monitored metric cells change by £10. These are **eight cells, not eight independent issues**: the same invoice correction appears in monthly balances, its invoice and aggregate exposure. The every-penny policy flags eight cells; the £25-only policy flags none; the £25-or-5% policy flags six. All three remain pending review.

The two cells below the combined threshold are February closing MRR and February billed amounts (both £240 → £250, about 4.17%). March closing MRR changes £180 → £190 (about 5.56%). February closing balance and B's corrected invoice change £0 → £10; March closing balance and period-end gross unpaid/net balances change £40 → £50 (25%). Relative decisions use exact integer arithmetic, not rounded displayed percentages.

The queue monitors monthly closing MRR, billed amount, net cash and closing invoice balance; every period-end invoice balance; and aggregate period-end gross unpaid, credit magnitude and net balance. It is **not a complete anomaly detector**. Source revisions and the full monetary audit remain available even if none of these measures change. In particular, the queue does not replace review of MRR movement classifications or data completeness.

## Feedback record template — uncompleted

Copy this into a separate issue, document or pull-request discussion when there is genuine feedback. Keep personal information private unless the reviewer agrees to publication. No contact, message or review has been created by this template.

```json
{
  "status": "pending_human_review",
  "request_sha256": "copy from the exported request",
  "reviewer": null,
  "reviewed_at": null,
  "definition_challenges": [],
  "evidence_requested": [],
  "proposed_changes": [],
  "decision_rationale": null,
  "author_response": null,
  "change_or_no_change_reason": null,
  "reproduction_command": null,
  "new_release_or_pull_request": null,
  "reviewer_follow_up": null
}
```

Useful review questions:

- **Finance:** Could a cash refund legally reopen this invoice, or is the model missing a credit note? What additional source fields would settle that definition?
- **Operations:** Which unpaid/credit balances warrant investigation first, and what evidence is needed before acting? What changes if payments can be allocated across several invoices?
- **Engineering:** Can a payment-date correction move cash between months without changing quarter cash? Can you reproduce the source-bound request from a clean checkout?
- **Reporting owner:** When should an original close remain frozen, and who should authorize consuming a restated version? Which affected outputs should be communicated?

## Respond with a reviewable change

If a reviewer requests a definition change, write the proposed source contract and a small hand-calculated counterexample first. Add a failing test, implement the smallest justified change, and compare the original and revised outputs over the affected months and invoices. Publish a **new** immutable release and identify the older release it supersedes; do not rewrite its bytes or original close. Ask the reviewer to inspect the new evidence and retain the actual discussion. A decision to leave the model unchanged also needs a stated reason.

The goal is a real sequence of question → evidence → change or reasoned rejection → follow-up. A polished template alone is not collaboration evidence.

## Reproduced issue and regression proof

On base commit `be68a76cb6a41fd66c43c5181975cb9e19dd5d34`, a two-invoice fixture demonstrated a misleading decision brief:

| Invoice | Invoiced | Paid | Balance |
|---|---:|---:|---:|
| A / unpaid | £100 | £0 | £100 |
| B / credit | £100 | £200 | −£100 |
| Net | £200 | £200 | £0 |

The actual old renderer said **“£0.00 remains on the invoice ledger.”** The arithmetic was correct but concealed the outstanding invoice and separate credit. The updated brief and review section show **£100 gross unpaid, £100 credit, £0 net** and avoid a collection recommendation. `test_net_zero_must_not_hide_unpaid_invoice_and_credit` runs this fixture through the actual SQL pipeline; the JavaScript regression checks the emitted decision text. Additional tests reject duplicate invoice grain, unreconciled exposure and browser-unsafe gross sums.

The payment-date fixture independently moves £100 from January to February. Quarter cash remains unchanged, while both monthly changes still request review. Threshold tests cover inclusive boundaries, negative baselines, undefined zero-baseline percentages and rejected unsafe values. Release tests bind requests to frozen source bytes and preserve the earlier artifact manifest.

## Reproduce

```bash
python -m pytest -q tests/test_subscription_review.py
python -m mart.subscriptions --output dist/subscription-review
node --test tests_js/subscriptions.test.mjs
```

The JavaScript suite exercises the emitted code and checked-in payload with a minimal DOM, checks that the published HTML matches the current template, and verifies request export. It is not a visual browser test. The release manifest records source/code/artifact hashes and distinguishes reconciliations from review status.

Design references: [GitLab analytics engineering](https://handbook.gitlab.com/handbook/enterprise-data/organization/analytics-engineering/) for shared definitions and reviewable work; [dbt unit tests](https://docs.getdbt.com/docs/build/unit-tests) for controlled input/output fixtures. The working brief is: **reproduce the decision risk; state grain and time scope; derive a separate oracle; compare bounded policies without changing actuals; preserve evidence; leave human approval pending until it exists.** No new framework or service is required.
