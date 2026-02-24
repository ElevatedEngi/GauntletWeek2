<?php

/**
 * ChartSummarizer Module Entry Point
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

namespace OpenEMR\Modules\ChartSummarizer;

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Menu\MenuEvent;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

/**
 * Zend/Laminas module entry point for the Chart Summarizer module.
 *
 * Registered in OpenEMR's module system. Responsible for:
 * - Declaring module metadata
 * - Registering event listeners (menu injection, hooks)
 * - Loading module-specific autoloading
 */
class Module
{
    /**
     * Module identifier — must be unique across all installed modules.
     */
    public const MODULE_NAME = 'ChartSummarizer';

    /**
     * Module display name shown in the OpenEMR modules manager.
     */
    public const MODULE_DISPLAY_NAME = 'AI Patient Chart Summarizer';

    /**
     * Minimum OpenEMR version required for this module.
     */
    public const MINIMUM_OPENEMR_VERSION = '8.0.0';

    /**
     * Bootstrap the module within the OpenEMR application lifecycle.
     *
     * Called by OpenEMR's module loader when the module is enabled.
     * Register event listeners, menu items, and any startup logic here.
     *
     * @param EventDispatcherInterface $eventDispatcher OpenEMR event bus.
     * @param ModulesClassLoader       $loader          Module class autoloader.
     *
     * @TODO Register a listener on MenuEvent to inject the "Chart Summary"
     *       menu item under Patient → Clinical.
     * @TODO Register the module's route prefix with OpenEMR's router.
     */
    public function bootstrap(
        EventDispatcherInterface $eventDispatcher,
        ModulesClassLoader $loader
    ): void {
        // Register this module's namespace with the autoloader
        $loader->registerNamespaceIfNotExists(
            'OpenEMR\\Modules\\ChartSummarizer\\',
            __DIR__ . '/src/'
        );

        // TODO: Register menu event listener
        // $eventDispatcher->addListener(MenuEvent::MENU_UPDATE, [$this, 'onMenuUpdate']);
    }

    /**
     * Return module metadata used by the OpenEMR modules manager.
     *
     * @return array<string, string> Associative array of module metadata.
     */
    public function getModuleInfo(): array
    {
        return [
            'name'            => self::MODULE_NAME,
            'display_name'    => self::MODULE_DISPLAY_NAME,
            'description'     => 'AI-powered patient chart summarization using Claude/GPT. '
                               . 'Generates clinician-reviewed draft summaries from FHIR data.',
            'version'         => '0.1.0',
            'author'          => 'OpenEMR Community',
            'license'         => 'GPL-3.0',
            'min_oemr_version' => self::MINIMUM_OPENEMR_VERSION,
        ];
    }
}
