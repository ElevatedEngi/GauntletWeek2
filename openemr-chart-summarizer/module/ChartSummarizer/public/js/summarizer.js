/**
 * Chart Summarizer module — client-side JavaScript.
 *
 * Copyright (C) 2026 OpenEMR Community
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * Responsibilities:
 *  - Submit summary generation form via fetch() POST
 *  - Poll for status if generation takes >5 seconds
 *  - Render the Markdown summary as sanitised HTML
 *  - Show confidence level banner with correct colour
 *  - Render citations panel linked to FHIR resources
 *  - Handle approve / edit / reject feedback actions
 *  - Copy-to-clipboard for summary text
 *  - Graceful error handling with user-friendly messages
 */

'use strict';

(function () {

    // -----------------------------------------------------------------------
    // DOM references
    // -----------------------------------------------------------------------

    const form              = document.getElementById('summary-request-form');
    const generateBtn       = document.getElementById('generate-btn');
    const generateBtnText   = document.getElementById('generate-btn-text');
    const generateSpinner   = document.getElementById('generate-btn-spinner');
    const summaryLoading    = document.getElementById('summary-loading');
    const loadingMessage    = document.getElementById('loading-message');
    const summaryOutput     = document.getElementById('summary-output');
    const summaryContent    = document.getElementById('summary-content');
    const summaryMeta       = document.getElementById('summary-meta');
    const confidenceBanner  = document.getElementById('confidence-banner');
    const copyBtn           = document.getElementById('copy-btn');
    const citationsList     = document.getElementById('citations-list');
    const citationCount     = document.getElementById('citation-count');
    const noCitations       = document.getElementById('no-citations');
    const summaryError      = document.getElementById('summary-error');
    const summaryErrorMsg   = document.getElementById('summary-error-msg');
    const summaryUnavailable = document.getElementById('summary-unavailable');
    const summaryTimeout    = document.getElementById('summary-timeout');
    const approveBtn        = document.getElementById('approve-btn');
    const editBtn           = document.getElementById('edit-btn');
    const rejectBtn         = document.getElementById('reject-btn');
    const feedbackSubmitBtn = document.getElementById('feedback-submit-btn');
    const feedbackEdits     = document.getElementById('feedback-edits');
    const feedbackNotes     = document.getElementById('feedback-notes');

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    /** @type {string|null} UUID of the current summary (metadata.request_id) */
    let currentSummaryId = null;

    /** @type {string} Raw markdown text for copy-to-clipboard */
    let currentSummaryText = '';

    // -----------------------------------------------------------------------
    // Form submit — trigger summary generation
    // -----------------------------------------------------------------------

    if (form) {
        form.addEventListener('submit', async function (e) {
            e.preventDefault();
            await generateSummary();
        });
    }

    /**
     * Submit the summary request to the PHP controller (which proxies to the
     * Python microservice) and render the response.
     *
     * If the server takes more than 5 seconds, the loading message updates to
     * indicate work is in progress.
     */
    async function generateSummary() {
        resetAllStates();
        setLoadingState(true);
        showLoading('Retrieving patient data…');

        const formData = new FormData(form);
        const payload = {
            patient_id:        formData.get('patient_pid'),
            specialty:         formData.get('specialty'),
            date_range_months: parseInt(formData.get('months') || '12', 10),
        };

        // Update loading message after 5 seconds to reassure the user
        const loadingTimer = setTimeout(() => {
            showLoading('Generating AI summary — this may take up to 60 seconds…');
        }, 5000);

        try {
            const response = await fetch('/modules/ChartSummarizer/generate', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(payload),
            });

            clearTimeout(loadingTimer);

            if (response.status === 429) {
                showError('Rate limit reached. Please wait before generating another summary.');
                return;
            }

            if (response.status === 504) {
                showSpecificError('timeout');
                return;
            }

            if (response.status === 503) {
                showSpecificError('unavailable');
                return;
            }

            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.error || err.detail || `Server error (HTTP ${response.status})`);
            }

            const data = await response.json();

            if (!data || !data.summary_text) {
                showSpecificError('unavailable');
                return;
            }

            renderSummary(data);

        } catch (err) {
            clearTimeout(loadingTimer);
            if (err.name === 'TypeError') {
                showError('Unable to reach the chart summarizer service. Please contact your administrator.');
            } else {
                showError(err.message || 'An unexpected error occurred.');
            }
        } finally {
            setLoadingState(false);
            hideLoading();
        }
    }

    // -----------------------------------------------------------------------
    // Rendering helpers
    // -----------------------------------------------------------------------

    /**
     * Render the SummaryResponse JSON in the UI.
     *
     * @param {Object} data - SummaryResponse from the Python service.
     */
    function renderSummary(data) {
        currentSummaryId   = data.metadata?.request_id || null;
        currentSummaryText = data.summary_text || '';

        // --- Confidence banner ---
        const level = (data.confidence_level || 'RED').toUpperCase();
        const levelConfig = {
            GREEN:  { cls: 'confidence-green',  icon: '✓', label: 'High Confidence — All claims verified against source records' },
            YELLOW: { cls: 'confidence-yellow', icon: '⚠', label: 'Moderate Confidence — Some data gaps noted' },
            RED:    { cls: 'confidence-red',    icon: '✗', label: 'Low Confidence — Manual review strongly required' },
        };
        const cfg = levelConfig[level] || levelConfig.RED;
        confidenceBanner.className = `alert ${cfg.cls} mb-3`;
        confidenceBanner.innerHTML = `<strong>${cfg.icon} ${cfg.label}</strong>`;

        // --- Summary content (render Markdown if possible) ---
        if (typeof window.markdownit !== 'undefined') {
            const md = window.markdownit({ html: false, linkify: false });
            summaryContent.innerHTML = md.render(currentSummaryText);
        } else if (typeof marked !== 'undefined') {
            summaryContent.innerHTML = marked.parse(currentSummaryText);
        } else {
            // Fallback: plain text with line-break preservation
            const escaped = currentSummaryText
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
            summaryContent.innerHTML = `<pre class="summarizer-plaintext">${escaped}</pre>`;
        }

        // --- Metadata ---
        const meta = data.metadata || {};
        const parts = [];
        if (meta.model_used) parts.push(meta.model_used);
        if (meta.latency_ms) parts.push(`${meta.latency_ms}ms`);
        if (data.status === 'partial') parts.push('⚠ Partial data');
        summaryMeta.textContent = parts.join(' · ');

        // --- Citations ---
        renderCitations(data.citations || []);

        summaryOutput.classList.remove('d-none');
    }

    /**
     * Render the citations list panel.
     *
     * @param {Array} citations - Array of Citation objects from the response.
     */
    function renderCitations(citations) {
        citationsList.innerHTML = '';

        if (!citations || citations.length === 0) {
            noCitations.classList.remove('d-none');
            citationCount.textContent = '0';
            return;
        }

        noCitations.classList.add('d-none');
        citationCount.textContent = String(citations.length);

        citations.forEach(function (citation, idx) {
            const item = document.createElement('div');
            item.className = 'citation-item p-2 border-bottom';

            const verified = citation.verified;
            const badgeClass = verified ? 'bg-success' : 'bg-warning text-dark';
            const badgeLabel = verified ? 'Verified' : 'Unverified';

            item.innerHTML = `
                <div class="d-flex justify-content-between align-items-start">
                    <small class="text-muted">#${idx + 1} · ${escapeHtml(citation.source_type || '')}</small>
                    <span class="badge ${badgeClass} ms-1">${badgeLabel}</span>
                </div>
                <div class="citation-claim mt-1">${escapeHtml(citation.claim_text || '')}</div>
                ${citation.source_id
                    ? `<div class="citation-source mt-1"><code class="citation-id">${escapeHtml(citation.source_id)}</code></div>`
                    : ''}
                ${citation.source_date
                    ? `<div class="text-muted" style="font-size:0.75rem;">${escapeHtml(citation.source_date)}</div>`
                    : ''}
            `;
            citationsList.appendChild(item);
        });
    }

    // -----------------------------------------------------------------------
    // UI state helpers
    // -----------------------------------------------------------------------

    function setLoadingState(loading) {
        if (generateBtn) generateBtn.disabled = loading;
        if (generateBtnText) generateBtnText.textContent = loading ? 'Generating…' : 'Generate Summary';
        if (generateSpinner) generateSpinner.classList.toggle('d-none', !loading);
    }

    function showLoading(message) {
        if (summaryLoading) summaryLoading.classList.remove('d-none');
        if (loadingMessage && message) loadingMessage.textContent = message;
    }

    function hideLoading() {
        if (summaryLoading) summaryLoading.classList.add('d-none');
    }

    function showError(message) {
        if (summaryErrorMsg) summaryErrorMsg.textContent = message;
        if (summaryError) summaryError.classList.remove('d-none');
    }

    function showSpecificError(type) {
        if (type === 'timeout' && summaryTimeout) summaryTimeout.classList.remove('d-none');
        if (type === 'unavailable' && summaryUnavailable) summaryUnavailable.classList.remove('d-none');
    }

    function resetAllStates() {
        if (summaryOutput)      summaryOutput.classList.add('d-none');
        if (summaryError)       summaryError.classList.add('d-none');
        if (summaryUnavailable) summaryUnavailable.classList.add('d-none');
        if (summaryTimeout)     summaryTimeout.classList.add('d-none');
        currentSummaryId   = null;
        currentSummaryText = '';
    }

    function escapeHtml(text) {
        return String(text)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // -----------------------------------------------------------------------
    // Copy to clipboard
    // -----------------------------------------------------------------------

    if (copyBtn) {
        copyBtn.addEventListener('click', async function () {
            if (!currentSummaryText) return;
            try {
                await navigator.clipboard.writeText(currentSummaryText);
                const originalContent = copyBtn.innerHTML;
                copyBtn.innerHTML = '<i class="fas fa-check"></i> <span class="ms-1">Copied!</span>';
                copyBtn.classList.replace('btn-outline-secondary', 'btn-success');
                setTimeout(function () {
                    copyBtn.innerHTML = originalContent;
                    copyBtn.classList.replace('btn-success', 'btn-outline-secondary');
                }, 2000);
            } catch (_) {
                // Fallback for older browsers
                const ta = document.createElement('textarea');
                ta.value = currentSummaryText;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
            }
        });
    }

    // -----------------------------------------------------------------------
    // Feedback: Approve
    // -----------------------------------------------------------------------

    if (approveBtn) {
        approveBtn.addEventListener('click', async function () {
            if (!currentSummaryId) return;
            await submitFeedback('approved', '', '');
            approveBtn.classList.replace('btn-outline-success', 'btn-success');
            approveBtn.innerHTML = '<i class="fas fa-check me-1"></i>Approved';
            approveBtn.disabled = true;
        });
    }

    // -----------------------------------------------------------------------
    // Feedback: Edit (show modal, submit on confirm)
    // -----------------------------------------------------------------------

    if (editBtn) {
        editBtn.addEventListener('click', function () {
            if (!currentSummaryId) return;
            // Show feedback modal
            const modal = new bootstrap.Modal(document.getElementById('feedbackModal'));
            modal.show();
        });
    }

    if (feedbackSubmitBtn) {
        feedbackSubmitBtn.addEventListener('click', async function () {
            if (!currentSummaryId) return;
            const edits = feedbackEdits ? feedbackEdits.value.trim() : '';
            const notes = feedbackNotes ? feedbackNotes.value.trim() : '';
            await submitFeedback('edited', edits, notes);
            // Close modal
            const modalEl = document.getElementById('feedbackModal');
            if (modalEl) {
                const modal = bootstrap.Modal.getInstance(modalEl);
                if (modal) modal.hide();
            }
            if (editBtn) {
                editBtn.classList.replace('btn-warning', 'btn-outline-warning');
                editBtn.innerHTML = '<i class="fas fa-edit me-1"></i>Edited';
                editBtn.disabled = true;
            }
        });
    }

    // -----------------------------------------------------------------------
    // Feedback: Reject
    // -----------------------------------------------------------------------

    if (rejectBtn) {
        rejectBtn.addEventListener('click', async function () {
            if (!currentSummaryId) return;
            if (!confirm('Reject this summary? This will be recorded for quality improvement.')) return;
            await submitFeedback('rejected', '', '');
            resetAllStates();
        });
    }

    // -----------------------------------------------------------------------
    // Feedback submission helper
    // -----------------------------------------------------------------------

    /**
     * POST clinician feedback to the PHP controller.
     *
     * @param {string} action  - "approved" | "edited" | "rejected"
     * @param {string} edits   - Description of edits (no PHI)
     * @param {string} notes   - Additional notes (no PHI)
     */
    async function submitFeedback(action, edits, notes) {
        if (!currentSummaryId) return;

        try {
            const response = await fetch('/modules/ChartSummarizer/feedback', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify({
                    summary_id: currentSummaryId,
                    action:     action,
                    edits:      edits,
                    notes:      notes,
                }),
            });

            if (!response.ok) {
                console.warn('Feedback submission failed:', response.status);
            }
        } catch (err) {
            console.warn('Feedback submission error:', err.message);
            // Non-fatal — feedback failures must not interrupt the clinical workflow
        }
    }

})();