{**
 * plugins/paymethod/stripe/templates/paymentForm.tpl
 *
 * Stripe payment redirect page (shown briefly before redirect to Stripe Checkout).
 *}
{include file="frontend/components/header.tpl" pageTitle="plugins.paymethod.stripe"}

<div class="page page_payment_form">
	<h1 class="page_title">
		{translate key="plugins.paymethod.stripe"}
	</h1>
	<p>{translate key="plugins.paymethod.stripe.redirecting"}</p>
</div>

{include file="frontend/components/footer.tpl"}
