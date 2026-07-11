<?php

namespace APP\plugins\generic\wpojsSubscriptionApi;

use APP\facades\Repo;
use APP\template\TemplateManager;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Mail;
use PKP\config\Config;
use PKP\mail\mailables\PasswordResetRequested;
use PKP\pages\login\LoginHandler;

/**
 * Overrides the lost-password flow for synced member accounts.
 *
 * Members' passwords are managed in WP and pushed to OJS by the sync.
 * A password set via the stock OJS reset flow works only until the
 * member next changes their WP password, then is silently overwritten —
 * a support trap. Instead of a reset link, synced members get an email
 * pointing at the WP reset page. Journal-only accounts (no WP
 * counterpart) keep the stock reset flow.
 *
 * Wired up via the LoadHandler hook for login/requestResetPassword
 * only; every other login op stays on the stock handler.
 */
class WpojsLoginHandler extends LoginHandler
{
    public function requestResetPassword($args, $request)
    {
        // Core validates altcha via a private method this subclass can't
        // reach. If altcha is enabled for this form, keep the stock flow
        // (members then get a normal reset link — acceptable fallback).
        if (Config::getVar('captcha', 'altcha_on_lost_password')) {
            return parent::requestResetPassword($args, $request);
        }

        $wpUrl = (string) Config::getVar('wpojs', 'wp_member_url', '');
        $email = (string) $request->getUserVar('email');
        $user = $email ? Repo::user()->getByEmail($email, true) : null;

        // Stock flow for journal-only accounts, disabled accounts (core
        // shows its own message), unknown emails (core shows the same
        // confirmation either way — no account enumeration), and when
        // no WP URL is configured to point members at.
        if ($wpUrl === '' || !$user || $user->getDisabled() || !$this->isSyncedMember($user->getId())) {
            return parent::requestResetPassword($args, $request);
        }

        $this->setupTemplate($request);

        $wpResetUrl = rtrim($wpUrl, '/') . '/wp-login.php?action=lostpassword';

        // Reuse the stock mailable (from/recipient/template plumbing) but
        // with a body that carries the WP pointer instead of a reset link.
        // {$recipientName} is substituted by the mailable at send time.
        $site = $request->getSite();
        $mailable = (new PasswordResetRequested($site))
            ->recipients($user)
            ->from($site->getLocalizedContactEmail(), $site->getLocalizedContactName())
            ->body(__('plugins.generic.wpojsSubscriptionApi.wpManagedPassword.body', [
                'wpResetUrl' => htmlspecialchars($wpResetUrl, ENT_QUOTES, 'UTF-8'),
            ]))
            ->subject(__('plugins.generic.wpojsSubscriptionApi.wpManagedPassword.subject'));
        Mail::send($mailable);

        // Same confirmation page as the stock flow.
        $templateMgr = TemplateManager::getManager($request);
        $templateMgr->assign([
            'pageTitle' => 'user.login.resetPassword',
            'message' => 'user.login.lostPassword.confirmationSent',
            'backLink' => $request->url(null, $request->getRequestedPage(), null, null),
            'backLinkLabel' => 'user.login',
        ])->display('frontend/pages/message.tpl');
    }

    /**
     * A synced member either was created by the sync (marker setting) or
     * was adopted by it (pre-existing account matched by email — those
     * get a synced subscription). Both have WP-managed passwords.
     */
    private function isSyncedMember(int $userId): bool
    {
        $created = DB::table('user_settings')
            ->where('user_id', $userId)
            ->where('setting_name', 'wpojs_created_by_sync')
            ->exists();
        if ($created) {
            return true;
        }

        return DB::table('subscriptions')
            ->where('user_id', $userId)
            ->where('notes', 'like', 'Synced from WP%')
            ->exists();
    }
}
