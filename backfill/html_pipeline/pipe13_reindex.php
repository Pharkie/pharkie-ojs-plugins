<?php
/**
 * Dispatch a search-reindex job for specific submissions.
 *
 * The scoped companion to pipe13_patch_metadata.py: after a metadata patch,
 * only the changed submissions need reindexing — not the 1,400-article
 * rebuild that rebuildSearchIndex.php performs.
 *
 * Runs from stdin (no file needs copying into the container):
 *   docker compose exec -T ojs php -- 9821 9822 < pipe13_reindex.php
 *
 * Jobs land on the default queue; drain with jobs.php run (the python
 * wrapper does this automatically).
 */

use PKP\jobs\submissions\UpdateSubmissionSearchJob;

require('/var/www/html/tools/bootstrap.php');

array_shift($argv);  // script name ("Standard input code")
$queued = 0;
foreach ($argv as $arg) {
    $id = (int) $arg;
    if ($id <= 0) {
        continue;
    }
    dispatch(new UpdateSubmissionSearchJob($id));
    echo "queued reindex for submission {$id}\n";
    $queued++;
}
if ($queued === 0) {
    fwrite(STDERR, "usage: php -- <submissionId> [...] < pipe13_reindex.php\n");
    exit(1);
}
