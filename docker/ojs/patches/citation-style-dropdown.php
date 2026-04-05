<?php
/**
 * Patch: citationStyleLanguage plugin — cleaner citation UX.
 *
 * - Heading: "Cite This Article"
 * - Native <select> with "Format" label
 * - Download links as plain text (not dropdown)
 * - No border around controls
 */

$pluginDir = '/var/www/html/plugins/generic/citationStyleLanguage';

// --- Patch template ---
$tplFile = "$pluginDir/templates/citationblock.tpl";
$newTpl = <<<'TPL'
{* Cite This Article — patched *}
{if $citation}
	<div class="item citation">
		<section class="sub_item citation_display">
			<h2 class="label">
				Cite This Article
			</h2>
			<div class="value">
				<div id="citationOutput" role="region" aria-live="polite">
					{$citation}
				</div>
				<div class="citation_formats">
					<label for="citationStyleSelect" style="font-size:13px;color:#666;">Format</label>
					<select id="citationStyleSelect" aria-label="Citation format">
						{foreach from=$citationStyles item="citationStyle"}
							<option
								value="{url page="citationstylelanguage" op="get" path=$citationStyle.id params=$citationArgsJson}"
								{if $citationStyle.isPrimary}selected{/if}
							>
								{$citationStyle.title|escape}
							</option>
						{/foreach}
					</select>
					{if count($citationDownloads)}
						<div class="citation_downloads" style="margin-top:6px;font-size:13px;color:#666;">
							Download:
							{foreach from=$citationDownloads item="citationDownload" name=dlLoop}
								<a href="{url page="citationstylelanguage" op="download" path=$citationDownload.id params=$citationArgs}">{if $citationDownload.id == "ris"}RIS{elseif $citationDownload.id == "bibtex"}BibTeX{else}{$citationDownload.title|escape}{/if}</a>{if !$smarty.foreach.dlLoop.last} &middot; {/if}
							{/foreach}
						</div>
					{/if}
				</div>
			</div>
		</section>
	</div>
{/if}
TPL;

file_put_contents($tplFile, $newTpl);
echo "citation-style-dropdown patch: template patched\n";

// --- Patch JS: handle <select> change ---
$jsFile = "$pluginDir/js/articleCitation.js";
$newJs = <<<'JS'
/**
 * Patched: citation format switching via native <select>.
 */
document.addEventListener('DOMContentLoaded', () => {
	const citationOutput = document.getElementById('citationOutput');
	const styleSelect = document.getElementById('citationStyleSelect');

	if (citationOutput && styleSelect) {
		styleSelect.addEventListener('change', () => {
			const jsonHref = styleSelect.value;
			if (!jsonHref) return;
			citationOutput.style.opacity = '0.5';
			fetch(jsonHref)
				.then(r => r.json())
				.then(data => {
					citationOutput.innerHTML = data.content;
					citationOutput.style.opacity = '1';
				})
				.catch(() => {
					citationOutput.style.opacity = '1';
				});
		});
	}
});
JS;

file_put_contents($jsFile, $newJs);
echo "citation-style-dropdown patch: JS patched\n";
