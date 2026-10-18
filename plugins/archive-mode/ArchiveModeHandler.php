<?php

/**
 * Stands in for every route archive mode closes (ArchiveModeRoutes::isClosed):
 * sign-in, registration, password resets, the profile, subscriptions,
 * payments and the pages on submitting. Whatever was asked for, GET or POST,
 * it does nothing and shows one heading and one line. The bar above it
 * already says the journal has moved and carries the link there, so the
 * message repeats neither.
 */

namespace APP\plugins\generic\archiveMode;

use APP\handler\Handler;
use APP\template\TemplateManager;

class ArchiveModeHandler extends Handler
{
    public function closed($args, $request)
    {
        $this->setupTemplate($request);
        $message = '<p class="archive-mode-closed">'
            . htmlspecialchars(__('plugins.generic.archiveMode.closed.text'), ENT_QUOTES, 'UTF-8') . '</p>';

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
