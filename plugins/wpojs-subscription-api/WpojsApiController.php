<?php

namespace APP\plugins\generic\wpojsSubscriptionApi;

use APP\core\Application;
use APP\facades\Repo;
use APP\subscription\IndividualSubscription;
use Illuminate\Http\JsonResponse;
use Illuminate\Http\Request;
use Illuminate\Http\Response;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Mail;
use Illuminate\Support\Facades\Route;
use PKP\config\Config;
use PKP\core\Core;
use PKP\core\PKPBaseController;
use PKP\core\PKPRequest;
use PKP\db\DAORegistry;
use PKP\mail\mailables\PasswordResetRequested;
use PKP\security\Role;
use PKP\security\Validation;
use APP\plugins\generic\wpojsSubscriptionApi\WpojsApiLog;

class WpojsApiController extends PKPBaseController
{
    // Subscription status constants (from classes/subscription/Subscription.php)
    private const STATUS_ACTIVE = 1;
    private const STATUS_OTHER = 16; // "Other" — used for expired-by-sync and GDPR

    public function getHandlerPath(): string
    {
        return 'wpojs';
    }

    public function getRouteGroupMiddleware(): array
    {
        // No OJS middleware needed — this is a machine-to-machine API.
        // Auth is handled per-endpoint via checkIp() (IP allowlist) and
        // checkApiKey() (shared-secret Bearer token comparison).
        return [];
    }

    public function authorize(PKPRequest $request, array &$args, array $roleAssignments): bool
    {
        // OJS's default policy for API controllers is DENY unless a
        // role-based policy explicitly grants access. This controller
        // is a machine-to-machine API (WP→OJS) with no OJS user session,
        // so role-based auth doesn't apply. Instead, every endpoint
        // enforces its own auth via checkIp() (IP allowlist) and
        // checkAuth() (API key verification). Permit here so the
        // PolicyAuthorizer middleware doesn't block us before our
        // endpoint-level auth runs.
        return true;
    }

    public function getGroupRoutes(): void
    {
        // Ping bypasses auth — reachability check only
        Route::get('ping', $this->ping(...))->name('wpojs.ping');

        Route::get('preflight', $this->preflight(...))->name('wpojs.preflight');
        Route::get('subscription-types', $this->getSubscriptionTypes(...))->name('wpojs.subscriptionTypes');
        Route::get('users', $this->findUser(...))->name('wpojs.users.find');
        Route::post('users/find-or-create', $this->findOrCreateUser(...))->name('wpojs.users.findOrCreate');
        Route::put('users/{userId}/email', $this->updateUserEmail(...))->name('wpojs.users.updateEmail');
        Route::delete('users/{userId}', $this->deleteUser(...))->name('wpojs.users.delete');
        Route::post('subscriptions', $this->createSubscription(...))->name('wpojs.subscriptions.create');
        Route::put('subscriptions/expire-by-user/{userId}', $this->expireSubscriptionByUser(...))->name('wpojs.subscriptions.expireByUser');
        Route::put('subscriptions/{subscriptionId}/expire', $this->expireSubscription(...))->name('wpojs.subscriptions.expire');
        Route::get('subscriptions', $this->getSubscriptions(...))->name('wpojs.subscriptions.list');
        Route::post('subscriptions/status-batch', $this->getSubscriptionStatusBatch(...))->name('wpojs.subscriptions.statusBatch');
        Route::post('welcome-email', $this->sendWelcomeEmail(...))->name('wpojs.welcomeEmail');
    }

    // ---------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------

    /**
     * Verify client IP is in the allowlist.
     * Uses REMOTE_ADDR directly — not $request->ip() which trusts
     * X-Forwarded-For and is spoofable behind a reverse proxy.
     */
    private function checkIp(Request $request): ?JsonResponse
    {
        $allowedIps = (string) Config::getVar('wpojs', 'allowed_ips', '');

        if (empty($allowedIps)) {
            return response()->json(
                ['error' => 'IP allowlist not configured'],
                Response::HTTP_FORBIDDEN
            );
        }

        $allowed = array_map('trim', explode(',', $allowedIps));
        $clientIp = $request->server('REMOTE_ADDR');

        // Pre-compute IPv4 long for CIDR matching. Returns false for IPv6.
        $clientLong = ip2long($clientIp);
        if ($clientLong === false) {
            error_log('[wpojs-api] IPv6 address detected (' . $clientIp . '). IP allowlist CIDR matching only supports IPv4. Configure your server for IPv4 or add the exact IPv6 address to allowed_ips.');
        }

        $matched = false;
        foreach ($allowed as $entry) {
            if (str_contains($entry, '/')) {
                // CIDR notation (e.g. 172.16.0.0/12) — IPv4 only
                if ($clientLong === false) {
                    continue; // skip CIDR check for IPv6 clients
                }
                [$subnet, $bits] = explode('/', $entry, 2);
                $bits = (int) $bits;
                if ($bits < 0 || $bits > 32) {
                    continue; // invalid CIDR prefix length
                }
                $subnetLong = ip2long($subnet);
                $mask = -1 << (32 - $bits);
                if ($subnetLong !== false
                    && ($clientLong & $mask) === ($subnetLong & $mask)) {
                    $matched = true;
                    break;
                }
            } elseif ($entry === $clientIp) {
                // Exact string match — works for both IPv4 and IPv6
                $matched = true;
                break;
            }
        }

        if (!$matched) {
            return response()->json(
                ['error' => 'IP not allowed (IPv4 CIDR and exact IPv4/IPv6 matching supported)'],
                Response::HTTP_FORBIDDEN
            );
        }

        return null;
    }

