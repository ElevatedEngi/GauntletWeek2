<?php

/**
 * SummarizerController — handles HTTP requests for the Chart Summarizer module.
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
 */

declare(strict_types=1);

namespace OpenEMR\Modules\ChartSummarizer\Controller;

use Laminas\Mvc\Controller\AbstractActionController;
use Laminas\View\Model\JsonModel;
use Laminas\View\Model\ViewModel;
use OpenEMR\Common\Acl\AclMain;

/**
 * Controller for the Chart Summarizer module UI.
 *
 * Routes:
 *   GET  /modules/ChartSummarizer              → indexAction()      (summary viewer)
 *   POST /modules/ChartSummarizer/generate     → generateAction()   (trigger generation)
 *   POST /modules/ChartSummarizer/feedback     → feedbackAction()   (submit feedback)
 *   GET  /modules/ChartSummarizer/settings     → settingsAction()   (admin config)
 *
 * All calls to the Python microservice use the versioned /api/v1/ prefix and
 * forward a Bearer token (CHART_SUMMARIZER_API_KEY) for authentication.
 */
class SummarizerController extends AbstractActionController
{
    /** @var array<string, mixed> Module runtime configuration. */
    private array $moduleConfig;

    public function __construct()
    {
        $this->moduleConfig = $this->loadModuleConfig();
    }

    // -----------------------------------------------------------------------
    // Public actions
    // -----------------------------------------------------------------------

    /**
     * Display the chart summary viewer for a specific patient.
     *
     * Expects query param: ?pid=<OpenEMR PID>
     * ACL required: patients / docs (chart read access).
     *
     * @return ViewModel
     */
    public function indexAction(): ViewModel
    {
        if (!$this->checkAcl('patients', 'docs')) {
            return $this->accessDenied();
        }

        $pid = (int) $this->params()->fromQuery('pid', 0);
        if ($pid <= 0) {
            return new ViewModel(['error' => 'No patient selected. Please open a patient chart first.']);
        }

        return new ViewModel([
            'pid'              => $pid,
            'showDisclaimer'   => (bool) ($this->moduleConfig['show_disclaimer'] ?? true),
            'allowSaveToChart' => (bool) ($this->moduleConfig['allow_save_to_chart'] ?? false),
        ]);
    }

    /**
     * Generate a chart summary via the Python agent.
     *
     * Called from POST /modules/ChartSummarizer/generate
     *
     * Expected POST fields:
     *   patient_id           string  (required) — OpenEMR PID
     *   specialty            string  (optional, default: primary_care)
     *   date_range_months    int     (optional, default: 12) — look-back window
     *   requested_sections   array   (optional, default: all sections)
     *
     * Forwards the logged-in user's session token to the Python agent for auth.
     * Returns JSON-encoded SummaryResponse, or an error object on failure.
     *
     * ACL required: patients / docs.
     *
     * @return JsonModel
     */
    public function generateAction(): JsonModel
    {
        if (!$this->getRequest()->isPost()) {
            $this->getResponse()->setStatusCode(405);
            return new JsonModel(['error' => 'Method not allowed.', 'code' => 405]);
        }

        if (!$this->checkAcl('patients', 'docs')) {
            $this->getResponse()->setStatusCode(403);
            return new JsonModel(['error' => 'Access denied.', 'code' => 403]);
        }

        $post             = $this->getRequest()->getPost();
        $patientId        = trim((string) ($post['patient_id'] ?? ''));
        $specialty        = trim((string) ($post['specialty'] ?? 'primary_care'));
        $dateRangeMonths  = max(0, min(120, (int) ($post['date_range_months'] ?? 12)));

        if ($patientId === '') {
            $this->getResponse()->setStatusCode(400);
            return new JsonModel(['error' => 'patient_id is required.', 'code' => 400]);
        }

        /** @var string[]|mixed $rawSections */
        $rawSections = $post['requested_sections'] ?? null;
        $sections    = (is_array($rawSections) && !empty($rawSections))
            ? $rawSections
            : [
                'demographics', 'conditions', 'medications', 'allergies',
                'labs', 'vitals', 'encounters', 'immunizations', 'procedures',
            ];

        $payload = [
            'patient_id'             => $patientId,
            'specialty'              => $specialty,
            'date_range_months'      => $dateRangeMonths,
            'requested_sections'     => $sections,
            'requesting_provider_id' => $_SESSION['authUserID'] ?? null,
        ];

        // Forward the OpenEMR session token so the Python agent can validate it
        // via OAuth2 introspection. Falls back to the shared AGENT_API_KEY.
        $sessionToken = $_SESSION['authToken'] ?? '';
        try {
            $response = $this->callMicroservice('/api/v1/summarize', $payload, $sessionToken);
        } catch (\RuntimeException $e) {
            $this->getResponse()->setStatusCode(502);
            return new JsonModel(['error' => $e->getMessage(), 'code' => 502]);
        }

        return new JsonModel($response);
    }

