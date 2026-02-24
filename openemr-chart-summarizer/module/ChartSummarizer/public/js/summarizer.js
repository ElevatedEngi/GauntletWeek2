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
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 *
 * Responsibilities:
 *  - Submit the summary generation form via fetch() POST
 *  - Display loading state while waiting for the response
 *  - Render the Markdown summary as HTML
 *  - Show the confidence level banner
 *  - Handle approve / flag / reject / feedback actions
 */

/* global fetch, marked */

'use strict';

(function () {

    // -----------------------------------------------------------------------
    // DOM references
    // -----------------------------------------------------------------------

    const form            = document.getElementById('summary-request-form');
    const generateBtn     = document.getElementById('generate-btn');
    const generateBtnText = document.getElementById('generate-btn-text');
    const generateSpinner = document.getElementById('generate-btn-spinner');
    const summaryOutput   = document.getElementById('summary-output');
    const summaryContent  = document.getElementById('summary-content');
    const summaryMeta     = document.getElementById('summary-meta');
    const confidenceBanner = document.getElementById('confidence-banner');
    const summaryError    = document.getElementById('summary-error');
    const approveBtn      = document.getElementById('approve-btn');
    const flagBtn         = document.getElementById('flag-btn');
    const rejectBtn       = document.getElementById('reject-btn');
    const feedbackBtn     = document.getElementById('feedback-btn');

    // -----------------------------------------------------------------------
    // State
    // -----------------------------------------------------------------------

    /** @type {string|null} */
    let currentRequestId = null;

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
     * Submit the summary request to the PHP controller, which proxies to the
     * Python microservice.
     *
     * TODO: Replace the placeholder URL with the actual OpenEMR module route.
     * TODO: Add CSRF token header once OpenEMR CSRF integration is in place.
     */
    async function generateSummary() {
        setLoadingState(true);
        hideError();
        hideSummary();

        const formData = new FormData(form);
        const payload = {
            patient_pid: formData.get('patient_pid'),
            specialty:   formData.get('specialty'),
            months:      parseInt(formData.get('months') || '12', 10),
        };

        try {
            // TODO: Update URL to the actual module route when implemented
            const response = await fetch('/modules/ChartSummarizer/summarize', {
                method:  'POST',
                headers: { 'Content-Type': 'application/json' },
                body:    JSON.stringify(payload),
            });

            if (!response.ok) {
                const err = await response.json().catch(() => ({}));
                throw new Error(err.detail || `HTTP ${response.status}`);
            }

            const data = await response.json();
            renderSummary(data);

        } catch (err) {
            showError(err.message || 'An unexpected error occurred.');
        } finally {
            setLoadingState(false);
        }
    }

    // -----------------------------------------------------------------------
    // Rendering helpers
    // -----------------------------------------------------------------------

    /**
     * Render the summary response object in the UI.
     *
     * @param {Object} data - SummaryResponse JSON from the Python service.
     */
    function renderSummary(data) {
        currentRequestId = data.metadata?.request_id || null;

        // Confidence banner
        const level = data.confidence_level || 'RED';
        const levelLabels = {
            GREEN:  'High Confidence — All claims verified',
            YELLOW: 'Moderate Confidence — Some data gaps noted',
            RED:    'Low Confidence — Manual review required',
        };
        confidenceBanner.className = `alert confidence-${level.toLowerCase()} mb-3`;
        confidenceBanner.textContent = levelLabels[level] || level;

        // Summary text (render Markdown if 'marked' library is available)
        const rawMarkdown = data.summary_text || '';
        if (typeof marked !== 'undefined') {
            summaryContent.innerHTML = marked.parse(rawMarkdown);
        } else {
            summaryContent.textContent = rawMarkdown;
        }

        // Metadata
        const meta = data.metadata || {};
        summaryMeta.textContent = [
            meta.model_used,
            meta.latency_ms ? `${meta.latency_ms}ms` : '',
        ].filter(Boolean).join(' · ');

        summaryOutput.classList.remove('d-none');
    }

    function setLoadingState(loading) {
        generateBtn.disabled       = loading;
        generateBtnText.textContent = loading ? 'Generating…' : 'Generate Summary';
        generateSpinner.classList.toggle('d-none', !loading);
    }

    function showError(message) {
        summaryError.textContent = message;
        summaryError.classList.remove('d-none');
    }

    function hideError() {
        summaryError.classList.add('d-none');
    }

    function hideSummary() {
        summaryOutput.classList.add('d-none');
    }

    // -----------------------------------------------------------------------
    // Action buttons (approve / flag / reject / feedback)
    // TODO: Implement each action with appropriate fetch() calls
    // -----------------------------------------------------------------------

    if (approveBtn) {
        approveBtn.addEventListener('click', function () {
            // TODO: POST to /modules/ChartSummarizer/approve with currentRequestId
            throw new Error('Approve action not yet implemented.');
        });
    }

    if (flagBtn) {
        flagBtn.addEventListener('click', function () {
            // TODO: POST to /modules/ChartSummarizer/flag with currentRequestId
            throw new Error('Flag action not yet implemented.');
        });
    }

    if (rejectBtn) {
        rejectBtn.addEventListener('click', function () {
            currentRequestId = null;
            hideSummary();
        });
    }

    if (feedbackBtn) {
        feedbackBtn.addEventListener('click', function () {
            // TODO: Show feedback modal and POST to /modules/ChartSummarizer/feedback
            throw new Error('Feedback action not yet implemented.');
        });
    }

})();