    /**
     * Verify Bearer token matches the shared API key secret.
     * This is a machine-to-machine API — no OJS user session needed.
     */
    private function checkApiKey(Request $request): ?JsonResponse
    {
        $secret = (string) Config::getVar('wpojs', 'api_key_secret', '');
        if (empty($secret)) {
            $secret = (string) Config::getVar('security', 'api_key_secret', '');
        }

        if (empty($secret)) {
            return response()->json(
                ['error' => 'API key secret not configured'],
                Response::HTTP_INTERNAL_SERVER_ERROR
            );
        }

        $authHeader = $request->header('Authorization', '');
        $token = '';
        if (str_starts_with($authHeader, 'Bearer ')) {
            $token = substr($authHeader, 7);
        }

        if (empty($token) || !hash_equals($secret, $token)) {
            return response()->json(
                ['error' => 'Invalid or missing API key'],
                Response::HTTP_UNAUTHORIZED
            );
        }

        return null;
    }

    /**
     * Combined authorization: IP allowlist + API key + load protection.
     * Call at the start of every protected endpoint.
     * Logs auth failures (401/403/429). Success logging is deferred to
     * jsonResponse() so the actual endpoint status code is recorded.
     * Also triggers periodic log cleanup (at most once per hour).
     */
    private function checkAuth(Request $request): ?JsonResponse
    {
        if (!defined('WPOJS_REQUEST_START')) {
            define('WPOJS_REQUEST_START', microtime(true));
        }

        $this->maybeCleanupLogs();

        $ipError = $this->checkIp($request);
        if ($ipError) {
            $this->logRequest($request, $ipError->getStatusCode());
            return $ipError;
        }

        $keyError = $this->checkApiKey($request);
        if ($keyError) {
            $this->logRequest($request, $keyError->getStatusCode());
            return $keyError;
        }

        $loadError = $this->checkLoad();
        if ($loadError) {
            $this->logRequest($request, 429);
            return $loadError;
        }

        return null;
    }

    /**
     * Run log cleanup at most once per hour.
     * Uses a plugin setting to track the last run timestamp.
     */
    private function maybeCleanupLogs(): void
    {
        static $checked = false;
        if ($checked) {
            return;
        }
        $checked = true;

        try {
            $lastCleanup = (int) DB::table('plugin_settings')
                ->where('plugin_name', 'wpojssubscriptionapiplugin')
                ->where('setting_name', 'last_log_cleanup')
                ->value('setting_value');

            if (time() - $lastCleanup < 3600) {
                return;
            }

            WpojsApiLog::cleanup(30);

            DB::table('plugin_settings')->updateOrInsert(
                ['plugin_name' => 'wpojssubscriptionapiplugin', 'setting_name' => 'last_log_cleanup', 'context_id' => 0],
                ['setting_value' => (string) time()]
            );
        } catch (\Exception $e) {
            // Cleanup is best-effort — don't break the API request.
        }
    }

    /**
     * Load-based backpressure: OJS measures its own response times and
     * returns 429 when under pressure. No magic request counts.
     *
     * Thresholds:
     *   avg < 500ms  → healthy, allow
     *   avg 500-2000ms → stressed, Retry-After: 2
     *   avg > 2000ms → overloaded, Retry-After: 5
     *   < 5 recent samples → cold start, allow
     */
    private function checkLoad(): ?JsonResponse
    {
        try {
            $stats = WpojsApiLog::getAverageResponseTime(20, 60);
        } catch (\Exception $e) {
            error_log('[wpojs] Load check skipped: ' . $e->getMessage());
            return null;
        }

        // Cold start — not enough data to judge.
        if ($stats['sample_count'] < 5) {
            return null;
        }

        $avgMs = $stats['avg_ms'];

        if ($avgMs === null || $avgMs < 500) {
            return null; // healthy
        }

        $retryAfter = $avgMs > 2000 ? 5 : 2;

        return response()->json(
            ['error' => 'Server under load. Please retry later.', 'avg_ms' => $avgMs],
            Response::HTTP_TOO_MANY_REQUESTS
        )->withHeaders(['Retry-After' => $retryAfter]);
    }

    /**
     * Write an entry to the API request log.
     */
    private function logRequest(Request $request, int $httpStatus, ?int $durationMs = null): void
    {
        $endpoint = $request->path();
        $method = $request->method();
        $sourceIp = $request->server('REMOTE_ADDR', 'unknown');

        WpojsApiLog::log($endpoint, $method, $sourceIp, $httpStatus, $durationMs);
    }

