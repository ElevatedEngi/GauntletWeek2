<?php

/**
 * Module configuration for the ChartSummarizer module.
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
 * @return array<string, mixed> Laminas module configuration array.
 */

declare(strict_types=1);

return [
    // ------------------------------------------------------------------
    // Router
    // ------------------------------------------------------------------
    'router' => [
        'routes' => [
            'chart-summarizer' => [
                'type'    => \Laminas\Router\Http\Literal::class,
                'options' => [
                    'route'    => '/modules/ChartSummarizer',
                    'defaults' => [
                        'controller' => \OpenEMR\Modules\ChartSummarizer\Controller\SummarizerController::class,
                        'action'     => 'index',
                    ],
                ],
                'may_terminate' => true,
                'child_routes'  => [
                    'generate' => [
                        'type'    => \Laminas\Router\Http\Literal::class,
                        'options' => [
                            'route'    => '/generate',
                            'defaults' => ['action' => 'generate'],
                        ],
                    ],
                    'feedback' => [
                        'type'    => \Laminas\Router\Http\Literal::class,
                        'options' => [
                            'route'    => '/feedback',
                            'defaults' => ['action' => 'feedback'],
                        ],
                    ],
                    'settings' => [
                        'type'    => \Laminas\Router\Http\Literal::class,
                        'options' => [
                            'route'    => '/settings',
                            'defaults' => ['action' => 'settings'],
                        ],
                    ],
                ],
            ],
        ],
    ],

    // ------------------------------------------------------------------
    // Controllers
    // ------------------------------------------------------------------
    'controllers' => [
        'factories' => [
            \OpenEMR\Modules\ChartSummarizer\Controller\SummarizerController::class
                => \Laminas\ServiceManager\Factory\InvokableFactory::class,
        ],
    ],

    // ------------------------------------------------------------------
    // View manager
    // ------------------------------------------------------------------
    'view_manager' => [
        'template_path_stack' => [
            __DIR__ . '/../templates',
        ],
    ],

    // ------------------------------------------------------------------
    // Module-specific settings (stored in OpenEMR globals table)
    // ------------------------------------------------------------------
    'chart_summarizer' => [
        // Python microservice endpoint (internal network)
        'agent_base_url'       => 'http://chart-summarizer-agent:8000',

        // Default request timeout in seconds
        'request_timeout_s'    => 30,

        // Maximum summaries per user per hour (abuse prevention)
        'rate_limit_per_hour'  => 20,

        // Show AI-generated disclaimer on all summaries
        'show_disclaimer'      => true,

        // Allow clinicians to save approved summaries to the chart
        'allow_save_to_chart'  => false, // TODO: Enable after audit trail is complete
    ],
];
