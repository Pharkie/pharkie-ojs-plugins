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
        $allowedIps = Config::getVar('wpojs', 'allowed_ips', '');

        if (empty($allowedIps)) {
            return response()->json(
                ['error' => 'No allowed IPs configured in config.inc.php [wpojs] section'],
                Response::HTTP_FORBIDDEN
            );
        }

        $allowed = array_map('trim', explode(',', $allowedIps));
        $clientIp = $request->server('REMOTE_ADDR');

        $matched = false;
        foreach ($allowed as $entry) {
            if (str_contains($entry, '/')) {
                // CIDR notation (e.g. 172.16.0.0/12)
                [$subnet, $bits] = explode('/', $entry, 2);
                $subnetLong = ip2long($subnet);
                $clientLong = ip2long($clientIp);
                $mask = -1 << (32 - (int)$bits);
                if ($subnetLong !== false && $clientLong !== false
                    && ($clientLong & $mask) === ($subnetLong & $mask)) {
                    $matched = true;
                    break;
                }
            } elseif ($entry === $clientIp) {
                $matched = true;
                break;
            }
        }

        if (!$matched) {
            return response()->json(
                ['error' => 'IP not allowed'],
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
        $secret = Config::getVar('wpojs', 'api_key_secret', '');
        if (empty($secret)) {
            $secret = Config::getVar('security', 'api_key_secret', '');
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
     * Combined authorization: IP allowlist + API key.
     * Call at the start of every protected endpoint.
     */
    private function checkAuth(Request $request): ?JsonResponse
    {
        return $this->checkIp($request) ?? $this->checkApiKey($request);
    }

    private function getJournalId(): int
    {
        $context = Application::get()->getRequest()->getContext();
        if (!$context) {
            throw new \RuntimeException('No journal context');
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
        return response()->json(['status' => 'ok'], Response::HTTP_OK);
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

        return response()->json([
            'compatible' => $compatible,
            'checks' => $checks,
        ], Response::HTTP_OK);
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
            return response()->json(
                ['error' => 'Provide valid email query parameter'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $user = Repo::user()->getByEmail($email, true);
        if (!$user) {
            return response()->json(['found' => false], Response::HTTP_OK);
        }

        return response()->json([
            'found' => true,
            'userId' => $user->getId(),
            'email' => $user->getEmail(),
            'username' => $user->getUsername(),
            'disabled' => (bool) $user->getDisabled(),
        ], Response::HTTP_OK);
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
            return response()->json(['error' => 'Invalid or missing email'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($firstName) || strlen($firstName) > 255) {
            return response()->json(['error' => 'Invalid or missing firstName'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($lastName) || strlen($lastName) > 255) {
            return response()->json(['error' => 'Invalid or missing lastName'], Response::HTTP_BAD_REQUEST);
        }

        // Check for existing user (include disabled accounts)
        $existingUser = Repo::user()->getByEmail($email, true);
        if ($existingUser) {
            return response()->json([
                'userId' => $existingUser->getId(),
                'created' => false,
            ], Response::HTTP_OK);
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
            $user->setGivenName($firstName, 'en');
            $user->setFamilyName($lastName, 'en');
            $user->setPassword(Validation::encryptCredentials($username, bin2hex(random_bytes(16))));
            $user->setDateRegistered(Core::getCurrentDate());
            $user->setDateValidated(Core::getCurrentDate()); // marks email as verified
            $user->setMustChangePassword(true);
            $user->setDisabled(false);

            $userId = Repo::user()->add($user);

            // Assign Reader role for this journal
            $contextId = $this->getJournalId();
            $readerGroup = Repo::userGroup()
                ->getByRoleIds([Role::ROLE_ID_READER], $contextId)
                ->first();
            if ($readerGroup) {
                Repo::userGroup()->assignUserToGroup(
                    userId: $userId,
                    userGroupId: $readerGroup->getId()
                );
            }
        } catch (\Illuminate\Database\QueryException $e) {
            // Duplicate key = race condition: another request created this user
            $raceUser = Repo::user()->getByEmail($email, true);
            if ($raceUser) {
                // Ensure Reader role is assigned (idempotent — safe if already assigned)
                $contextId = $this->getJournalId();
                $readerGroup = Repo::userGroup()
                    ->getByRoleIds([Role::ROLE_ID_READER], $contextId)
                    ->first();
                if ($readerGroup) {
                    Repo::userGroup()->assignUserToGroup(
                        userId: $raceUser->getId(),
                        userGroupId: $readerGroup->getId()
                    );
                }

                return response()->json([
                    'userId' => $raceUser->getId(),
                    'created' => false,
                ], Response::HTTP_OK);
            }
            return response()->json(
                ['error' => 'Failed to create user'],
                Response::HTTP_INTERNAL_SERVER_ERROR
            );
        } catch (\Exception $e) {
            return response()->json(
                ['error' => 'Failed to create user'],
                Response::HTTP_INTERNAL_SERVER_ERROR
            );
        }

        if ($sendWelcomeEmail) {
            // Best effort — don't fail user creation if email fails
            $this->doSendWelcomeEmail($userId);
        }

        return response()->json([
            'userId' => $userId,
            'created' => true,
        ], Response::HTTP_OK);
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
            return response()->json(['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($newEmail) || !filter_var($newEmail, FILTER_VALIDATE_EMAIL) || strlen($newEmail) > 255) {
            return response()->json(['error' => 'Invalid or missing newEmail'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->get($userId);
        if (!$user) {
            return response()->json(['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        // OJS doesn't enforce uniqueness in edit() — we must check
        $existing = Repo::user()->getByEmail($newEmail, true);
        if ($existing && $existing->getId() !== $userId) {
            return response()->json(
                ['error' => 'Email already in use by another account'],
                Response::HTTP_CONFLICT
            );
        }

        Repo::user()->edit($user, ['email' => $newEmail]);

        return response()->json(['userId' => $userId], Response::HTTP_OK);
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
            return response()->json(['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->get($userId);
        if (!$user) {
            return response()->json(['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        // Anonymise all PII and disable account
        $anonymisedEmail = 'deleted_' . $userId . '@anonymised.invalid';
        Repo::user()->edit($user, [
            'email' => $anonymisedEmail,
            'username' => 'deleted_' . $userId,
            'givenName' => ['en' => 'Deleted'],
            'familyName' => ['en' => 'User'],
            'affiliation' => ['en' => ''],
            'biography' => ['en' => ''],
            'orcid' => '',
            'url' => '',
            'phone' => '',
            'mailingAddress' => '',
            'datePasswordResetRequested' => null,
            'disabled' => true,
        ]);

        // Expire any active subscription in this journal
        $journalId = $this->getJournalId();
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($userId, $journalId);
        if ($sub && (int) $sub->getStatus() === self::STATUS_ACTIVE) {
            $sub->setStatus(self::STATUS_OTHER);
            $dao->updateObject($sub);
        }

        // Remove all user_settings (may contain PII like preferredPublicName)
        DB::table('user_settings')->where('user_id', $userId)->delete();

        return response()->json([
            'deleted' => true,
            'userId' => $userId,
        ], Response::HTTP_OK);
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
            return response()->json(['error' => 'Invalid or missing userId'], Response::HTTP_BAD_REQUEST);
        }
        if ($typeId <= 0) {
            return response()->json(['error' => 'Invalid or missing typeId'], Response::HTTP_BAD_REQUEST);
        }
        if (empty($dateStart) || !$this->isValidDate($dateStart)) {
            return response()->json(['error' => 'Invalid or missing dateStart (expected Y-m-d)'], Response::HTTP_BAD_REQUEST);
        }
        if ($dateEnd !== null && !$this->isValidDate($dateEnd)) {
            return response()->json(['error' => 'Invalid dateEnd (expected Y-m-d or null)'], Response::HTTP_BAD_REQUEST);
        }

        // Verify user exists
        $user = Repo::user()->get($userId);
        if (!$user) {
            return response()->json(['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        $journalId = $this->getJournalId();

        // Verify subscription type exists for this journal
        $typeDao = DAORegistry::getDAO('SubscriptionTypeDAO');
        $type = $typeDao->getById($typeId);
        if (!$type || (int) $type->getJournalId() !== $journalId) {
            return response()->json(
                ['error' => 'Invalid subscription type for this journal'],
                Response::HTTP_BAD_REQUEST
            );
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

            return response()->json([
                'subscriptionId' => $existingSub->getId(),
            ], Response::HTTP_OK);
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

            return response()->json([
                'subscriptionId' => $sub->getId(),
            ], Response::HTTP_OK);
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

                return response()->json([
                    'subscriptionId' => $raceSub->getId(),
                ], Response::HTTP_OK);
            }

            return response()->json(
                ['error' => 'Failed to create subscription'],
                Response::HTTP_INTERNAL_SERVER_ERROR
            );
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
            return response()->json(['error' => 'Invalid subscriptionId'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalId();
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getById($subscriptionId);

        if (!$sub || (int) $sub->getJournalId() !== $journalId) {
            return response()->json(['error' => 'Subscription not found'], Response::HTTP_NOT_FOUND);
        }

        // Idempotent: setting to OTHER even if already OTHER is fine
        $sub->setStatus(self::STATUS_OTHER);
        $dao->updateObject($sub);

        return response()->json(['subscriptionId' => $sub->getId()], Response::HTTP_OK);
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
            return response()->json(['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalId();
        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($userId, $journalId);

        if (!$sub) {
            return response()->json(
                ['error' => 'No subscription found for this user in this journal'],
                Response::HTTP_NOT_FOUND
            );
        }

        $sub->setStatus(self::STATUS_OTHER);
        $dao->updateObject($sub);

        return response()->json(['subscriptionId' => $sub->getId()], Response::HTTP_OK);
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
            return response()->json(
                ['error' => 'Provide a non-empty emails array'],
                Response::HTTP_BAD_REQUEST
            );
        }

        if (count($emails) > 500) {
            return response()->json(
                ['error' => 'Maximum 500 emails per batch'],
                Response::HTTP_BAD_REQUEST
            );
        }

        $journalId = $this->getJournalId();
        $results = [];

        // Batch-lookup users by email
        $emailToUserId = [];
        foreach ($emails as $email) {
            $email = trim($email);
            if (empty($email)) {
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

        return response()->json(['results' => $results], Response::HTTP_OK);
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
            return response()->json(
                ['error' => 'Provide email or userId query parameter'],
                Response::HTTP_BAD_REQUEST
            );
        }

        if (!empty($email) && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            return response()->json(['error' => 'Invalid email format'], Response::HTTP_BAD_REQUEST);
        }

        if (!empty($queryUserId) && (int) $queryUserId <= 0) {
            return response()->json(['error' => 'Invalid userId'], Response::HTTP_BAD_REQUEST);
        }

        $journalId = $this->getJournalId();

        $resolvedUserId = null;
        if (!empty($email)) {
            $user = Repo::user()->getByEmail($email, true);
            if (!$user) {
                return response()->json([], Response::HTTP_OK);
            }
            $resolvedUserId = $user->getId();
        } else {
            $resolvedUserId = (int) $queryUserId;
        }

        $dao = DAORegistry::getDAO('IndividualSubscriptionDAO');
        $sub = $dao->getByUserIdForJournal($resolvedUserId, $journalId);

        if (!$sub) {
            return response()->json([], Response::HTTP_OK);
        }

        return response()->json([
            [
                'subscriptionId' => $sub->getId(),
                'userId' => $sub->getUserId(),
                'journalId' => $sub->getJournalId(),
                'typeId' => $sub->getTypeId(),
                'status' => $sub->getStatus(),
                'dateStart' => $sub->getDateStart(),
                'dateEnd' => $sub->getDateEnd(),
            ],
        ], Response::HTTP_OK);
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
            return response()->json(['error' => 'Invalid or missing userId'], Response::HTTP_BAD_REQUEST);
        }

        $user = Repo::user()->get($userId);
        if (!$user) {
            return response()->json(['error' => 'User not found'], Response::HTTP_NOT_FOUND);
        }

        // Dedup: skip if already sent
        $alreadySent = DB::table('user_settings')
            ->where('user_id', $userId)
            ->where('setting_name', 'wpojs_welcome_email_sent')
            ->exists();

        if ($alreadySent) {
            return response()->json([
                'sent' => false,
                'reason' => 'Welcome email already sent',
            ], Response::HTTP_OK);
        }

        $sent = $this->doSendWelcomeEmail($userId);

        if (!$sent) {
            return response()->json(
                ['error' => 'Failed to send welcome email'],
                Response::HTTP_INTERNAL_SERVER_ERROR
            );
        }

        return response()->json(['sent' => true], Response::HTTP_OK);
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
            // Email failed — remove dedup flag so it can be retried
            DB::table('user_settings')
                ->where('user_id', $userId)
                ->where('setting_name', 'wpojs_welcome_email_sent')
                ->delete();

            return false;
        }
    }
}
