<?php

/**
 * @file plugins/paymethod/stripe/StripePaymentPlugin.php
 *
 * @class StripePaymentPlugin
 *
 * @brief Stripe payment plugin for OJS.
 *
 * Implements Stripe Checkout (redirect flow) with webhook confirmation.
 * Two paths to payment fulfillment (belt and suspenders):
 * 1. Redirect callback: buyer pays → Stripe redirects back → verify → grant access
 * 2. Webhook: Stripe sends checkout.session.completed → verify signature → grant access
 */

namespace APP\plugins\paymethod\stripe;

use APP\core\Application;
use APP\core\Request;
use APP\payment\ojs\OJSPaymentManager;
use APP\template\TemplateManager;
use Illuminate\Support\Collection;
use PKP\components\forms\context\PKPPaymentSettingsForm;
use PKP\components\forms\FieldOptions;
use PKP\components\forms\FieldText;
use PKP\db\DAORegistry;
use PKP\payment\QueuedPaymentDAO;
use PKP\plugins\Hook;
use PKP\plugins\PaymethodPlugin;

require_once(dirname(__FILE__) . '/vendor/autoload.php');

class StripePaymentPlugin extends PaymethodPlugin
{
    /**
     * @copydoc Plugin::getName()
     */
    public function getName()
    {
        return 'StripePayment';
    }

    /**
     * @copydoc Plugin::getDisplayName()
     */
    public function getDisplayName()
    {
        return __('plugins.paymethod.stripe.displayName');
    }

    /**
     * @copydoc Plugin::getDescription()
     */
    public function getDescription()
    {
        return __('plugins.paymethod.stripe.description');
    }

    /**
     * @copydoc Plugin::register()
     */
    public function register($category, $path, $mainContextId = null)
    {
        if (!parent::register($category, $path, $mainContextId)) {
            return false;
        }

        $this->addLocaleData();
        Hook::add('Form::config::before', $this->addSettings(...));
        return true;
    }

    /**
     * Add settings to the payments form in Distribution Settings.
     */
    public function addSettings($hookName, $form)
    {
        if ($form->id !== PKPPaymentSettingsForm::FORM_PAYMENT_SETTINGS) {
            return;
        }

        $context = Application::get()->getRequest()->getContext();
        if (!$context) {
            return;
        }

        $contextId = $context->getId();

        $form->addGroup([
            'id' => 'stripepayment',
            'label' => __('plugins.paymethod.stripe.displayName'),
            'showWhen' => 'paymentsEnabled',
        ])
            ->addField(new FieldOptions('stripeTestMode', [
                'label' => __('plugins.paymethod.stripe.settings.testMode'),
                'options' => [
                    ['value' => true, 'label' => __('common.enable')]
                ],
                'value' => (bool) $this->getSetting($contextId, 'testMode'),
                'groupId' => 'stripepayment',
            ]))
            ->addField(new FieldText('stripeSecretKey', [
                'label' => __('plugins.paymethod.stripe.settings.secretKey'),
                'value' => $this->getSetting($contextId, 'secretKey'),
                'groupId' => 'stripepayment',
            ]))
            ->addField(new FieldText('stripePublishableKey', [
                'label' => __('plugins.paymethod.stripe.settings.publishableKey'),
                'value' => $this->getSetting($contextId, 'publishableKey'),
                'groupId' => 'stripepayment',
            ]))
            ->addField(new FieldText('stripeWebhookSecret', [
                'label' => __('plugins.paymethod.stripe.settings.webhookSecret'),
                'value' => $this->getSetting($contextId, 'webhookSecret'),
                'groupId' => 'stripepayment',
            ]));
    }

