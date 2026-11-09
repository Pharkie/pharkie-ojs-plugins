<?php
/**
 * Unpublish articles through OJS's own Repo::publication()->unpublish().
 *
 * The spec-mode companion to pipe13_patch_metadata.py ("unpublish": true).
 * OJS's path does what raw SQL would miss: the submission's status follows,
 * its DOIs are marked stale, an event is logged, and PublicationUnpublished
 * queues the search-index update. Nothing is deleted: the article stays on
 * its issue, out of view.
 *
 * Runs from stdin, like pipe13_reindex.php:
 *   docker compose exec -T ojs php -- [--confirm] 9100 9141 < pipe13_unpublish.php
 * Without --confirm it lists what it would unpublish.
 */

use APP\core\Application;
use APP\facades\Repo;
use APP\submission\Submission;
use PKP\cliTool\CommandLineTool;

// The CLI request has no journal in its path, and unpublish() resolves the
// context from the request; give it the journal's.
$_SERVER['PATH_INFO'] = '/ea';

require('/var/www/html/tools/bootstrap.php');

class UnpublishTool extends CommandLineTool
{
    public function execute(): int
    {
        $confirm = in_array('--confirm', $this->argv, true);
        $ids = array_values(array_filter(array_map('intval', $this->argv)));
        if (!$ids) {
            fwrite(STDERR, "usage: php -- [--confirm] <submissionId> [...] < pipe13_unpublish.php\n");
            return 1;
        }
        // unpublish() ends by reading the journal from the request; without it
        // the status is already flipped when it dies, before the search update.
        $context = Application::get()->getRequest()->getContext();
        if (!$context) {
            fwrite(STDERR, "ERROR: the CLI request has no journal context\n");
            return 1;
        }
        echo "journal context: {$context->getPath()}\n";
        foreach ($ids as $id) {
            $submission = Repo::submission()->get($id);
            if (!$submission) {
                echo "submission {$id}: not found\n";
                return 1;
            }
            $publication = $submission->getCurrentPublication();
            $title = substr((string) $publication->getLocalizedTitle(), 0, 60);
            $status = (int) $publication->getData('status');
            if ($status !== Submission::STATUS_PUBLISHED) {
                echo "submission {$id}: status {$status}, not published; skipped ({$title})\n";
                continue;
            }
            if (!$confirm) {
                echo "submission {$id}: would unpublish ({$title})\n";
                continue;
            }
            Repo::publication()->unpublish($publication);
            $after = (int) Repo::publication()->get($publication->getId())->getData('status');
            echo "submission {$id}: unpublished, status {$status} -> {$after} ({$title})\n";
        }
        return 0;
    }
}

$tool = new UnpublishTool($argv ?? []);
exit($tool->execute());
