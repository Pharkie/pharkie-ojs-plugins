#!/usr/bin/env node
/**
 * Stripe integration test — standalone, no OJS involved.
 *
 * Modes:
 *   node scripts/dev/test-stripe.js              Test mode: auth + create checkout session
 *   node scripts/dev/test-stripe.js --live       Live auth + order creation only (no charge)
 *   node scripts/dev/test-stripe.js --live-buy   Live £0.10 test purchase (opens browser)
 *
 * Env vars:
 *   OJS_STRIPE_SECRET_KEY   (required)
 *   OJS_STRIPE_PUBLISHABLE_KEY (optional, for --live-buy browser checkout)
 *
 * Screenshots saved to scripts/stripe-{mode}-result.png
 */
const { chromium } = require('playwright');

const mode = process.argv[2] || '--test';
const isLive = mode === '--live' || mode === '--live-buy';
const isLiveBuy = mode === '--live-buy';

const SECRET_KEY = process.env.OJS_STRIPE_SECRET_KEY;
if (!SECRET_KEY) {
  console.error('ERROR: Set OJS_STRIPE_SECRET_KEY env var.');
  console.error('  Test keys: https://dashboard.stripe.com/test/apikeys');
  process.exit(1);
}

const ENV_LABEL = isLive ? 'LIVE' : 'TEST';
const amount = isLiveBuy ? 10 : 300; // pence
const amountLabel = isLiveBuy ? '£0.10' : '£3.00';