    /**
     * @copydoc PaymethodPlugin::saveSettings()
     */
    public function saveSettings(string $hookname, array $args)
    {
        $illuminateRequest = $args[0];
        $request = $args[1];
        $updatedSettings = $args[3];

        $allParams = $illuminateRequest->input();
        $contextId = $request->getContext()->getId();

        $settingsMap = [
            'stripeSecretKey' => ['key' => 'secretKey', 'type' => 'string'],
            'stripePublishableKey' => ['key' => 'publishableKey', 'type' => 'string'],
            'stripeWebhookSecret' => ['key' => 'webhookSecret', 'type' => 'string'],
            'stripeTestMode' => ['key' => 'testMode', 'type' => 'bool'],
        ];

        foreach ($settingsMap as $formParam => $config) {
            if (!array_key_exists($formParam, $allParams)) {
                continue;
            }
            $val = $config['type'] === 'bool'
                ? ($allParams[$formParam] === 'true')
                : (string) $allParams[$formParam];
            $this->updateSetting($contextId, $config['key'], $val);
            $updatedSettings->put($formParam, $val);
        }
    }

    /**
     * @copydoc PaymethodPlugin::getPaymentForm()
     */
    public function getPaymentForm($context, $queuedPayment)
    {
        return new StripePaymentForm($this, $queuedPayment);
    }

    /**
     * @copydoc PaymethodPlugin::isConfigured()
     */
    public function isConfigured($context)
    {
        if (!$context) {
            return false;
        }
        return $this->getSetting($context->getId(), 'secretKey') != '';
    }

    /**
     * @copydoc PaymethodPlugin::handle()
     *
     * Routes to return callback or webhook handler based on $args[0].
     */
    public function handle($args, $request)
    {
        $op = $args[0] ?? '';

        switch ($op) {
            case 'return':
                $this->handleReturn($request);
                break;
            case 'webhook':
                $this->handleWebhook($request);
                break;
            default:
                error_log('Stripe: unknown operation: ' . $op);
                $request->redirect(null, 'index');
        }
    }

    /**
     * Handle the return callback after successful Stripe Checkout.
     */
    private function handleReturn(Request $request)
    {
        $journal = $request->getJournal();

        try {
            $sessionId = $request->getUserVar('session_id');
            $queuedPaymentId = (int) $request->getUserVar('queuedPaymentId');

            if (!$sessionId || !$queuedPaymentId) {
                throw new \Exception('Missing session_id or queuedPaymentId');
            }

            $queuedPaymentDao = DAORegistry::getDAO('QueuedPaymentDAO');
            $queuedPayment = $queuedPaymentDao->getById($queuedPaymentId);
            if (!$queuedPayment) {
                throw new \Exception("Invalid queued payment ID {$queuedPaymentId}");
            }

            // Verify with Stripe server-side (never trust client data)
            $stripe = new \Stripe\StripeClient($this->getSetting($journal->getId(), 'secretKey'));
            $session = $stripe->checkout->sessions->retrieve($sessionId);

            if ($session->payment_status !== 'paid') {
                throw new \Exception('Payment not completed. Status: ' . $session->payment_status);
            }

            // Verify amount and currency match
            $expectedAmount = (int) round($queuedPayment->getAmount() * 100);
            if ($session->amount_total !== $expectedAmount) {
                throw new \Exception("Amount mismatch: expected {$expectedAmount}, got {$session->amount_total}");
            }
            if (strtoupper($session->currency) !== strtoupper($queuedPayment->getCurrencyCode())) {
                throw new \Exception("Currency mismatch: expected {$queuedPayment->getCurrencyCode()}, got {$session->currency}");
            }

            // Verify the session metadata matches (prevents cross-payment attacks)
            if (($session->metadata['queuedPaymentId'] ?? '') != $queuedPaymentId) {
                throw new \Exception('Payment metadata mismatch');
            }

            // Fulfill the payment (grants article access)
            $paymentManager = Application::get()->getPaymentManager($journal);
            $paymentManager->fulfillQueuedPayment($request, $queuedPayment, $this->getName());

            // Redirect to the article
            $request->redirectUrl($queuedPayment->getRequestUrl());
        } catch (\Exception $e) {
            error_log('Stripe return error: ' . $e->getMessage());
            $templateMgr = TemplateManager::getManager($request);
            $templateMgr->assign('message', 'plugins.paymethod.stripe.error');
            $templateMgr->display('frontend/pages/message.tpl');
        }
    }

