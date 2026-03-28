<?php

/**
 * @file api/v1/qa-splits/index.php
 *
 * API entry point for the QA Splits plugin.
 *
 * OJS 3.5 routes API requests to api/v1/{entity}/index.php files.
 * This file is mounted into the OJS installation at that path so the
 * APIRouter can find it and load our controller.
 */

return new \APP\plugins\generic\qaSplits\QaSplitsController();