    /**
     * Build a JSON response and log the actual HTTP status code + duration.
     * Use this instead of response()->json() in endpoint methods
     * so the API log reflects the real outcome (not a premature 200).
     */
    private function jsonResponse(Request $request, array $data, int $status = 200): JsonResponse
    {
        $durationMs = null;
        if (defined('WPOJS_REQUEST_START')) {
            $durationMs = (int) round((microtime(true) - WPOJS_REQUEST_START) * 1000);
        }
        $this->logRequest($request, $status, $durationMs);
        return response()->json($data, $status);
    }

    /**
     * Get the journal ID from the request context.
     * Returns a JsonResponse error if no journal context is available.
     */
    private function getJournalIdOrFail(): int|JsonResponse
    {
        $context = Application::get()->getRequest()->getContext();
        if (!$context) {
            return response()->json(
                ['error' => 'Invalid request context'],
                Response::HTTP_BAD_REQUEST
            );
        }
        return $context->getId();
    }

    private function isValidDate(string $date): bool
    {
        $d = \DateTime::createFromFormat('Y-m-d', $date);
        return $d && $d->format('Y-m-d') === $date;
    }

    // ---------------------------------------------------------------
    // GET /wpojs/ping
    // No auth, no IP check — pure reachability probe.
    // ---------------------------------------------------------------

    public function ping(Request $request): JsonResponse
    {
        if (!defined('WPOJS_REQUEST_START')) {
            define('WPOJS_REQUEST_START', microtime(true));
        }
        return $this->jsonResponse($request, ['status' => 'ok']);
    }

    // ---------------------------------------------------------------
    // GET /wpojs/preflight
    // ---------------------------------------------------------------

    public function preflight(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $checks = [];
        $compatible = true;

        // Repo::user() methods
        foreach (['getByEmail', 'newDataObject', 'add', 'edit', 'get', 'delete'] as $method) {
            $ok = method_exists(Repo::user(), $method);
            $checks[] = ['name' => "Repo::user()->{$method}()", 'ok' => $ok];
            if (!$ok) {
                $compatible = false;
            }
        }

        // Repo::userGroup() methods
        foreach (['getByRoleIds', 'assignUserToGroup'] as $method) {
            $ok = method_exists(Repo::userGroup(), $method);
            $checks[] = ['name' => "Repo::userGroup()->{$method}()", 'ok' => $ok];
            if (!$ok) {
                $compatible = false;
            }
        }

        // Repo::emailTemplate() methods
        foreach (['getByKey'] as $method) {
            $ok = method_exists(Repo::emailTemplate(), $method);
            $checks[] = ['name' => "Repo::emailTemplate()->{$method}()", 'ok' => $ok];
            if (!$ok) {
                $compatible = false;
            }
        }

        // IndividualSubscriptionDAO
        $subDao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        if (!$subDao) {
            $checks[] = ['name' => 'IndividualSubscriptionDAO', 'ok' => false];
            $compatible = false;
        } else {
            foreach (['insertObject', 'updateObject', 'getById', 'getByUserIdForJournal', 'deleteById'] as $method) {
                $ok = method_exists($subDao, $method);
                $checks[] = ['name' => "IndividualSubscriptionDAO::{$method}()", 'ok' => $ok];
                if (!$ok) {
                    $compatible = false;
                }
            }
        }

        // SubscriptionTypeDAO
        $typeDao = DAORegistry::getDAO('SubscriptionTypeDAO');
        if (!$typeDao) {
            $checks[] = ['name' => 'SubscriptionTypeDAO', 'ok' => false];
            $compatible = false;
        } else {
            $ok = method_exists($typeDao, 'getById');
            $checks[] = ['name' => 'SubscriptionTypeDAO::getById()', 'ok' => $ok];
            if (!$ok) {
                $compatible = false;
            }
        }

        // Validation methods
        foreach (['encryptCredentials'] as $method) {
            $ok = method_exists(Validation::class, $method);
            $checks[] = ['name' => "Validation::{$method}()", 'ok' => $ok];
            if (!$ok) {
                $compatible = false;
            }
        }

        // Validation::generatePasswordResetHash (for welcome email reset links)
        $ok = method_exists(Validation::class, 'generatePasswordResetHash');
        $checks[] = ['name' => 'Validation::generatePasswordResetHash()', 'ok' => $ok];
        if (!$ok) {
            $compatible = false;
        }

        // PasswordResetRequested mailable
        $ok = class_exists(PasswordResetRequested::class);
        $checks[] = ['name' => 'PasswordResetRequested class', 'ok' => $ok];
        if (!$ok) {
            $compatible = false;
        }

        // Core::getCurrentDate()
        $ok = method_exists(Core::class, 'getCurrentDate');
        $checks[] = ['name' => 'Core::getCurrentDate()', 'ok' => $ok];
        if (!$ok) {
            $compatible = false;
        }

        // Role constants
        $ok = defined(Role::class . '::ROLE_ID_READER');
        $checks[] = ['name' => 'Role::ROLE_ID_READER constant', 'ok' => $ok];
        if (!$ok) {
            $compatible = false;
        }

        // DB tables needed for role check
        try {
            DB::table('user_user_groups')->limit(1)->exists();
            $checks[] = ['name' => 'user_user_groups table', 'ok' => true];
        } catch (\Exception $e) {
            $checks[] = ['name' => 'user_user_groups table', 'ok' => false];
            $compatible = false;
        }

        // Plugin API log table (created by schema.xml on plugin enable)
        try {
            DB::table('wpojs_api_log')->limit(1)->exists();
            $checks[] = ['name' => 'wpojs_api_log table', 'ok' => true];
        } catch (\Exception $e) {
            $checks[] = ['name' => 'wpojs_api_log table (disable and re-enable plugin to create)', 'ok' => false];
            $compatible = false;
        }

        // Subscription types — at least one must exist for sync to work
        $journalIdResult = $this->getJournalIdOrFail();
        if (is_int($journalIdResult)) {
            try {
                $typeCount = (int) DB::table('subscription_types')
                    ->where('journal_id', $journalIdResult)
                    ->count();
                $ok = $typeCount > 0;
                $detail = $ok ? "{$typeCount} type(s) found" : 'No subscription types — create one in OJS Subscriptions settings';
                $checks[] = ['name' => 'Subscription types exist', 'ok' => $ok, 'detail' => $detail];
                if (!$ok) {
                    $compatible = false;
                }
            } catch (\Exception $e) {
                $checks[] = ['name' => 'subscription_types table', 'ok' => false];
                $compatible = false;
            }
        }

        // Load protection status
        $loadStats = WpojsApiLog::getAverageResponseTime(20, 60);
        $avgDetail = $loadStats['avg_ms'] !== null
            ? "load-based (avg response: {$loadStats['avg_ms']}ms, samples: {$loadStats['sample_count']})"
            : 'load-based (no recent data)';
        $checks[] = ['name' => 'Load protection', 'ok' => true, 'detail' => $avgDetail];

        return $this->jsonResponse($request, [
            'compatible' => $compatible,
            'checks' => $checks,
        ]);
    }