    /**
     * Handle Stripe webhook events.
     *
     * Verifies webhook signature, extracts queuedPaymentId from session metadata,
     * and fulfills the payment if not already done (idempotent).
     */
    private function handleWebhook(Request $request)
    {
        $journal = $request->getJournal();
        $webhookSecret = $this->getSetting($journal->getId(), 'webhookSecret');

        // Read raw POST body (Stripe sends JSON)
        $payload = file_get_contents('php://input');
        $sigHeader = $_SERVER['HTTP_STRIPE_SIGNATURE'] ?? '';

        // Must have webhook secret configured
        if (empty($webhookSecret)) {
            http_response_code(500);
            echo json_encode(['error' => 'Webhook secret not configured']);
            exit;
        }

        try {
            // Verify signature (also rejects stale/replayed events)
            $event = \Stripe\Webhook::constructEvent($payload, $sigHeader, $webhookSecret);
        } catch (\Stripe\Exception\SignatureVerificationException $e) {
            error_log('Stripe webhook signature verification failed: ' . $e->getMessage());
            http_response_code(400);
            echo json_encode(['error' => 'Invalid signature']);
            exit;
        } catch (\UnexpectedValueException $e) {
            error_log('Stripe webhook invalid payload: ' . $e->getMessage());
            http_response_code(400);
            echo json_encode(['error' => 'Invalid payload']);
            exit;
        }

        // Only handle checkout.session.completed
        if ($event->type !== 'checkout.session.completed') {
            http_response_code(200);
            echo json_encode(['status' => 'ignored', 'type' => $event->type]);
            exit;
        }

        $session = $event->data->object;

        // Payment must be complete
        if ($session->payment_status !== 'paid') {
            http_response_code(200);
            echo json_encode(['status' => 'not_paid']);
            exit;
        }

        $queuedPaymentId = $session->metadata['queuedPaymentId'] ?? null;
        if (!$queuedPaymentId) {
            error_log('Stripe webhook: no queuedPaymentId in session metadata');
            http_response_code(200);
            echo json_encode(['status' => 'no_payment_id']);
            exit;
        }

        try {
            $queuedPaymentDao = DAORegistry::getDAO('QueuedPaymentDAO');
            $queuedPayment = $queuedPaymentDao->getById((int) $queuedPaymentId);

            if (!$queuedPayment) {
                // Already fulfilled (redirect callback got there first) — that's fine
                http_response_code(200);
                echo json_encode(['status' => 'already_fulfilled']);
                exit;
            }

            // Verify amount
            $expectedAmount = (int) round($queuedPayment->getAmount() * 100);
            if ($session->amount_total !== $expectedAmount) {
                error_log("Stripe webhook: amount mismatch for payment {$queuedPaymentId}");
                http_response_code(200);
                echo json_encode(['status' => 'amount_mismatch']);
                exit;
            }

            // Fulfill payment
            $paymentManager = Application::get()->getPaymentManager($journal);
            $paymentManager->fulfillQueuedPayment($request, $queuedPayment, $this->getName());

            http_response_code(200);
            echo json_encode(['status' => 'fulfilled']);
            exit;
        } catch (\Exception $e) {
            error_log('Stripe webhook fulfillment error: ' . $e->getMessage());
            http_response_code(500);
            echo json_encode(['error' => 'Fulfillment failed']);
            exit;
        }
    }
}

if (!PKP_STRICT_MODE) {
    class_alias('\APP\plugins\paymethod\stripe\StripePaymentPlugin', '\StripePaymentPlugin');
}
