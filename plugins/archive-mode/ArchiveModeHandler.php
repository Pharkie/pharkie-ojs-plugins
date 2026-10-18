<?php

/**
 * Stands in for every route archive mode closes (ArchiveModeRoutes::isClosed):
 * sign-in, registration, password resets, the profile, subscriptions and
 * payments. Whatever was asked for, GET or POST, it does nothing and shows
 * one message with a link to the journal's new home.
 */

namespace APP\plugins\generic\archiveMode;

use APP\handler\Handler;
use APP\template\TemplateManager;

class ArchiveModeHandler extends Handler
{
    public function __construct(private ArchiveModePlugin $plugin)
    {
        parent::__construct();
    }

    public function closed($args, $request)
    {
        $this->setupTemplate($request);
        [$kind, $url] = $this->plugin->destination($request);
        $site = ArchiveModeRoutes::siteName(ArchiveModePlugin::movedTo());

        $esc = fn (string $s) => htmlspecialchars($s, ENT_QUOTES, 'UTF-8');
        $message = '<p>' . $esc(__('plugins.generic.archiveMode.closed.text')) . '</p>'
            . '<p>' . $esc(__('plugins.generic.archiveMode.closed.moved', ['site' => $site])) . '</p>'
            . '<p><a class="archive-mode-closed__link" href="' . $esc($url) . '">' . $esc($this->plugin->linkLabel($kind)) . '</a></p>';

        header('X-Robots-Tag: noindex');
        $templateMgr = TemplateManager::getManager($request);
        $templateMgr->addHeader('archiveModeNoindex', '<meta name="robots" content="noindex">');
        $templateMgr->assign([
            'pageTitle' => 'plugins.generic.archiveMode.closed.title',
            'messageTranslated' => $message,
        ]);
        $templateMgr->display('frontend/pages/message.tpl');
    }
}
