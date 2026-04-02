<?php
/**
 * Patch: crossrefReferenceLinking plugin — display DOIs without credentials.
 *
 * The pkp/crossrefReferenceLinking plugin only registers its display hook
 * (Templates::Article::Details::Reference) when Crossref credentials are
 * configured. This means matched DOIs won't render on article pages unless
 * the Crossref export plugin has username/password set.
 *
 * This patch moves the display hook registration before the credentials
 * check, so DOIs display regardless. Credentials are still required for
 * depositing references and polling getResolvedRefs.
 *
 * Apply by adding to docker/ojs/Dockerfile or entrypoint.
 *
 * In CrossrefReferenceLinkingPlugin.php register():
 *
 * BEFORE (line ~68):
 *   if (!$this->hasCrossrefCredentials(...) || !$this->citationsEnabled(...)) {
 *       return true;
 *   }
 *   ...
 *   Hook::add('Templates::Article::Details::Reference', [$this, 'displayReferenceDOI']);
 *
 * AFTER:
 *   Hook::add('Templates::Article::Details::Reference', [$this, 'displayReferenceDOI']);
 *   if (!$this->hasCrossrefCredentials(...) || !$this->citationsEnabled(...)) {
 *       return true;
 *   }
 */
