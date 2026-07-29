<?php

/**
 * Assign DOIs to a newly imported issue and its articles.
 *
 * The journal is configured with doiCreationTime = "never", so OJS does not
 * mint DOIs on import. Back-issues did not need it -- they arrive with DOIs
 * already registered at Crossref, carried in the import XML. A NEW issue has
 * no DOIs at all, and this is the step that creates them.
 *
 * It calls OJS's own Repo::createDois(), so suffixes follow the journal's
 * configured pattern and match the rest of the archive. Both repository calls
 * skip anything that already has a DOI, so re-running is safe.
 *
 * Run inside the OJS container:
 *   docker compose exec -T ojs php /var/www/html/backfill-assign-dois.php 37 2
 *   ...or via the wrapper: backfill/html_pipeline/pipe11_assign_dois.sh 37.2
 *
 * Assigning is not depositing. Crossref deposit is a separate, deliberate
 * step from the DOIs page in the OJS admin (or the depositAll API route).
 */

use APP\facades\Repo;
use PKP\cliTool\CommandLineTool;
use PKP\db\DAORegistry;

require('/var/www/html/tools/bootstrap.php');

class AssignIssueDoisTool extends CommandLineTool
{
    public function __construct($argv = [])
    {
        parent::__construct($argv);
        if (count($this->argv) < 2) {
            $this->usage();
            exit(1);
        }
    }

    public function usage()
    {
        echo "Assign DOIs to an issue and all its articles.\n\n"
            . "Usage: php {$this->scriptName} <volume> <number> [--dry-run]\n";
    }

    public function execute()
    {
        [$volume, $number] = $this->argv;
        $dryRun = in_array('--dry-run', $this->argv, true);

        $contextDao = DAORegistry::getDAO('JournalDAO');
        $context = $contextDao->getByPath('ea');
        if (!$context) {
            echo "ERROR: journal 'ea' not found\n";
            exit(1);
        }

        $issue = null;
        foreach (Repo::issue()->getCollector()->filterByContextIds([$context->getId()])->getMany() as $candidate) {
            if ((string) $candidate->getVolume() === (string) $volume
                && (string) $candidate->getNumber() === (string) $number) {
                $issue = $candidate;
                break;
            }
        }
        if (!$issue) {
            echo "ERROR: issue {$volume}.{$number} not found\n";
            exit(1);
        }

        echo "Issue {$volume}.{$number} (id {$issue->getId()})\n";
        echo $dryRun ? "DRY RUN — nothing will be written\n\n" : "\n";

        // Issue DOI
        if (empty($issue->getData('doiId'))) {
            if ($dryRun) {
                echo "  issue: would mint a DOI\n";
            } else {
                Repo::issue()->createDoi($issue);
                $issue = Repo::issue()->get($issue->getId());
                $doi = $issue->getData('doiObject');
                echo '  issue: ' . ($doi ? $doi->getData('doi') : '(mint failed)') . "\n";
            }
        } else {
            $doi = $issue->getData('doiObject');
            echo '  issue: already has ' . ($doi ? $doi->getData('doi') : 'a DOI') . " — left alone\n";
        }

        // Article DOIs
        $submissions = Repo::submission()->getCollector()
            ->filterByContextIds([$context->getId()])
            ->filterByIssueIds([$issue->getId()])
            ->getMany();

        $minted = $existing = $failed = 0;
        foreach ($submissions as $submission) {
            $publication = $submission->getCurrentPublication();
            $title = substr((string) $publication->getLocalizedTitle(), 0, 54);

            if (!empty($publication->getData('doiId'))) {
                $existing++;
                continue;
            }
            if ($dryRun) {
                echo "  would mint: {$title}\n";
                $minted++;
                continue;
            }

            $failures = Repo::submission()->createDois($submission);
            if (!empty($failures)) {
                $failed++;
                echo "  FAILED: {$title}\n";
                foreach ($failures as $failure) {
                    echo '          ' . $failure->getMessage() . "\n";
                }
                continue;
            }
            $fresh = Repo::publication()->get($publication->getId());
            $doi = $fresh->getData('doiObject');
            $minted++;
            echo '  ' . ($doi ? $doi->getData('doi') : '(no doi returned)') . "  {$title}\n";
        }

        echo "\nMinted {$minted}, already had one {$existing}, failed {$failed}\n";
        if (!$dryRun) {
            echo "DOIs are assigned, NOT deposited. Deposit from the OJS admin DOIs page.\n";
        }
        if ($failed) {
            exit(1);
        }
    }
}

$tool = new AssignIssueDoisTool($argv ?? []);
$tool->execute();
