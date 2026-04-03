<?php

/**
 * @file api/v1/archive-checker/index.php
 *
 * API entry point for the Archive Checker plugin.
 *
 * OJS 3.5 routes API requests to api/v1/{entity}/index.php files.
 * This file is mounted into the OJS installation at that path so the
 * APIRouter can find it and load our controller.
 */

return new \APP\plugins\generic\archiveChecker\ArchiveCheckerController();
