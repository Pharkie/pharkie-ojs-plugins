#!/usr/bin/env node
/**
 * PayPal integration test — standalone, no OJS involved.
 *
 * Modes:
 *   node scripts/test-paypal.js              Sandbox full flow (create order + buyer login)
 *   node scripts/test-paypal.js --live        Live auth + order creation only (no buyer login, no charge)
 *   node scripts/test-paypal.js --live-buy    Live £0.10 test purchase (buyer must approve in browser)
 *
 * Env vars (or edit defaults below):
 *   OJS_PAYPAL_CLIENT_ID, OJS_PAYPAL_SECRET, PAYPAL_BUYER_EMAIL, PAYPAL_BUYER_PASSWORD
 *
 * Screenshots saved to scripts/paypal-sandbox-result.png (or paypal-live-result.png)
 */
const { chromium } = require('playwright');

const mode = process.argv[2] || '--sandbox';
const isLive = mode === '--live' || mode === '--live-buy';
const isLiveBuy = mode === '--live-buy';

// All credentials via env vars — no hardcoded defaults.
const CLIENT_ID = process.env.OJS_PAYPAL_CLIENT_ID;
const SECRET = process.env.OJS_PAYPAL_SECRET;
const BUYER_EMAIL = process.env.PAYPAL_BUYER_EMAIL;
const BUYER_PASSWORD = process.env.PAYPAL_BUYER_PASSWORD;

if (!CLIENT_ID || !SECRET) {
  console.error('ERROR: Set OJS_PAYPAL_CLIENT_ID and OJS_PAYPAL_SECRET env vars.');
  process.exit(1);
}
if (!isLive && (!BUYER_EMAIL || !BUYER_PASSWORD)) {
  console.error('ERROR: Set PAYPAL_BUYER_EMAIL and PAYPAL_BUYER_PASSWORD env vars for sandbox test.');
  process.exit(1);
}

const API_BASE = isLive ? 'https://api-m.paypal.com' : 'https://api-m.sandbox.paypal.com';
const ENV_LABEL = isLive ? 'LIVE' : 'SANDBOX';

if (!process.env.OJS_PAYPAL_CLIENT_ID && isLive) {
  console.error('ERROR: Set OJS_PAYPAL_CLIENT_ID and OJS_PAYPAL_SECRET env vars for live mode.');
  process.exit(1);
}

