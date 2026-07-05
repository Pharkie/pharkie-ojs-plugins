<script>
	$(function() {ldelim}
		$('#umamiAnalyticsSettings').pkpHandler('$.pkp.controllers.form.AjaxFormHandler');
	{rdelim});
</script>

<form
	class="pkp_form"
	id="umamiAnalyticsSettings"
	method="POST"
	action="{url router=\PKP\core\PKPApplication::ROUTE_COMPONENT op="manage" category="generic" plugin=$pluginName verb="settings" save=true}"
>
	{csrf}

	{fbvFormArea id="umamiAnalyticsSettingsArea" title="plugins.generic.umamiAnalytics.settings"}

		{fbvFormSection title="plugins.generic.umamiAnalytics.settings.websiteId" description="plugins.generic.umamiAnalytics.settings.websiteIdDesc"}
			{fbvElement type="text" id="websiteId" value=$websiteId}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.umamiAnalytics.settings.scriptUrl" description="plugins.generic.umamiAnalytics.settings.scriptUrlDesc"}
			{fbvElement type="text" id="scriptUrl" value=$scriptUrl}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.umamiAnalytics.settings.dataDomains" description="plugins.generic.umamiAnalytics.settings.dataDomainsDesc"}
			{fbvElement type="text" id="dataDomains" value=$dataDomains}
		{/fbvFormSection}

		{fbvFormSection title="plugins.generic.umamiAnalytics.settings.options" list="true"}
			{fbvElement type="checkbox" id="trackEvents" checked=$trackEvents label="plugins.generic.umamiAnalytics.settings.trackEventsDesc"}
			{fbvElement type="checkbox" id="excludeStaff" checked=$excludeStaff label="plugins.generic.umamiAnalytics.settings.excludeStaffDesc"}
		{/fbvFormSection}

	{/fbvFormArea}

	{fbvFormButtons submitText="common.save"}
</form>