    // ---------------------------------------------------------------
    // GET /wpojs/subscription-types
    // List subscription types for the current journal.
    // ---------------------------------------------------------------

    public function getSubscriptionTypes(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) {
            return $journalId;
        }

        $locale = Application::get()->getRequest()->getContext()?->getPrimaryLocale() ?? 'en';

        $types = DB::table('subscription_types')
            ->leftJoin('subscription_type_settings', function ($join) use ($locale) {
                $join->on('subscription_types.type_id', '=', 'subscription_type_settings.type_id')
                     ->where('subscription_type_settings.setting_name', '=', 'name')
                     ->where('subscription_type_settings.locale', '=', $locale);
            })
            ->where('subscription_types.journal_id', $journalId)
            ->select(
                'subscription_types.type_id as id',
                DB::raw("COALESCE(subscription_type_settings.setting_value, CONCAT('Type #', subscription_types.type_id)) as name")
            )
            ->orderBy('subscription_types.seq')
            ->get()
            ->toArray();

        return $this->jsonResponse($request, ['types' => $types]);
    }

    // ---------------------------------------------------------------
    // GET /wpojs/users?email=...
    // Read-only user lookup (no side effects).
    // ---------------------------------------------------------------

    public function findUser(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $email = trim($request->query('email', ''));

        if (empty($email) || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            return $this->jsonResponse($request, ['error' => 'Provide valid email query parameter'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->getByEmail($email, true);
        if (!$user) {
            return $this->jsonResponse($request, ['found' => false]);
        }

        return $this->jsonResponse($request, [
            'found' => true,
            'userId' => $user->getId(),
            'email' => $user->getEmail(),
            'username' => $user->getUsername(),
            'disabled' => (bool) $user->getDisabled(),
        ]);
    }

    // ---------------------------------------------------------------
    // POST /wpojs/users/find-or-create
    // ---------------------------------------------------------------

    public function findOrCreateUser(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $email = trim($request->input('email', ''));
        $firstName = trim($request->input('firstName', ''));
        $lastName = trim($request->input('lastName', ''));
        $sendWelcomeEmail = (bool) $request->input('sendWelcomeEmail', false);

        if (empty($email) || !filter_var($email, FILTER_VALIDATE_EMAIL) || strlen($email) > 255) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing email'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($firstName) || strlen($firstName) > 255) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing firstName'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($lastName) || strlen($lastName) > 255) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing lastName'], Response::HTTP_BAD_REQUEST);
        }

        // Check for existing user (include disabled accounts)
        $existingUser = Repo::user()->getByEmail($email, true);
        if ($existingUser) {
            return $this->jsonResponse($request, [
                'userId' => $existingUser->getId(),
                'created' => false,
            ]);
        }

        try {
            $user = Repo::user()->newDataObject();
            // Generate unique username (reimplements Validation::suggestUsername
            // to avoid PHP 8.3 deprecation on empty-string increment — pkp/pkp-lib#12377)
            $base = preg_replace('/[^a-zA-Z0-9]/', '', strtolower($firstName) . strtolower($lastName));
            if ($base === '') {
                $base = 'user';
            }
            $username = $base;
            for ($i = 1; Repo::user()->getByUsername($username, true); $i++) {
                $username = $base . $i;
            }
            $user->setUsername($username);
            $user->setEmail($email);
            $locale = Application::get()->getRequest()->getContext()?->getPrimaryLocale() ?? 'en';
            $user->setGivenName($firstName, $locale);
            $user->setFamilyName($lastName, $locale);
            $user->setPassword(Validation::encryptCredentials($username, bin2hex(random_bytes(16))));
            $user->setDateRegistered(Core::getCurrentDate());
            $user->setDateValidated(Core::getCurrentDate()); // marks email as verified
            $user->setMustChangePassword(true);
            $user->setDisabled(false);

            $userId = Repo::user()->add($user);

            // Assign Reader role for this journal
            $contextId = $this->getJournalIdOrFail();
            if ($contextId instanceof JsonResponse) return $contextId;
            $readerGroup = Repo::userGroup()
                ->getByRoleIds([Role::ROLE_ID_READER], $contextId)
                ->first();
            if ($readerGroup) {
                Repo::userGroup()->assignUserToGroup(
                    userId: $userId,
                    userGroupId: $readerGroup->getKey()
                );
            }

            // Mark this user as created by sync (for status page stats).
            DB::table('user_settings')->insertOrIgnore([
                'user_id' => $userId,
                'locale' => '',
                'setting_name' => 'wpojs_created_by_sync',
                'setting_value' => Core::getCurrentDate(),
            ]);
        } catch (\Illuminate\Database\QueryException $e) {
            // Race condition: another request may have created user with same email
            $raceUser = Repo::user()->getByEmail($email, true);
            if ($raceUser) {
                // Ensure Reader role is assigned (idempotent — safe if already assigned)
                $contextId = $this->getJournalIdOrFail();
                if ($contextId instanceof JsonResponse) return $contextId;
                $readerGroup = Repo::userGroup()
                    ->getByRoleIds([Role::ROLE_ID_READER], $contextId)
                    ->first();
                if ($readerGroup) {
                    Repo::userGroup()->assignUserToGroup(
                        userId: $raceUser->getId(),
                        userGroupId: $readerGroup->getKey()
                    );
                }

                return $this->jsonResponse($request, [
                    'userId' => $raceUser->getId(),
                    'created' => false,
                ]);
            }

            // No email collision found — likely a username collision. Retry once
            // with a random suffix to break the tie.
            try {
                $user->setUsername($base . '_' . bin2hex(random_bytes(3)));
                $userId = Repo::user()->add($user);

                // Replicate the success path: assign Reader role and mark sync-created
                $contextId = $this->getJournalIdOrFail();
                if ($contextId instanceof JsonResponse) return $contextId;
                $readerGroup = Repo::userGroup()
                    ->getByRoleIds([Role::ROLE_ID_READER], $contextId)
                    ->first();
                if ($readerGroup) {
                    Repo::userGroup()->assignUserToGroup(
                        userId: $userId,
                        userGroupId: $readerGroup->getKey()
                    );
                }
                DB::table('user_settings')->insertOrIgnore([
                    'user_id' => $userId,
                    'locale' => '',
                    'setting_name' => 'wpojs_created_by_sync',
                    'setting_value' => Core::getCurrentDate(),
                ]);
            } catch (\Exception $retryException) {
                error_log('[wpojs-api] createUser retry failed for ' . ($email ?? 'unknown') . ': ' . $retryException->getMessage());
                return $this->jsonResponse($request, ['error' => 'Failed to create user'], Response::HTTP_INTERNAL_SERVER_ERROR);
            }
        } catch (\Exception $e) {
            error_log('[wpojs-api] createUser failed for ' . ($email ?? 'unknown') . ': ' . $e->getMessage());
            return $this->jsonResponse($request, ['error' => 'Failed to create user'], Response::HTTP_INTERNAL_SERVER_ERROR);
        }

        if ($sendWelcomeEmail) {
            // Best effort — don't fail user creation if email fails
            $this->doSendWelcomeEmail($userId);
        }

        return $this->jsonResponse($request, [
            'userId' => $userId,
            'created' => true,
        ]);
    }

    // ---------------------------------------------------------------
    // PUT /wpojs/users/{userId}/email
    // ---------------------------------------------------------------

    public function updateUserEmail(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $userId = (int) $request->route('userId');
        $newEmail = trim($request->input('newEmail', ''));

        if ($userId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($newEmail) || !filter_var($newEmail, FILTER_VALIDATE_EMAIL) || strlen($newEmail) > 255) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing newEmail'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->get($userId);
        if (!$user) {
            return $this->jsonResponse($request, ['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        // OJS doesn't enforce uniqueness in edit() — we must check
        $existing = Repo::user()->getByEmail($newEmail, true);
        if ($existing && $existing->getId() !== $userId) {
            return $this->jsonResponse($request, ['error' => 'Email already in use by another account'], Response::HTTP_CONFLICT);
        }

        Repo::user()->edit($user, ['email' => $newEmail]);

        return $this->jsonResponse($request, ['userId' => $userId]);
    }

    // ---------------------------------------------------------------
    // DELETE /wpojs/users/{userId}
    // GDPR erasure: anonymise all PII, disable account, expire subscription.
    // ---------------------------------------------------------------

    public function deleteUser(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $userId = (int) $request->route('userId');

        if ($userId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->get($userId);
        if (!$user) {
            return $this->jsonResponse($request, ['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        // Anonymise all PII and disable account
        $locale = Application::get()->getRequest()->getContext()?->getPrimaryLocale() ?? 'en';
        $anonymisedEmail = 'deleted_' . $userId . '@anonymised.invalid';
        Repo::user()->edit($user, [
            'email' => $anonymisedEmail,
            'username' => 'deleted_' . $userId,
            'givenName' => [$locale => 'Deleted'],
            'familyName' => [$locale => 'User'],
            'affiliation' => [$locale => ''],
            'biography' => [$locale => ''],
            'orcid' => '',
            'url' => '',
            'phone' => '',
            'mailingAddress' => '',
            'datePasswordResetRequested' => null,
            'disabled' => true,
        ]);

        // Expire any active subscription in this journal
        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) return $journalId;
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($userId, $journalId);
        if ($sub && (int) $sub->getStatus() === self::STATUS_ACTIVE) {
            $sub->setStatus(self::STATUS_OTHER);
            $dao->updateObject($sub);
        }

        // Remove PII-related settings (name, affiliation, biography already anonymised by edit() above)
        DB::table('user_settings')
            ->where('user_id', $userId)
            ->whereIn('setting_name', [
                'wpojs_created_by_sync',
                'wpojs_welcome_email_sent',
                'preferredPublicName',
                'signature',
                'mailingAddress',
                'phone',
                'orcid',
            ])
            ->delete();

        // Remove access_keys (password reset tokens tied to this user)
        DB::table('access_keys')->where('user_id', $userId)->delete();

        return $this->jsonResponse($request, [
            'deleted' => true,
            'userId' => $userId,
        ]);
    }

    // ---------------------------------------------------------------
    // POST /wpojs/subscriptions
    // Idempotent upsert. Creates or updates subscription.
    // ---------------------------------------------------------------

    public function createSubscription(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $userId = (int) $request->input('userId', 0);
        $typeId = (int) $request->input('typeId', 0);
        $dateStart = $request->input('dateStart');
        $dateEnd = $request->input('dateEnd'); // null for non-expiring

        if ($userId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing userId'], Response::HTTP_BAD_REQUEST);
        }
        if ($typeId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing typeId'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($dateStart) || !$this->isValidDate($dateStart)) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing dateStart (expected Y-m-d)'], Response::HTTP_BAD_REQUEST);
        }
        if ($dateEnd !== null && !$this->isValidDate($dateEnd)) {
            return $this->jsonResponse($request, ['error' => 'Invalid dateEnd (expected Y-m-d or null)'], Response::HTTP_BAD_REQUEST);
        }
        if ($dateEnd !== null && $dateEnd < $dateStart) {
            return $this->jsonResponse($request, ['error' => 'dateEnd must not be before dateStart'], Response::HTTP_BAD_REQUEST);
        }

        // Verify user exists
        $user = Repo::user()->get($userId);
        if (!$user) {
            return $this->jsonResponse($request, ['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) return $journalId;

        // Verify subscription type exists for this journal
        $typeDao = DAORegistry::getDAO('SubscriptionTypeDAO');
        $type = $typeDao->getById($typeId);
        if (!$type || (int) $type->getJournalId() !== $journalId) {
            return $this->jsonResponse($request, ['error' => 'Invalid subscription type for this journal'], Response::HTTP_BAD_REQUEST);
        }

        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $existingSub = $dao->getByUserIdForJournal($userId, $journalId);

        if ($existingSub) {
            $wasActive = ((int) $existingSub->getStatus() === self::STATUS_ACTIVE);
            $needsUpdate = false;

            if (!$wasActive) {
                // Reactivation: apply all incoming values (WP is source of truth)
                $existingSub->setStatus(self::STATUS_ACTIVE);
                $existingSub->setTypeId($typeId);
                $existingSub->setDateStart($dateStart);
                $existingSub->setDateEnd($dateEnd);
                $needsUpdate = true;
            } else {
                // Already active — extend only (idempotent, prevents shortening)
                $existingEnd = $existingSub->getDateEnd();

                if ($dateEnd === null && $existingEnd !== null) {
                    // Switching to non-expiring
                    $existingSub->setDateEnd(null);
                    $needsUpdate = true;
                } elseif ($dateEnd !== null && $existingEnd !== null && $dateEnd > $existingEnd) {
                    // New end date is later — extend
                    $existingSub->setDateEnd($dateEnd);
                    $needsUpdate = true;
                }
                // If existing is non-expiring and new has a date: keep non-expiring
                // If new dateEnd <= existing dateEnd: keep existing (no-op)

                // Update typeId if membership tier changed (upgrade/downgrade)
                if ((int) $existingSub->getTypeId() !== $typeId) {
                    $existingSub->setTypeId($typeId);
                    $needsUpdate = true;
                }
            }

            if ($needsUpdate) {
                $dao->updateObject($existingSub);
            }

            return $this->jsonResponse($request, [
                'subscriptionId' => $existingSub->getId(),
            ]);
        }

        // Create new subscription
        // Note: the subscriptions table may lack a unique constraint on (user_id, journal_id)
        // in some OJS versions. We guard against duplicate inserts with try/catch rather than
        // altering the schema (OJS upgrade path concern).
        try {
            $sub = new IndividualSubscription();
            $sub->setJournalId($journalId);
            $sub->setUserId($userId);
            $sub->setTypeId($typeId);
            $sub->setStatus(self::STATUS_ACTIVE);
            $sub->setDateStart($dateStart);
            $sub->setDateEnd($dateEnd);
            $sub->setNotes('Synced from WP');

            $dao->insertObject($sub);

            return $this->jsonResponse($request, [
                'subscriptionId' => $sub->getId(),
            ]);
        } catch (\Illuminate\Database\QueryException $e) {
            // Race condition: another request inserted a subscription concurrently.
            // Re-read and update the existing subscription instead.
            $raceSub = $dao->getByUserIdForJournal($userId, $journalId);
            if ($raceSub) {
                $raceSub->setStatus(self::STATUS_ACTIVE);
                $raceSub->setTypeId($typeId);
                $raceSub->setDateStart($dateStart);
                $raceSub->setDateEnd($dateEnd);
                $dao->updateObject($raceSub);

                return $this->jsonResponse($request, [
                    'subscriptionId' => $raceSub->getId(),
                ]);
            }

            error_log('[wpojs-api] createSubscription failed for userId=' . $userId . ': ' . $e->getMessage());
            return $this->jsonResponse($request, ['error' => 'Failed to create subscription'], Response::HTTP_INTERNAL_SERVER_ERROR);
        }
    }

    // ---------------------------------------------------------------
    // PUT /wpojs/subscriptions/{subscriptionId}/expire
    // ---------------------------------------------------------------

    public function expireSubscription(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $subscriptionId = (int) $request->route('subscriptionId');

        if ($subscriptionId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid subscriptionId'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) return $journalId;
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getById($subscriptionId);

        if (!$sub || (int) $sub->getJournalId() !== $journalId) {
            return $this->jsonResponse($request, ['error' => 'Subscription not found'], Response::HTTP_NOT_FOUND);
        }

        // Idempotent: setting to OTHER even if already OTHER is fine
        $sub->setStatus(self::STATUS_OTHER);
        $dao->updateObject($sub);

        return $this->jsonResponse($request, ['subscriptionId' => $sub->getId()]);
    }

    // ---------------------------------------------------------------
    // PUT /wpojs/subscriptions/expire-by-user/{userId}
    // Convenience: expire by userId (saves WP plugin an extra lookup).
    // ---------------------------------------------------------------

    public function expireSubscriptionByUser(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $userId = (int) $request->route('userId');

        if ($userId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) return $journalId;
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($userId, $journalId);

        if (!$sub) {
            return $this->jsonResponse($request, ['error' => 'No subscription found for this user in this journal'], Response::HTTP_NOT_FOUND);
        }

        $sub->setStatus(self::STATUS_OTHER);
        $dao->updateObject($sub);

        return $this->jsonResponse($request, ['subscriptionId' => $sub->getId()]);
    }

    // ---------------------------------------------------------------
    // POST /wpojs/subscriptions/status-batch
    // Batch subscription status check. Accepts up to 500 emails.
    // ---------------------------------------------------------------

    public function getSubscriptionStatusBatch(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $emails = $request->input('emails', []);

        if (!is_array($emails) || empty($emails)) {
            return $this->jsonResponse($request, ['error' => 'Provide a non-empty emails array'], Response::HTTP_BAD_REQUEST);
        }

        if (count($emails) > 500) {
            return $this->jsonResponse($request, ['error' => 'Maximum 500 emails per batch'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) return $journalId;
        $results = [];

        // Batch-lookup users by email
        $emailToUserId = [];
        foreach ($emails as $email) {
            if (!is_string($email)) {
                $results[] = ['email' => (string) $email, 'status' => 'error', 'error' => 'Invalid email format'];
                continue;
            }
            $email = trim($email);
            if (empty($email)) {
                continue;
            }
            if (!filter_var($email, FILTER_VALIDATE_EMAIL)) {
                $results[$email] = ['active' => false, 'found' => false, 'error' => 'invalid email'];
                continue;
            }
            $user = Repo::user()->getByEmail($email, true);
            if ($user) {
                $emailToUserId[$email] = $user->getId();
            } else {
                $results[$email] = ['active' => false, 'found' => false];
            }
        }

        // Batch-lookup subscriptions for found users
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        foreach ($emailToUserId as $email => $userId) {
            $sub = $dao->getByUserIdForJournal($userId, $journalId);
            if ($sub && (int) $sub->getStatus() === self::STATUS_ACTIVE) {
                $results[$email] = [
                    'active' => true,
                    'found' => true,
                    'subscriptionId' => $sub->getId(),
                    'typeId' => $sub->getTypeId(),
                    'dateEnd' => $sub->getDateEnd(),
                ];
            } else {
                $results[$email] = [
                    'active' => false,
                    'found' => true,
                    'userId' => $userId,
                ];
            }
        }

        return $this->jsonResponse($request, ['results' => $results]);
    }

    // ---------------------------------------------------------------
    // GET /wpojs/subscriptions?email=...&userId=...
    // ---------------------------------------------------------------

    public function getSubscriptions(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $email = $request->query('email');
        $queryUserId = $request->query('userId');

        if (empty($email) && empty($queryUserId)) {
            return $this->jsonResponse($request, ['error' => 'Provide email or userId query parameter'], Response::HTTP_BAD_REQUEST);
        }

        if (!empty($email) && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            return $this->jsonResponse($request, ['error' => 'Invalid email format'], Response::HTTP_BAD_REQUEST);
        }

        if (!empty($queryUserId) && (int) $queryUserId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalIdOrFail();
        if ($journalId instanceof JsonResponse) return $journalId;

        $resolvedUserId = null;
        if (!empty($email)) {
            $user = Repo::user()->getByEmail($email, true);
            if (!$user) {
                return $this->jsonResponse($request, []);
            }
            $resolvedUserId = $user->getId();
        } else {
            $resolvedUserId = (int) $queryUserId;
        }

        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($resolvedUserId, $journalId);

        if (!$sub) {
            return $this->jsonResponse($request, []);
        }

        return $this->jsonResponse($request, [
            [
                'subscriptionId' => $sub->getId(),
                'userId' => $sub->getUserId(),
                'journalId' => $sub->getJournalId(),
                'typeId' => $sub->getTypeId(),
                'status' => $sub->getStatus(),
                'dateStart' => $sub->getDateStart(),
                'dateEnd' => $sub->getDateEnd(),
            ],
        ]);
    }

    // ---------------------------------------------------------------
    // POST /wpojs/welcome-email
    // ---------------------------------------------------------------

    public function sendWelcomeEmail(Request $request): JsonResponse
    {
        $authError = $this->checkAuth($request);
        if ($authError) {
            return $authError;
        }

        $userId = (int) $request->input('userId', 0);

        if ($userId <= 0) {
            return $this->jsonResponse($request, ['error' => 'Invalid or missing userId'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->get($userId);
        if (!$user) {
            return $this->jsonResponse($request, ['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        // Dedup: skip if already sent
        $alreadySent = DB::table('user_settings')
            ->where('user_id', $userId)
            ->where('setting_name', 'wpojs_welcome_email_sent')
            ->exists();

        if ($alreadySent) {
            return $this->jsonResponse($request, [
                'sent' => false,
                'reason' => 'Welcome email already sent',
            ]);
        }

        $sent = $this->doSendWelcomeEmail($userId);

        if (!$sent) {
            return $this->jsonResponse($request, ['error' => 'Failed to send welcome email'], Response::HTTP_INTERNAL_SERVER_ERROR);
        }

        return $this->jsonResponse($request, ['sent' => true]);
    }

    // ---------------------------------------------------------------
    // Internal: send the welcome/password-reset email
    // Returns true on success, false on failure.
    // ---------------------------------------------------------------

    private function doSendWelcomeEmail(int $userId): bool
    {
        $user = Repo::user()->get($userId);
        if (!$user) {
            return false;
        }

        // Set dedup flag first (prevents concurrent duplicate sends).
        // insertOrIgnore returns 0 if the row already existed.
        $inserted = DB::table('user_settings')->insertOrIgnore([
            'user_id' => $userId,
            'locale' => '',
            'setting_name' => 'wpojs_welcome_email_sent',
            'setting_value' => '1',
        ]);

        if (!$inserted) {
            // Another request already claimed this send
            return false;
        }

        try {
            $ojsRequest = Application::get()->getRequest();
            $context = $ojsRequest->getContext();
            $site = $ojsRequest->getSite();

            // Generate password reset hash (OJS 3.5 uses Validation, not AccessKeyManager)
            $resetHash = Validation::generatePasswordResetHash($user->getId());

            // Record that a password reset was requested (mirrors OJS login handler)
            Repo::user()->edit($user, [
                'datePasswordResetRequested' => Core::getCurrentDate(),
            ]);

            // Build the password reset URL
            $dispatcher = $ojsRequest->getDispatcher();
            $resetUrl = $dispatcher->url(
                $ojsRequest,
                Application::ROUTE_PAGE,
                null, // current context
                'login',
                'resetPassword',
                [$user->getUsername()],
                ['confirm' => $resetHash]
            );

            // Build the mailable
            $template = Repo::emailTemplate()->getByKey(
                $context->getId(),
                PasswordResetRequested::getEmailTemplateKey()
            );

            $mailable = new PasswordResetRequested($site);
            $mailable->recipients($user);

            // Set password reset URL as template variable
            $mailable->viewData['passwordResetUrl'] = $resetUrl;

            $mailable->body($template->getLocalizedData('body'));
            $mailable->subject($template->getLocalizedData('subject'));

            // Set from address with null safety
            $contactEmail = $context->getData('contactEmail');
            if ($contactEmail) {
                $mailable->from($contactEmail, $context->getData('contactName') ?? '');
            }

            Mail::send($mailable);

            return true;
        } catch (\Exception $e) {
            error_log('[wpojs-api] Welcome email failed for userId=' . $userId . ': ' . $e->getMessage());
            // Email failed — remove dedup flag so it can be retried
            DB::table('user_settings')
                ->where('user_id', $userId)
                ->where('setting_name', 'wpojs_welcome_email_sent')
                ->delete();

            return false;
        }
    }
}