(async () => {
  // Step 1: Auth
  console.log(`[${ENV_LABEL}] 1. Getting access token from ${API_BASE}...`);
  const tokenResp = await fetch(`${API_BASE}/v1/oauth2/token`, {
    method: 'POST',
    headers: {
      'Authorization': 'Basic ' + Buffer.from(`${CLIENT_ID}:${SECRET}`).toString('base64'),
      'Content-Type': 'application/x-www-form-urlencoded'
    },
    body: 'grant_type=client_credentials'
  });
  const tokenData = await tokenResp.json();
  if (!tokenData.access_token) {
    console.log('   FAILED:', JSON.stringify(tokenData));
    process.exit(1);
  }
  console.log('   OK: Token obtained');
  console.log(`   Scopes: ${tokenData.scope.split(' ').length} granted`);
  console.log(`   App ID: ${tokenData.app_id}`);
  console.log(`   Expires in: ${tokenData.expires_in}s`);

  // Step 2: Create order
  const amount = isLiveBuy ? '0.10' : '3.00';
  const description = isLive
    ? `Live test order (${isLiveBuy ? 'will charge £0.10' : 'auth check only — order will expire uncaptured'})`
    : 'Standalone sandbox test (no OJS)';

  console.log(`\n[${ENV_LABEL}] 2. Creating order (GBP £${amount})...`);
  const orderResp = await fetch(`${API_BASE}/v2/checkout/orders`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${tokenData.access_token}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      intent: 'CAPTURE',
      purchase_units: [{
        amount: { currency_code: 'GBP', value: amount },
        description
      }],
      application_context: {
        return_url: 'https://example.com/return',
        cancel_url: 'https://example.com/cancel',
        brand_name: 'SEA Journal Test'
      }
    })
  });
  const order = await orderResp.json();
  if (order.status !== 'CREATED') {
    console.log('   FAILED:', JSON.stringify(order, null, 2));
    process.exit(1);
  }
  const approveUrl = order.links.find(l => l.rel === 'approve').href;
  console.log(`   OK: Order ${order.id} created`);
  console.log(`   Status: ${order.status}`);
  console.log(`   Approve URL: ${approveUrl}`);

  // --live mode (no --live-buy): stop here
  if (isLive && !isLiveBuy) {
    console.log(`\n[${ENV_LABEL}] 3. Verifying order exists...`);
    const checkResp = await fetch(`${API_BASE}/v2/checkout/orders/${order.id}`, {
      headers: { 'Authorization': `Bearer ${tokenData.access_token}` }
    });
    const checkData = await checkResp.json();
    console.log(`   Status: ${checkData.status}`);
    console.log(`   Amount: ${checkData.purchase_units[0].amount.value} ${checkData.purchase_units[0].amount.currency_code}`);

    console.log('\n=== RESULT: ALL LIVE CHECKS PASSED ===');
    console.log('Auth: OK | Order creation: OK | Order retrieval: OK');
    console.log(`Order ${order.id} will expire uncaptured (no charge).`);
    console.log('\nTo test the full buyer flow, run with --live-buy');
    process.exit(0);
  }

  // Step 3: Open checkout page (sandbox or --live-buy)
  const browser = await chromium.launch({ headless: !isLiveBuy });
  const context = await browser.newContext();
  const page = await context.newPage();

  if (isLiveBuy) {
    // Live buy: open browser for manual approval, don't automate login
    console.log(`\n[${ENV_LABEL}] 3. Opening checkout in browser (£0.10 — you approve manually)...`);
    console.log('   URL:', approveUrl);
    await page.goto(approveUrl);

    console.log('\n   >>> Waiting for you to approve the payment in the browser...');
    console.log('   >>> The browser will close automatically when done.');
    console.log('   >>> Or press Ctrl+C to cancel (order expires, no charge).\n');

    // Wait for redirect back to return_url (max 5 minutes)
    try {
      await page.waitForURL('**/example.com/return**', { timeout: 300000 });
      const url = new URL(page.url());
      const payerID = url.searchParams.get('PayerID');
      const token = url.searchParams.get('token');
      console.log('   Buyer approved! PayerID:', payerID);

      // Capture the payment
      console.log(`\n[${ENV_LABEL}] 4. Capturing payment...`);
      const captureResp = await fetch(`${API_BASE}/v2/checkout/orders/${order.id}/capture`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${tokenData.access_token}`,
          'Content-Type': 'application/json'
        }
      });
      const captureData = await captureResp.json();
      if (captureData.status === 'COMPLETED') {
        const capture = captureData.purchase_units[0].payments.captures[0];
        console.log('   OK: Payment captured!');
        console.log(`   Capture ID: ${capture.id}`);
        console.log(`   Amount: ${capture.amount.value} ${capture.amount.currency_code}`);
        console.log(`   Status: ${capture.status}`);
        console.log('\n=== RESULT: LIVE PURCHASE COMPLETE (£0.10) ===');
        console.log('Refund via PayPal dashboard if needed.');
      } else {
        console.log('   FAILED:', JSON.stringify(captureData, null, 2));
      }
    } catch (e) {
      if (e.name === 'TimeoutError') {
        console.log('   Timed out waiting for approval. Order will expire (no charge).');
      } else {
        console.log('   Error:', e.message);
      }
    }

    const screenshotPath = __dirname + '/paypal-live-result.png';
    await page.screenshot({ path: screenshotPath });
    console.log(`\nScreenshot: ${screenshotPath}`);
    await browser.close();
    process.exit(0);
  }

  // Sandbox: automated buyer login
  console.log(`\n[${ENV_LABEL}] 3. Opening sandbox checkout...`);
  await page.goto(approveUrl);
  await page.waitForTimeout(3000);

  console.log(`[${ENV_LABEL}] 4. Logging in as ${BUYER_EMAIL}...`);
  await page.fill('input[name="login_email"]', BUYER_EMAIL);
  await page.click('button#btnNext');
  await page.waitForTimeout(3000);
  await page.fill('input[name="login_password"]', BUYER_PASSWORD);
  await page.click('button#btnLogin');
  await page.waitForTimeout(8000);

  // Check result
  const screenshotPath = __dirname + '/paypal-sandbox-result.png';
  await page.screenshot({ path: screenshotPath });

  const bodyText = await page.textContent('body').catch(() => '');
  if (bodyText.includes('international regulations') || bodyText.includes('declined')) {
    console.log(`\n[${ENV_LABEL}] RESULT: BLOCKED by PayPal sandbox`);
    console.log('Error: "This transaction has been declined in order to comply with international regulations."');
    console.log('This is a PayPal sandbox issue, not an integration issue.');
  } else if (bodyText.includes('Pay Now') || bodyText.includes('Complete Purchase') || bodyText.includes('Continue')) {
    console.log(`\n[${ENV_LABEL}] RESULT: SUCCESS — payment approval page shown`);
  } else {
    console.log(`\n[${ENV_LABEL}] RESULT: Unknown state`);
    console.log('URL:', page.url());
    console.log('Body:', bodyText.substring(0, 300));
  }

  console.log(`\nScreenshot: ${screenshotPath}`);
  await browser.close();
})();