(async () => {
  // Step 1: Verify API key
  console.log(`[${ENV_LABEL}] 1. Verifying Stripe API key...`);
  const stripe = require('stripe')(SECRET_KEY);

  try {
    const balance = await stripe.balance.retrieve();
    console.log('   OK: API key valid');
    console.log(`   Available: ${balance.available.map(b => `${(b.amount / 100).toFixed(2)} ${b.currency.toUpperCase()}`).join(', ') || 'none'}`);
    console.log(`   Pending: ${balance.pending.map(b => `${(b.amount / 100).toFixed(2)} ${b.currency.toUpperCase()}`).join(', ') || 'none'}`);
  } catch (e) {
    console.log(`   FAILED: ${e.message}`);
    process.exit(1);
  }

  // Step 2: Create Checkout Session
  console.log(`\n[${ENV_LABEL}] 2. Creating Checkout Session (GBP ${amountLabel})...`);
  let session;
  try {
    session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      mode: 'payment',
      line_items: [{
        price_data: {
          currency: 'gbp',
          unit_amount: amount,
          product_data: {
            name: `Standalone test (no OJS) - ${ENV_LABEL}`,
          },
        },
        quantity: 1,
      }],
      metadata: {
        test: 'true',
        source: 'scripts/dev/test-stripe.js',
      },
      success_url: 'https://example.com/return?session_id={CHECKOUT_SESSION_ID}',
      cancel_url: 'https://example.com/cancel',
    });
    console.log(`   OK: Session ${session.id}`);
    console.log(`   Status: ${session.status}`);
    console.log(`   Payment status: ${session.payment_status}`);
    console.log(`   URL: ${session.url}`);
  } catch (e) {
    console.log(`   FAILED: ${e.message}`);
    process.exit(1);
  }

  // Step 3: Verify session retrieval
  console.log(`\n[${ENV_LABEL}] 3. Verifying session retrieval...`);
  try {
    const retrieved = await stripe.checkout.sessions.retrieve(session.id);
    console.log(`   OK: Retrieved session ${retrieved.id}`);
    console.log(`   Amount: ${retrieved.amount_total / 100} ${retrieved.currency.toUpperCase()}`);
    console.log(`   Metadata: ${JSON.stringify(retrieved.metadata)}`);
  } catch (e) {
    console.log(`   FAILED: ${e.message}`);
    process.exit(1);
  }

  // --live (no buy): stop here
  if (isLive && !isLiveBuy) {
    console.log(`\n=== RESULT: ALL ${ENV_LABEL} CHECKS PASSED ===`);
    console.log('Auth: OK | Session creation: OK | Session retrieval: OK');
    console.log(`Session ${session.id} will expire (no charge).`);
    console.log('\nTo test the full buyer flow, run with --live-buy');
    process.exit(0);
  }

  // Step 4: Open checkout page
  const browser = await chromium.launch({ headless: !isLiveBuy });
  const context = await browser.newContext();
  const page = await context.newPage();

  if (isLiveBuy) {
    // Live buy: open browser for manual checkout
    console.log(`\n[${ENV_LABEL}] 4. Opening Stripe Checkout in browser (${amountLabel})...`);
    console.log('   URL:', session.url);
    await page.goto(session.url);

    console.log('\n   >>> Complete the payment in the browser.');
    console.log('   >>> The browser will close when done.');
    console.log(`   >>> Or press Ctrl+C to cancel (session expires, no charge).\n`);

    try {
      await page.waitForURL('**/example.com/return**', { timeout: 300000 });
      const url = new URL(page.url());
      const returnSessionId = url.searchParams.get('session_id');
      console.log('   Payment completed! Session:', returnSessionId);

      // Verify payment
      const completed = await stripe.checkout.sessions.retrieve(returnSessionId);
      console.log(`   Payment status: ${completed.payment_status}`);
      console.log(`   Amount: ${completed.amount_total / 100} ${completed.currency.toUpperCase()}`);

      if (completed.payment_status === 'paid') {
        console.log(`\n=== RESULT: LIVE PURCHASE COMPLETE (${amountLabel}) ===`);
        console.log('Refund via Stripe dashboard if needed.');
      } else {
        console.log(`\n=== RESULT: Payment not completed (status: ${completed.payment_status}) ===`);
      }
    } catch (e) {
      if (e.name === 'TimeoutError') {
        console.log('   Timed out. Session will expire (no charge).');
      } else {
        console.log('   Error:', e.message);
      }
    }

    const screenshotPath = __dirname + '/stripe-live-result.png';
    await page.screenshot({ path: screenshotPath });
    console.log(`\nScreenshot: ${screenshotPath}`);
    await browser.close();
    process.exit(0);
  }

  // Test mode: automated checkout with test card
  console.log(`\n[${ENV_LABEL}] 4. Opening Stripe Checkout...`);
  await page.goto(session.url);
  await page.waitForTimeout(3000);

  console.log(`[${ENV_LABEL}] 5. Filling test card details...`);
  try {
    // Stripe Checkout uses iframes — find the card input
    await page.fill('input[name="cardNumber"], #cardNumber', '4242424242424242');
    await page.fill('input[name="cardExpiry"], #cardExpiry', '12/30');
    await page.fill('input[name="cardCvc"], #cardCvc', '123');
    await page.fill('input[name="billingName"], #billingName', 'Test Buyer');

    // Some Stripe checkouts also need email
    const emailField = await page.$('input[name="email"], #email');
    if (emailField) {
      await emailField.fill('test@example.com');
    }

    await page.screenshot({ path: __dirname + '/stripe-test-filled.png' });

    // Click Pay button
    console.log(`[${ENV_LABEL}] 6. Submitting payment...`);
    await page.click('button[type="submit"], .SubmitButton');
    await page.waitForTimeout(10000);

    console.log('   Final URL:', page.url());
    await page.screenshot({ path: __dirname + '/stripe-test-result.png' });

    if (page.url().includes('example.com/return')) {
      const url = new URL(page.url());
      const returnSessionId = url.searchParams.get('session_id');
      const completed = await stripe.checkout.sessions.retrieve(returnSessionId);

      if (completed.payment_status === 'paid') {
        console.log(`\n=== RESULT: TEST PAYMENT SUCCESSFUL ===`);
        console.log(`Session: ${completed.id}`);
        console.log(`Amount: ${completed.amount_total / 100} ${completed.currency.toUpperCase()}`);
      } else {
        console.log(`\n=== RESULT: Payment status: ${completed.payment_status} ===`);
      }
    } else {
      console.log('\n=== RESULT: Did not redirect to success URL ===');
      const bodyText = await page.textContent('body').catch(() => '');
      console.log('Body:', bodyText.substring(0, 300));
    }
  } catch (e) {
    console.log('   Error during checkout:', e.message);
    await page.screenshot({ path: __dirname + '/stripe-test-error.png' });
  }

  await browser.close();
})();
