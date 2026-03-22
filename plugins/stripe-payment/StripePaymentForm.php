<?php

/**
 * @file plugins/paymethod/stripe/StripePaymentForm.php
 *
 * @class StripePaymentForm
 *
 * @brief Creates a Stripe Checkout Session and redirects the buyer.
 */

namespace APP\plugins\paymethod\stripe;

use APP\core\Application;
use APP\core\Request;
use APP\template\TemplateManager;
use PKP\form\Form;
use PKP\payment\QueuedPayment;

class StripePaymentForm extends Form
{
    /** @var StripePaymentPlugin */
    private $_plugin;

    /** @var QueuedPayment */
    private $_queuedPayment;

    public function __construct(StripePaymentPlugin $plugin, QueuedPayment $queuedPayment)
    {
        $this->_plugin = $plugin;
        $this->_queuedPayment = $queuedPayment;
        parent::__construct(null);
    }

    /**
     * @copydoc Form::display()
     *
     * Creates a Stripe Checkout Session and redirects the buyer to Stripe's hosted page.
     */
    public function display($request = null, $template = null)
    {
        try {
            $journal = $request->getJournal();
            $contextId = $journal->getId();
            $paymentManager = Application::get()->getPaymentManager($journal);

            $secretKey = $this->_plugin->getSetting($contextId, 'secretKey');
            if (empty($secretKey)) {
                throw new \Exception('Stripe secret key not configured');
            }

            $stripe = new \Stripe\StripeClient($secretKey);

            // Amount in smallest currency unit (pence for GBP)
            $amount = (int) round($this->_queuedPayment->getAmount() * 100);
            $currency = strtolower($this->_queuedPayment->getCurrencyCode());

            // Build return URL: OJS routes /payment/plugin/{PluginName}/{op} to handle()
            // Stripe requires literal {CHECKOUT_SESSION_ID} template — OJS url() would encode the braces
            $baseReturnUrl = $request->url(
                null,
                'payment',
                'plugin',
                [$this->_plugin->getName(), 'return'],
                [
                    'queuedPaymentId' => $this->_queuedPayment->getId(),
                ]
            );
            $returnUrl = $baseReturnUrl . '&session_id={CHECKOUT_SESSION_ID}';

            $cancelUrl = $this->_queuedPayment->getRequestUrl() ?: $request->url(null, 'index');

            $session = $stripe->checkout->sessions->create([
                'payment_method_types' => ['card'],
                'mode' => 'payment',
                'line_items' => [[
                    'price_data' => [
                        'currency' => $currency,
                        'unit_amount' => $amount,
                        'product_data' => [
                            'name' => $paymentManager->getPaymentName($this->_queuedPayment),
                        ],
                    ],
                    'quantity' => 1,
                ]],
                'metadata' => [
                    'queuedPaymentId' => (string) $this->_queuedPayment->getId(),
                ],
                'success_url' => $returnUrl,
                'cancel_url' => $cancelUrl,
            ]);

            // Redirect buyer to Stripe Checkout
            $request->redirectUrl($session->url);
        } catch (\Exception $e) {
            error_log('Stripe checkout error: ' . $e->getMessage());

            $context = Application::get()->getRequest()->getContext();
            $contactEmail = $context ? $context->getData('contactEmail') : '';

            $userMessage = 'Payment could not be initiated. Please try again.';
            if ($e instanceof \Stripe\Exception\AuthenticationException) {
                $userMessage = 'Payment system configuration error. Please contact the journal.';
            }
            if (!empty($contactEmail)) {
                $userMessage .= ' If the problem persists, please contact ' . htmlspecialchars($contactEmail) . '.';
            }

            $templateMgr = TemplateManager::getManager($request);
            $templateMgr->assign('message', $userMessage);
            $templateMgr->display('frontend/pages/message.tpl');
        }
    }
}