    /**
     * Submit clinician feedback on a generated summary.
     *
     * Expected POST fields:
     *   summary_id  string  (required) — the summary UUID from metadata.request_id
     *   action      string  (required) — "approved" | "edited" | "rejected"
     *   edits       string  (optional) — description of edits made (no PHI)
     *   notes       string  (optional) — free-text notes (no PHI)
     *
     * ACL required: patients / docs.
     *
     * @return JsonModel
     */
    public function feedbackAction(): JsonModel
    {
        if (!$this->getRequest()->isPost()) {
            $this->getResponse()->setStatusCode(405);
            return new JsonModel(['error' => 'Method not allowed.', 'code' => 405]);
        }

        if (!$this->checkAcl('patients', 'docs')) {
            $this->getResponse()->setStatusCode(403);
            return new JsonModel(['error' => 'Access denied.', 'code' => 403]);
        }

        $post      = $this->getRequest()->getPost();
        $summaryId = trim((string) ($post['summary_id'] ?? ''));
        $action    = trim((string) ($post['action'] ?? ''));

        if ($summaryId === '') {
            $this->getResponse()->setStatusCode(400);
            return new JsonModel(['error' => 'summary_id is required.', 'code' => 400]);
        }

        $validActions = ['approved', 'edited', 'rejected'];
        if (!in_array($action, $validActions, true)) {
            $this->getResponse()->setStatusCode(400);
            return new JsonModel([
                'error' => 'action must be one of: ' . implode(', ', $validActions),
                'code'  => 400,
            ]);
        }

        $payload = [
            'action' => $action,
            'edits'  => trim((string) ($post['edits'] ?? '')),
            'notes'  => trim((string) ($post['notes'] ?? '')),
        ];

        try {
            $response = $this->callMicroservice('/api/v1/summarize/' . $summaryId . '/feedback', $payload);
        } catch (\RuntimeException $e) {
            $this->getResponse()->setStatusCode(502);
            return new JsonModel(['error' => $e->getMessage(), 'code' => 502]);
        }

        return new JsonModel($response);
    }

