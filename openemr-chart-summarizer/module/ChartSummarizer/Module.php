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
 * - Registering event listeners (menu injection)
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
     *
     * @param EventDispatcherInterface $eventDispatcher OpenEMR event bus.
     * @param ModulesClassLoader       $loader          Module class autoloader.
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

        // Inject "AI Chart Summary" under Patient menu
        $eventDispatcher->addListener(
            MenuEvent::MENU_UPDATE,
            [$this, 'onMenuUpdate']
        );
    }

    /**
     * Add the "AI Chart Summary" menu item under Patient → Clinical.
     *
     * @param MenuEvent $event OpenEMR menu update event.
     */
    public function onMenuUpdate(MenuEvent $event): void
    {
        $menu = $event->getMenu();

        // Build the menu item definition
        $menuItem = new \stdClass();
        $menuItem->requirement = 0;
        $menuItem->target = 'main';
        $menuItem->menu_id = 'chart_summarizer';
        $menuItem->label = xlt('AI Chart Summary');
        $menuItem->url = '/modules/ChartSummarizer/index?pid=';
        $menuItem->appendPid = true;
        $menuItem->icon = 'fas fa-brain';
        $menuItem->aclSection = 'patients';
        $menuItem->aclValue = 'docs';

        // Find the "Patient" top-level section and append under it
        foreach ($menu as $menuSection) {
            if ($menuSection->menu_id === 'patientMenu') {
                $menuSection->children[] = $menuItem;
                break;
            }
        }

        $event->setMenu($menu);
    }

    /**
     * Return module metadata used by the OpenEMR modules manager.
     *
     * @return array<string, string> Associative array of module metadata.
     */
    public function getModuleInfo(): array
    {
        return [
            'name'             => self::MODULE_NAME,
            'display_name'     => self::MODULE_DISPLAY_NAME,
            'description'      => 'AI-powered patient chart summarization using Claude/GPT. '
                                . 'Generates clinician-reviewed draft summaries from FHIR data.',
            'version'          => '0.1.0',
            'author'           => 'OpenEMR Community',
            'license'          => 'GPL-3.0',
            'min_oemr_version' => self::MINIMUM_OPENEMR_VERSION,
        ];
    }
}