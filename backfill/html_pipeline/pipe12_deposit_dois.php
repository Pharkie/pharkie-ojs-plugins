<?php

/**
 * Deposit an issue's DOIs to the registration agency (Crossref).
 *
 * Assigning a DOI (pipe11) only records it locally. This is the step that
 * hands the metadata to Crossref so the DOI actually resolves.
 *
 * Scoped to ONE issue on purpose. OJS's own "deposit all" sweeps up every DOI
 * needing deposit in the journal, which on this instance would also re-deposit
 * unrelated stale ones — a separate decision that should be taken separately.
 *
 * Depositing is asynchronous: the job hands the metadata over and OJS marks the
 * DOI "submitted" (status 2). Crossref confirms later, and only then does it
 * become "registered" (status 3). An HTTP 200 is not a registration.
 *
 * Run inside the OJS container:
 *   docker compose exec -T ojs php /var/www/html/backfill-deposit-dois.php 37 2 --confirm
 *   ...or via the wrapper: backfill/html_pipeline/pipe12_deposit_dois.sh 37.2 --confirm
 *
 * Without --confirm it lists what it would deposit and exits.
 *
 * Crossref rate-limits bursts and will 429 the odd one even when paced.
 * Re-running is safe and picks up only what has not registered yet.
 */

use APP\facades\Repo;
use PKP\cliTool\CommandLineTool;
use PKP\db\DAORegistry;
use PKP\doi\Doi;
use APP\jobs\doi\DepositIssue;
use PKP\jobs\doi\DepositSubmission;

require('/var/www/html/tools/bootstrap.php');

const DEPOSIT_GAP_MICROSECONDS = 3000000;  // 3s between deposits

class DepositIssueDoisTool extends CommandLineTool
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
        echo "Deposit an issue's DOIs to Crossref.\n\n"
            . "Usage: php {$this->scriptName} <volume> <number> [--confirm]\n"
            . "Without --confirm, lists what would be deposited and exits.\n";
    }

    public function execute()
    {
        [$volume, $number] = $this->argv;
        $confirm = in_array('--confirm', $this->argv, true);

        $contextDao = DAORegistry::getDAO('JournalDAO');
        $context = $contextDao->getByPath('ea');
        if (!$context) {
            echo "ERROR: journal 'ea' not found\n";
            exit(1);
        }

        $agency = $context->getConfiguredDoiAgency();
        if ($agency === null) {
            echo "ERROR: no DOI registration agency configured\n";
            exit(1);
        }
        echo 'Agency: ' . $agency->getName() . "\n";

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
        echo "Issue {$volume}.{$number} (id {$issue->getId()})\n\n";

        $submissions = Repo::submission()->getCollector()
            ->filterByContextIds([$context->getId()])
            ->filterByIssueIds([$issue->getId()])
            ->getMany();

        $toDeposit = [];
        $skipped = 0;
        foreach ($submissions as $submission) {
            $publication = $submission->getCurrentPublication();
            $doi = $publication->getData('doiObject');
            $title = substr((string) $publication->getLocalizedTitle(), 0, 52);

            if (!$doi) {
                echo "  no DOI, skipping: {$title}\n";
                $skipped++;
                continue;
            }
            $status = (int) $doi->getData('status');
            if ($status === Doi::STATUS_REGISTERED) {
                echo "  already registered, skipping: {$doi->getData('doi')}\n";
                $skipped++;
                continue;
            }
            $toDeposit[] = ['id' => $submission->getId(), 'doi' => $doi, 'title' => $title];
        }

        // The issue itself carries a DOI too, deposited by its own job.
        $issueDoi = $issue->getData('doiObject');
        $depositIssue = $issueDoi
            && (int) $issueDoi->getData('status') !== Doi::STATUS_REGISTERED;
        if ($issueDoi && !$depositIssue) {
            echo '  issue DOI already registered, skipping: '
                . $issueDoi->getData('doi') . "\n";
        }

        if (!$toDeposit && !$depositIssue) {
            echo "\nNothing to deposit.\n";
            return;
        }

        echo "\n" . count($toDeposit) . " to deposit"
            . ($skipped ? ", {$skipped} skipped" : '') . ":\n";
        foreach ($toDeposit as $row) {
            echo '  ' . $row['doi']->getData('doi') . '  ' . $row['title'] . "\n";
        }
        if ($depositIssue) {
            echo '  ' . $issueDoi->getData('doi') . "  (the issue itself)\n";
        }

        if (!$confirm) {
            echo "\nDRY RUN — nothing deposited. Re-run with --confirm.\n";
            return;
        }

        // Crossref rate-limits a burst: depositing 23 at once returned a 429
        // on one of them. A short gap between deposits avoids it, and the
        // whole issue still goes through in under a minute.
        $doiIds = [];
        $first = true;
        foreach ($toDeposit as $row) {
            if (!$first) {
                usleep(DEPOSIT_GAP_MICROSECONDS);
            }
            $first = false;
            dispatch(new DepositSubmission($row['id'], $context, $agency));
            $doiIds = array_merge($doiIds, Repo::doi()->getDoisForSubmission($row['id']));
            echo '  deposited ' . $row['doi']->getData('doi') . "\n";
        }
        if ($depositIssue) {
            usleep(DEPOSIT_GAP_MICROSECONDS);
            dispatch(new DepositIssue($issue->getId(), $context, $agency));
            $doiIds[] = $issueDoi->getId();
        }
        Repo::doi()->markSubmitted($doiIds);

        $total = count($toDeposit) + ($depositIssue ? 1 : 0);
        echo "\nQueued {$total} deposits and marked them submitted.\n";
        echo "Drain the job queue, then check status: submitted (2) becomes\n"
            . "registered (3) only once Crossref confirms.\n";
    }
}

$tool = new DepositIssueDoisTool($argv ?? []);
$tool->execute();