    /**
     * Render the module settings page (admin-only).
     *
     * GET: render the settings form with current values.
     * POST: save updated settings to the OpenEMR globals table; return JSON.
     *
     * ACL required: admin / super.
     *
     * @return ViewModel|JsonModel
     */
    public function settingsAction(): ViewModel|JsonModel
    {
        if (!$this->checkAcl('admin', 'super')) {
            $this->getResponse()->setStatusCode(403);
            return new ViewModel(['error' => 'Administrator access required.']);
        }

        if ($this->getRequest()->isPost()) {
            $post = $this->getRequest()->getPost();
            $this->saveModuleSettings([
                'agent_base_url'      => trim((string) ($post['agent_base_url'] ?? '')),
                'request_timeout_s'   => max(5, min(120, (int) ($post['request_timeout_s'] ?? 30))),
                'rate_limit_per_hour' => max(1, min(200, (int) ($post['rate_limit_per_hour'] ?? 20))),
                'show_disclaimer'     => (bool) ($post['show_disclaimer'] ?? true),
                'allow_save_to_chart' => (bool) ($post['allow_save_to_chart'] ?? false),
            ]);
            return new JsonModel(['success' => true]);
        }

        return new ViewModel(['config' => $this->moduleConfig]);
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    /**
     * Wrap AclMain::aclCheckCore() and return a plain bool.
     */
    private function checkAcl(string $section, string $value): bool
    {
        return (bool) AclMain::aclCheckCore($section, $value);
    }

    /**
     * Forward a JSON request to the Python microservice via cURL.
     *
     * Auth precedence:
     *  1. If $sessionToken is provided (OpenEMR OAuth2 token), forward it so
     *     the Python agent can validate via introspection.
     *  2. Otherwise fall back to the shared CHART_SUMMARIZER_API_KEY.
     *
     * @param string               $path         API path, e.g. '/api/v1/summarize'.
     * @param array<string, mixed> $payload      JSON-serialisable request body.
     * @param string               $sessionToken Optional OpenEMR OAuth2 token to forward.
     *
     * @return array<string, mixed> Decoded JSON response body.
     *
     * @throws \RuntimeException On cURL failure, timeout, auth error, or
     *                           non-2xx response code.
     * @throws \JsonException    If the response body is not valid JSON.
     */
    private function callMicroservice(string $path, array $payload, string $sessionToken = ''): array
    {
        $baseUrl  = rtrim((string) ($this->moduleConfig['agent_base_url'] ?? 'http://chart-summarizer-agent:8000'), '/');
        $url      = $baseUrl . $path;
        $timeout  = (int) ($this->moduleConfig['request_timeout_s'] ?? 30);
        // Prefer the forwarded session token; fall back to the shared API key.
        $apiKey   = $sessionToken !== '' ? $sessionToken : (string) (getenv('CHART_SUMMARIZER_API_KEY') ?: '');

        $jsonBody = json_encode($payload, JSON_THROW_ON_ERROR);

        $ch = curl_init($url);
        if ($ch === false) {
            throw new \RuntimeException('Failed to initialise cURL handle.');
        }

        $headers = [
            'Content-Type: application/json',
            'Accept: application/json',
        ];
        if ($apiKey !== '') {
            $headers[] = 'Authorization: Bearer ' . $apiKey;
        }

        curl_setopt_array($ch, [
            CURLOPT_POST           => true,
            CURLOPT_POSTFIELDS     => $jsonBody,
            CURLOPT_HTTPHEADER     => $headers,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_TIMEOUT        => $timeout,
            CURLOPT_CONNECTTIMEOUT => 10,
            CURLOPT_FAILONERROR    => false,
        ]);

        $rawResponse = curl_exec($ch);
        $httpCode    = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $curlError   = curl_error($ch);
        curl_close($ch);

        if ($rawResponse === false) {
            throw new \RuntimeException(
                sprintf('Microservice request failed (cURL): %s', $curlError ?: 'unknown error')
            );
        }

        if ($httpCode === 401) {
            throw new \RuntimeException(
                'Microservice authentication failed (401). '
                . 'Check that CHART_SUMMARIZER_API_KEY matches AGENT_API_KEY.'
            );
        }

        if ($httpCode === 429) {
            throw new \RuntimeException('Rate limit exceeded on the chart summarizer service.');
        }

        if ($httpCode === 504) {
            throw new \RuntimeException('Chart summarizer service timed out generating the summary.');
        }

        if ($httpCode >= 400) {
            throw new \RuntimeException(
                sprintf('Microservice returned HTTP %d error.', $httpCode)
            );
        }

        /** @var array<string, mixed>|null $decoded */
        $decoded = json_decode((string) $rawResponse, true, 512, JSON_THROW_ON_ERROR);
        if (!is_array($decoded)) {
            throw new \RuntimeException('Microservice returned a non-object JSON response.');
        }

        return $decoded;
    }

    /**
     * Build the module configuration from defaults, overridden by OpenEMR globals.
     *
     * @return array<string, mixed>
     */
    private function loadModuleConfig(): array
    {
        $defaults = [
            'agent_base_url'      => 'http://chart-summarizer-agent:8000',
            'request_timeout_s'   => 30,
            'rate_limit_per_hour' => 20,
            'show_disclaimer'     => true,
            'allow_save_to_chart' => false,
        ];

        foreach (array_keys($defaults) as $key) {
            $globalKey = 'chart_summarizer_' . $key;
            if (array_key_exists($globalKey, $GLOBALS)) {
                $defaults[$key] = $GLOBALS[$globalKey];
            }
        }

        return $defaults;
    }

    /**
     * Persist module settings to the OpenEMR globals table.
     *
     * @param array<string, mixed> $settings Key-value pairs to persist.
     */
    private function saveModuleSettings(array $settings): void
    {
        foreach ($settings as $key => $value) {
            $globalKey   = 'chart_summarizer_' . $key;
            $storedValue = is_bool($value) ? (int) $value : $value;

            $existing = sqlQuery(
                'SELECT gl_value FROM globals WHERE gl_name = ?',
                [$globalKey]
            );

            if ($existing) {
                sqlStatement(
                    'UPDATE globals SET gl_value = ? WHERE gl_name = ?',
                    [$storedValue, $globalKey]
                );
            } else {
                sqlStatement(
                    'INSERT INTO globals (gl_name, gl_value) VALUES (?, ?)',
                    [$globalKey, $storedValue]
                );
            }

            $GLOBALS[$globalKey] = $value;
        }
    }

    /**
     * Return a 403 ViewModel for access-denied scenarios.
     */
    private function accessDenied(): ViewModel
    {
        $this->getResponse()->setStatusCode(403);
        return new ViewModel(['error' => 'Access denied.']);
    }
}