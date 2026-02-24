<?php

/**
 * Module class map for the ChartSummarizer module.
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
 * @return array<string, string> Map of fully-qualified class names to file paths.
 */

declare(strict_types=1);

return [
    'OpenEMR\\Modules\\ChartSummarizer\\Module'
        => __DIR__ . '/Module.php',

    'OpenEMR\\Modules\\ChartSummarizer\\Controller\\SummarizerController'
        => __DIR__ . '/src/Controller/SummarizerController.php',
];
