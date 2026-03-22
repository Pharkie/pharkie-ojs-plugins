<script>
	$(function() {ldelim}
		$('#inlineHtmlGalleySettings').pkpHandler('$.pkp.controllers.form.AjaxFormHandler');
	{rdelim});
</script>

<form
	class="pkp_form"
	id="inlineHtmlGalleySettings"
	method="POST"
	action="{url router=\PKP\core\PKPApplication::ROUTE_COMPONENT op="manage" category="generic" plugin=$pluginName verb="settings" save=true}"
>
	{csrf}

	{fbvFormArea id="inlineHtmlGalleySettingsArea" title="plugins.generic.inlineHtmlGalley.settings"}

		{fbvFormSection title="plugins.generic.inlineHtmlGalley.settings.organisationName" description="plugins.generic.inlineHtmlGalley.settings.organisationNameDesc"}
			{fbvElement type="text" id="organisationName" value=$organisationName}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.inlineHtmlGalley.settings.membershipUrl" description="plugins.generic.inlineHtmlGalley.settings.membershipUrlDesc"}
			{fbvElement type="text" id="membershipUrl" value=$membershipUrl}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.inlineHtmlGalley.settings.paywallSectionName" description="plugins.generic.inlineHtmlGalley.settings.paywallSectionNameDesc"}
			{fbvElement type="text" id="paywallSectionName" value=$paywallSectionName}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.inlineHtmlGalley.settings.archiveNotice"}
			{fbvElement type="checkbox" id="archiveNoticeEnabled" checked=$archiveNoticeEnabled label="plugins.generic.inlineHtmlGalley.settings.archiveNoticeEnabledDesc"}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.inlineHtmlGalley.settings.messages" description="plugins.generic.inlineHtmlGalley.settings.messagesDesc"}
			{fbvElement type="text" id="syncedMemberMessage" value=$syncedMemberMessage label="plugins.generic.inlineHtmlGalley.settings.syncedMemberMessage"}
			{fbvElement type="text" id="subscriberMessage" value=$subscriberMessage label="plugins.generic.inlineHtmlGalley.settings.subscriberMessage"}
			{fbvElement type="text" id="purchaseMessage" value=$purchaseMessage label="plugins.generic.inlineHtmlGalley.settings.purchaseMessage"}
			{fbvElement type="text" id="adminMessage" value=$adminMessage label="plugins.generic.inlineHtmlGalley.settings.adminMessage"}
		{/fbvFormSection}

	{/fbvFormArea}

	{fbvFormButtons submitText="common.save"}
</form>
