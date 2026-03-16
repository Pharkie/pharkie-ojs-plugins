<?php

/**
 * Inline HTML Galley Plugin
 *
 * Renders HTML galley content inline on article pages for open-access articles
 * (e.g. editorials), replacing the separate full-text viewer link.
 *
 * Deploy to: plugins/generic/inlineHtmlGalley/ in OJS installation.
 * Requires OJS 3.5+.
 */

namespace APP\plugins\generic\inlineHtmlGalley;

use APP\core\Application;
use PKP\plugins\GenericPlugin;
use PKP\plugins\Hook;

class InlineHtmlGalleyPlugin extends GenericPlugin
{
    public function register($category, $path, $mainContextId = null)
    {
        $success = parent::register($category, $path, $mainContextId);

        if (!$success || !$this->getEnabled()) {
            return $success;
        }

        Hook::add('Templates::Article::Main', $this->renderInlineHtmlGalley(...));
        Hook::add('TemplateManager::display', $this->hideHtmlGalleyLink(...));

        return $success;
    }

    /**
     * Render HTML galley content inline on the article page.
     * Only for open-access articles that have an HTML galley labeled "Full Text".
     */
    public function renderInlineHtmlGalley(string $hookName, array $params): bool
    {
        $output = &$params[2];

        $request = Application::get()->getRequest();
        $context = $request->getContext();
        if (!$context) {
            return Hook::CONTINUE;
        }

        // Get the article from the template
        $templateMgr = $params[1];
        $article = $templateMgr->getTemplateVars('article');
        $publication = $templateMgr->getTemplateVars('publication');
        if (!$article || !$publication) {
            return Hook::CONTINUE;
        }

        // Only show for open-access articles (access_status = 1)
        if ((int) $publication->getData('accessStatus') !== 1) {
            return Hook::CONTINUE;
        }

        // Find an HTML galley labeled "Full Text"
        $galleys = $publication->getData('galleys');
        $htmlGalley = null;
        if ($galleys) {
            foreach ($galleys as $galley) {
                if ($galley->getLabel() === 'Full Text') {
                    $htmlGalley = $galley;
                    break;
                }
            }
        }

        if (!$htmlGalley) {
            return Hook::CONTINUE;
        }

        // Read the HTML file content
        $submissionFile = $htmlGalley->getFile();
        if (!$submissionFile) {
            return Hook::CONTINUE;
        }

        $file = app()->get('file')->fs->read($submissionFile->getData('path'));
        if (!$file) {
            return Hook::CONTINUE;
        }

        // Extract just the <body> content
        $bodyContent = $file;
        if (preg_match('/<body[^>]*>(.*?)<\/body>/is', $file, $matches)) {
            $bodyContent = $matches[1];
        }

        $bodyContent = trim($bodyContent);
        if (empty($bodyContent)) {
            return Hook::CONTINUE;
        }

        $output .= '<section class="item inline-html-galley">'
            . '<h2 class="label">Full Text</h2>'
            . '<div class="value">' . $bodyContent . '</div>'
            . '</section>';

        return Hook::CONTINUE;
    }

    /**
     * Hide "Full Text" galley links site-wide (article page sidebar + issue TOC).
     * The HTML content is rendered inline on article pages, so the separate
     * "Full Text" link is redundant. Uses JS to target by link text rather
     * than a generic CSS class (which would hide non-HTML galleys too).
     */
    public function hideHtmlGalleyLink(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        // Only inject on article, issue, or archive pages
        if (!str_contains($template, 'article.tpl')
            && !str_contains($template, 'issue.tpl')
            && !str_contains($template, 'issueArchive.tpl')) {
            return Hook::CONTINUE;
        }

        $templateMgr->addHeader('inline-html-galley-styles', '<style>
.inline-html-galley { margin-top: 2em; }
.inline-html-galley .value { line-height: 1.7; font-size: 15px; }
.inline-html-galley .value p { margin-bottom: 1em; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    document.querySelectorAll(".obj_galley_link").forEach(function(el) {
        if (el.textContent.trim() === "Full Text") el.style.display = "none";
    });
});
</script>');

        return Hook::CONTINUE;
    }

    public function getDisplayName()
    {
        return __('plugins.generic.inlineHtmlGalley.displayName');
    }

    public function getDescription()
    {
        return __('plugins.generic.inlineHtmlGalley.description');
    }
}
