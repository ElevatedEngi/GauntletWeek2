# Chart Summarizer Module — Manual Test Checklist

> PHP module testing is harder to automate due to OpenEMR's session/ACL layer.
> Run these checks after installing the module in a local OpenEMR instance.

## Prerequisites

- OpenEMR 7.x running locally (Docker Compose recommended).
- Chart Summarizer Python agent running and reachable at `http://chart-summarizer-agent:8000`.
- `CHART_SUMMARIZER_API_KEY` env var set to a non-empty value on the PHP side.
- At least one patient loaded (use the OpenEMR demo seed data).
- Two browser sessions available: one admin, one non-admin provider.

---

## 1. Module Installation

- [ ] Module appears in **Admin → Modules → Manage Modules** without errors.
- [ ] Clicking **Install** runs `install.sql` without MySQL errors.
- [ ] After install, `chart_summarizer_config` table exists with one row (id=1).
- [ ] After install, `chart_summarizer_requests` table exists and is empty.
- [ ] After install, `chart_summarizer_summaries` table exists and is empty.
- [ ] After install, `chart_summarizer_feedback` table exists and is empty.
- [ ] Module can be **Disabled** and **Re-enabled** without errors.
- [ ] Module can be **Uninstalled** cleanly (tables dropped if uninstall.sql exists, or left intact per OpenEMR convention).

---

## 2. ACL / Access Control

- [ ] A user **without** `patients/docs` ACL sees "Access denied" when navigating to `/modules/ChartSummarizer?pid=1`.
- [ ] A user **with** `patients/docs` ACL sees the summary viewer page.
- [ ] The **Settings page** (`/modules/ChartSummarizer/settings`) returns 403 for a non-admin user.
- [ ] An admin user (with `admin/super` ACL) can access the Settings page.
- [ ] Accessing the page **without a `pid` query param** shows "No patient selected."

---

## 3. Patient Menu Integration

- [ ] "AI Chart Summary" menu item appears under the patient menu when a chart is open.
- [ ] Clicking the menu item navigates to `/modules/ChartSummarizer?pid=<current_pid>`.
- [ ] Menu item is **not** visible when no patient chart is open.

---

## 4. Summary Generation (Happy Path)

- [ ] Select a specialty from the dropdown and click **Generate Summary**.
- [ ] A loading spinner and message "Retrieving patient data…" appears immediately.
- [ ] After 5+ seconds, loading message updates to "Generating AI summary — this may take up to 60 seconds…".
- [ ] On success, the summary output panel becomes visible.
- [ ] Confidence banner displays with correct color: GREEN (✓), YELLOW (⚠), or RED (✗).
- [ ] Summary content is rendered as formatted text (Markdown or fallback `<pre>`).
- [ ] Metadata line shows model name and latency (e.g. "claude-sonnet-4-6 · 3420ms").
- [ ] Citations panel shows count and individual citation items with Verified/Unverified badges.
- [ ] When no citations are returned, "No citations available" message is shown.

---

## 5. Summary Generation (Error States)

- [ ] When the Python agent is **unreachable** (stop the agent container), the error panel shows "Unable to reach the chart summarizer service."
- [ ] When the agent returns **HTTP 429**, the error panel shows "Rate limit reached."
- [ ] When the agent returns **HTTP 504**, the timeout panel shows the timeout message.
- [ ] When the agent returns **HTTP 503**, the unavailable panel appears.
- [ ] After any error, clicking **Generate Summary** again resets the error state and retries.

---

## 6. Copy to Clipboard

- [ ] Clicking **Copy** copies the raw Markdown summary text to clipboard.
- [ ] Button briefly shows "Copied!" and turns green, then reverts after 2 seconds.
- [ ] Copy button is only visible when a summary has been generated.

---

## 7. Feedback — Approve

- [ ] Clicking **Approve** sends a POST to `/modules/ChartSummarizer/feedback`.
- [ ] After approval, button turns solid green and shows "Approved" (disabled).
- [ ] A row is recorded in `chart_summarizer_feedback` with `rating = 1`.

---

## 8. Feedback — Edit (Modal)

- [ ] Clicking **Edit** opens the feedback modal.
- [ ] Entering edits and clicking **Submit Feedback** closes the modal.
- [ ] After submission, Edit button shows "Edited" (disabled).
- [ ] A row is recorded in `chart_summarizer_feedback` with `rating = -1` (or as configured).
- [ ] Modal closes cleanly on **Cancel** without submitting feedback.

---

## 9. Feedback — Reject

- [ ] Clicking **Reject** shows a browser `confirm()` dialog.
- [ ] Cancelling the confirm does **not** submit feedback.
- [ ] Confirming submits feedback and clears the summary output panel.
- [ ] A row is recorded in `chart_summarizer_feedback` with `rating = -1`.

---

## 10. Settings Page (Admin)

- [ ] Settings page renders with current config values pre-filled.
- [ ] Changing **Agent Base URL** and saving persists to the `globals` table.
- [ ] Changing **Request Timeout** outside 5–120 range is clamped server-side.
- [ ] Changing **Rate Limit** outside 1–200 range is clamped server-side.
- [ ] Toggling **Show Disclaimer** checkbox saves correctly.
- [ ] Toggling **Allow Save to Chart** checkbox saves correctly.
- [ ] After saving, reloading the settings page shows the updated values.

---

## 11. Rate Limiting

- [ ] Generate summaries rapidly for the same user until the rate limit is hit.
- [ ] On hitting the limit, HTTP 429 is returned and the error panel appears.
- [ ] A different user account is **not** affected by the first user's rate limit.

---

## 12. AI Disclaimer

- [ ] When `show_disclaimer = 1` (default), the AI-generated disclaimer is visible on the summary page.
- [ ] When `show_disclaimer = 0` (toggle in settings), the disclaimer is hidden.

---

## 13. HIPAA Audit Log

- [ ] After generating a summary, a row is added to `chart_summarizer_requests`.
- [ ] The row contains `patient_pid`, `provider_id`, `model_used`, `status`.
- [ ] The row does **not** contain any PHI beyond the numeric PID.
- [ ] Failed/partial requests are logged with the correct `status` value.

---

## 14. Security Spot-Checks

- [ ] Manually POST to `/modules/ChartSummarizer/generate` with `patient_id=<other_patient>` as a user without access — confirm 403 is returned.
- [ ] Confirm the PHP cURL call to the agent uses `Authorization: Bearer <key>` (check access logs on the agent side).
- [ ] Confirm `api_key_enc` in `chart_summarizer_config` is base64 ciphertext (not plaintext key).
- [ ] In summary HTML, confirm citation text containing `<script>` is escaped (`&lt;script&gt;`).

---

## Test Sign-off

| Tester | Date | OpenEMR Version | Agent Version | Pass/Fail | Notes |
|--------|------|-----------------|---------------|-----------|-------|
|        |      |                 |               |           |       |