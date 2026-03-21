#!/usr/bin/env php
<?php
/**
 * Patch: Crossref deposit error details visibility
 *
 * OJS 3.5 core bugs fixed:
 * 1. Repo::doi()->edit() silently drops custom settings (crossrefplugin_failedMsg,
 *    _batchId, _successMsg) because they are not in the Doi entity schema.
 *    Fix: write directly to doi_settings table after Repo::doi()->edit().
 * 2. $msgSave is null on the success-with-failures path in depositXML().
 *    Fix: capture the Crossref response body when failure_count > 0.
 *
 * Without this patch, the DOI management UI shows "Error" status but never
 * displays error details, making deposit debugging impossible.
 *
 * Applied: 2026-03-21
 */

$file = '/var/www/html/plugins/generic/crossref/CrossrefExportPlugin.php';
$code = file_get_contents($file);
$changed = false;

// --- Fix 1: Capture $msgSave on success-with-failures path ---

$needle1 = '        if ($failureCount > 0) {
            $status = Doi::STATUS_ERROR;
            $result = false;';

$replace1 = '        if ($failureCount > 0) {
            $msgSave = (string)$response->getBody();
            error_log("Crossref deposit failure: " . $msgSave);
            $status = Doi::STATUS_ERROR;
            $result = false;';

if (strpos($code, $needle1) !== false) {
    $code = str_replace($needle1, $replace1, $code);
    echo "Fix 1: patched depositXML to capture error response\n";
    $changed = true;
} elseif (strpos($code, 'Crossref deposit failure') !== false) {
    echo "Fix 1: already applied\n";
} else {
    echo "Fix 1: WARNING - target string not found, skipping\n";
}

// --- Fix 2: Write to doi_settings directly in updateDepositStatus ---

$needle2 = '            Repo::doi()->edit($doi, $editParams);
        }
    }

    /**
     * @copydoc DOIPubIdExportPlugin::markRegistered()';

$replace2 = '            Repo::doi()->edit($doi, $editParams);

            // PATCH: Write error/success messages directly to doi_settings.
            // Repo::doi()->edit() silently drops these custom settings in OJS 3.5.
            $settingsToWrite = [
                $this->getFailedMsgSettingName() => $failedMsg,
                $this->getDepositBatchIdSettingName() => $batchId,
                $this->getSuccessMsgSettingName() => $successMsg,
            ];
            foreach ($settingsToWrite as $settingName => $settingValue) {
                \Illuminate\Support\Facades\DB::table(\'doi_settings\')
                    ->updateOrInsert(
                        [\'doi_id\' => $doiId, \'setting_name\' => $settingName],
                        [\'setting_value\' => $settingValue ?? \'\']
                    );
                if ($settingValue === null) {
                    \Illuminate\Support\Facades\DB::table(\'doi_settings\')
                        ->where(\'doi_id\', $doiId)
                        ->where(\'setting_name\', $settingName)
                        ->delete();
                }
            }
        }
    }

    /**
     * @copydoc DOIPubIdExportPlugin::markRegistered()';

if (strpos($code, $needle2) !== false) {
    $code = str_replace($needle2, $replace2, $code);
    echo "Fix 2: patched updateDepositStatus to write doi_settings directly\n";
    $changed = true;
} elseif (strpos($code, 'PATCH: Write error/success') !== false) {
    echo "Fix 2: already applied\n";
} else {
    echo "Fix 2: WARNING - target string not found, skipping\n";
}

if ($changed) {
    file_put_contents($file, $code);
    echo "Patch applied successfully.\n";
} else {
    echo "No changes needed.\n";
}
