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
 *   GET  /modules/ChartSummarizer            → indexAction()     (summary viewer)
 *   POST /modules/ChartSummarizer/summarize  → summarizeAction() (trigger generation)
 *   GET  /modules/ChartSummarizer/settings   → settingsAction()  (admin config)
 *
 * Authentication to the Python microservice uses a shared Bearer token read
 * from the CHART_SUMMARIZER_API_KEY environment variable — the same secret
 * configured as AGENT_API_KEY in the Python agent's .env file.
 *
 * ACL checks use OpenEMR's AclMain::aclCheckCore().  A failing ACL check
 * returns a 403 ViewModel/JsonModel without throwing.
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
     * Accept a POST request to generate a chart summary via the Python agent.
     *
     * Expected POST fields:
     *   patient_id           string  (required) — OpenEMR PID
     *   specialty            string  (optional, default: primary_care)
     *   requested_sections   array   (optional, default: all sections)
     *
     * Returns a JSON-encoded SummaryResponse from the Python agent, or a JSON
     * error object with an "error" key and an HTTP "code" on failure.
     *
     * ACL required: patients / docs.
     *
     * @return JsonModel
     */
    public function summarizeAction(): JsonModel
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
        $patientId = trim((string) ($post['patient_id'] ?? ''));
        $specialty = trim((string) ($post['specialty'] ?? 'primary_care'));

        if ($patientId === '') {
            $this->getResponse()->setStatusCode(400);
            return new JsonModel(['error' => 'patient_id is required.', 'code' => 400]);
        }

        // Default to all supported sections when none specified
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
            'requested_sections'     => $sections,
            'requesting_provider_id' => $_SESSION['authUserID'] ?? null,
        ];

        try {
            $response = $this->callMicroservice('/summarize', $payload);
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
     *
     * @param string $section ACL section (e.g. 'patients', 'admin').
     * @param string $value   ACL value   (e.g. 'docs', 'super').
     *
     * @return bool True if the current user has the requested permission.
     */
    private function checkAcl(string $section, string $value): bool
    {
        return (bool) AclMain::aclCheckCore($section, $value);
    }

    /**
     * Forward a JSON request to the Python microservice via cURL.
     *
     * Sends ``Authorization: Bearer <key>`` when CHART_SUMMARIZER_API_KEY is
     * set in the environment.  The key must match AGENT_API_KEY configured in
     * the Python agent.
     *
     * @param string               $path    API path, e.g. '/summarize'.
     * @param array<string, mixed> $payload JSON-serialisable request body.
     *
     * @return array<string, mixed> Decoded JSON response body.
     *
     * @throws \RuntimeException On cURL failure, timeout, auth error, or
     *                           non-2xx response code.
     * @throws \JsonException    If the response body is not valid JSON.
     */
    private function callMicroservice(string $path, array $payload): array
    {
        $baseUrl  = rtrim((string) ($this->moduleConfig['agent_base_url'] ?? 'http://chart-summarizer-agent:8000'), '/');
        $url      = $baseUrl . $path;
        $timeout  = (int) ($this->moduleConfig['request_timeout_s'] ?? 30);
        $apiKey   = (string) (getenv('CHART_SUMMARIZER_API_KEY') ?: '');

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
            // Do not fail on HTTP 4xx/5xx — we inspect the code ourselves
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
     * OpenEMR stores module settings in the globals table, accessible at runtime
     * via $GLOBALS['chart_summarizer_<key>'].
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
     * Uses OpenEMR's built-in sqlQuery / sqlStatement helpers which are always
     * available in the global scope within an OpenEMR request.
     *
     * @param array<string, mixed> $settings Key-value pairs to persist.
     */
    private function saveModuleSettings(array $settings): void
    {
        foreach ($settings as $key => $value) {
            $globalKey    = 'chart_summarizer_' . $key;
            $storedValue  = is_bool($value) ? (int) $value : $value;

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

            // Keep in-memory globals in sync for the current request
            $GLOBALS[$globalKey] = $value;
        }
    }

    /**
     * Return a 403 ViewModel for access-denied scenarios.
     *
     * @return ViewModel
     */
    private function accessDenied(): ViewModel
    {
        $this->getResponse()->setStatusCode(403);
        return new ViewModel(['error' => 'Access denied.']);
    }
}
