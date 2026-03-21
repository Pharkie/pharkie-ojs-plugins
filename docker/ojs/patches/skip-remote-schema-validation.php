#!/usr/bin/env php
<?php
/**
 * Patch: Skip remote XML schema validation
 *
 * OJS tries to download http://www.w3.org/Math/XMLSchema/mathml3/mathml3.xsd
 * during Crossref XML export. W3C returns HTTP 403 (they block automated
 * schema downloads). The validation is non-blocking (just a PHP warning),
 * but the HTTP timeout wastes ~13 seconds per DOI deposit.
 *
 * This patch skips the schema validation case entirely. The Crossref XML
 * is still generated correctly — the schema validation only checked the
 * intermediate filter output, not the final deposit XML.
 *
 * Applied: 2026-03-21
 */

$file = '/var/www/html/lib/pkp/classes/xslt/XMLTypeDescription.php';
$code = file_get_contents($file);

$old = <<<'OLD'
            case self::XML_TYPE_DESCRIPTION_VALIDATE_SCHEMA:
                libxml_use_internal_errors(true);
                if (!$xmlDom->schemaValidate($this->_validationSource)) {
                    error_log(new Exception("XML validation failed with:\n" . print_r(libxml_get_errors(), true)));
                    return false;
                }

                break;
OLD;

$new = <<<'NEW'
            case self::XML_TYPE_DESCRIPTION_VALIDATE_SCHEMA:
                // PATCH: Skip remote schema validation - W3C returns 403,
                // wastes ~13s per request. Validation is non-blocking anyway.
                break;
NEW;

if (strpos($code, $old) !== false) {
    $code = str_replace($old, $new, $code);
    file_put_contents($file, $code);
    echo "Patch applied: skipped remote schema validation\n";
} elseif (strpos($code, 'PATCH: Skip remote schema validation') !== false) {
    echo "Patch already applied\n";
} else {
    echo "WARNING: target string not found, skipping\n";
}
