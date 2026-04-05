<?php
/**
 * Patch: recommendBySimilarity plugin — cleaner "Related articles" section.
 *
 * Changes:
 * - Rename "Similar Articles" → "Related articles"
 * - Limit to 6 results (was 10)
 * - Remove pagination
 * - Remove issue links (just link to the article)
 * - Title first, author second
 * - Simplify search link text
 */

$pluginDir = '/var/www/html/plugins/generic/recommendBySimilarity';

// --- Patch PHP: change count from 10 to 6 ---
$phpFile = "$pluginDir/RecommendBySimilarityPlugin.php";
$php = file_get_contents($phpFile);
if ($php === false) {
    fwrite(STDERR, "recommend-by-similarity patch: cannot read $phpFile\n");
    exit(1);
}

$php = str_replace(
    'private const DEFAULT_RECOMMENDATION_COUNT = 10;',
    'private const DEFAULT_RECOMMENDATION_COUNT = 6;',
    $php
);
file_put_contents($phpFile, $php);
echo "recommend-by-similarity patch: PHP patched (count=6)\n";

// --- Patch template ---
$tplFile = "$pluginDir/templates/articleFooter.tpl";
$newTpl = <<<'TPL'
{**
 * plugins/generic/recommendBySimilarity/templates/articleFooter.tpl
 *
 * Patched: "Related articles" — 6 items, no pagination, no issue links,
 * title first, simplified search link.
 *}
{if !$articlesBySimilarity->submissions->isEmpty()}
	<section id="articlesBySimilarityList">
		<h2 class="label" id="articlesBySimilarity">
			Related articles
		</h2>
		<ul>
			{foreach from=$articlesBySimilarity->submissions item=submission}
				{assign var=publication value=$submission->getCurrentPublication()}
				{assign var=issue value=$articlesBySimilarity->issues->get($publication->getData('issueId'))}

				<li>
					<a href="{url router=PKP\core\PKPApplication::ROUTE_PAGE journal=$currentContext->getPath() page="article" op="view" path=$submission->getBestId() urlLocaleForPage=""}">
						{$publication->getLocalizedFullTitle(null, 'html')|strip_unsafe_html}
					</a>
					{assign var=authors value=""}
					{foreach from=$publication->getData('authors') item=author name=authorLoop}
						{if $authors != ""}{assign var=authors value=$authors|cat:", "}{/if}
						{assign var=authors value=$authors|cat:$author->getFullName()}
					{/foreach}
					<span style="color:#666"> &mdash; {if $authors != ""}{$authors|escape}, {/if}{if $issue}{$issue->getVolume()}.{$issue->getNumber()} ({$issue->getYear()}){/if}</span>
				</li>
			{/foreach}
		</ul>
		<p id="articlesBySimilaritySearch">
			<a href="{url page="search" op="search" query=$articlesBySimilarity->query}">Search for similar articles &rsaquo;</a>
		</p>
	</section>
{/if}
TPL;

file_put_contents($tplFile, $newTpl);
echo "recommend-by-similarity patch: template patched\n";
