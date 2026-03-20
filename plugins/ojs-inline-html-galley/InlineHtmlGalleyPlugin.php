<?php

/**
 * Inline HTML Galley Plugin
 *
 * Renders HTML galley content inline on article pages when the user has access
 * (open-access, active subscription, or completed purchase). Replaces the
 * separate full-text viewer link with inline content — no extra click needed.
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
     * Shows for any article the user has access to (open-access, subscription,
     * or purchased) that has an HTML galley labeled "Full Text".
     *
     * Uses OJS's own $hasAccess template var — already computed by
     * ArticleHandler with the full access logic (subscription, purchase,
     * open-access, domain-based access).
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

        // Only render inline if the user has access to this article.
        // $hasAccess is set by ArticleHandler — covers open-access,
        // active subscription, domain-based access, and completed purchases.
        if (!$templateMgr->getTemplateVars('hasAccess')) {
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

        // Archive quality notice — shown above inline content for digitised back-issues
        $archiveNotice = '<div style="margin-bottom:16px;padding:10px 14px;background:#f8f5f0;'
            . 'border:1px solid #e0d8cc;border-radius:4px;font-size:13px;color:#666;line-height:1.5;">'
            . 'This article has been digitally restored from print. If you spot any errors '
            . 'or formatting issues, please email '
            . '<a href="mailto:journal@existentialanalysis.org.uk">journal@existentialanalysis.org.uk</a>.'
            . '</div>';

        $output .= '<section class="item inline-html-galley">'
            . '<h2 class="label">Full Text</h2>'
            . $archiveNotice
            . '<div class="value">' . $bodyContent . '</div>'
            . '</section>';

        return Hook::CONTINUE;
    }

    /**
     * Hide galley links and add inline HTML styling.
     *
     * On article pages: only hides "Full Text" link if inline content was
     * rendered (detected by .inline-html-galley on the page). Users without
     * access still see galley links with purchase prices.
     *
     * On issue TOC / archive pages: hides ALL galley links (PDF, HTML,
     * Full Text). The article title links to the landing page where access
     * logic determines what the reader sees (inline HTML + PDF download
     * for subscribers, purchase prompt for non-subscribers).
     */
    public function hideHtmlGalleyLink(string $hookName, array $args): bool
    {
        $templateMgr = $args[0];
        $template = $args[1] ?? '';

        // Only inject on article, issue, archive, or homepage (current issue)
        if (!str_contains($template, 'article.tpl')
            && !str_contains($template, 'issue.tpl')
            && !str_contains($template, 'issueArchive.tpl')
            && !str_contains($template, 'indexJournal.tpl')) {
            return Hook::CONTINUE;
        }

        $templateMgr->addHeader('inline-html-galley-styles', '<style>
.inline-html-galley { margin-top: 2em; }
.inline-html-galley .value { line-height: 1.7; font-size: 15px; }
.inline-html-galley .value p { margin-bottom: 1em; }
</style>
<script>
document.addEventListener("DOMContentLoaded", function() {
    var isArticlePage = !!document.querySelector(".obj_article_details");
    var hasInlineContent = !!document.querySelector(".inline-html-galley");
    // Hide empty References section (OJS 3.5 bug: template renders it even with no citations)
    var refsSection = document.querySelector(".item.references");
    if (refsSection) {
        var refsValue = refsSection.querySelector(".value");
        if (refsValue && !refsValue.textContent.trim()) {
            refsSection.style.display = "none";
        }
    }
    document.querySelectorAll(".obj_galley_link").forEach(function(el) {
        var label = el.textContent.trim();
        if (!isArticlePage) {
            // Issue TOC / archive: hide all galley links (PDF, HTML, Full Text).
            // Readers click the article title to reach the landing page where
            // access logic shows inline HTML + PDF download (or purchase prompt).
            el.style.display = "none";
        } else if (label === "Full Text" && hasInlineContent) {
            // Article page: hide "Full Text" link only when inline content rendered
            el.style.display = "none";
        }
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
