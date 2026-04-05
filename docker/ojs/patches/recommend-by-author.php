<?php
/**
 * Patch: recommendByAuthor plugin — cleaner "Articles by the same author(s)".
 *
 * Changes:
 * - Rename heading to "Articles by the same author(s)"
 * - Limit to 6 results (was 10)
 * - Remove pagination
 * - Remove issue/journal links (just link to the article)
 * - Title first, author second
 */

$pluginDir = '/var/www/html/plugins/generic/recommendByAuthor';

// --- Patch PHP: change count from 10 to 6 ---
$phpFile = "$pluginDir/RecommendByAuthorPlugin.php";
$php = file_get_contents($phpFile);
if ($php === false) {
    fwrite(STDERR, "recommend-by-author patch: cannot read $phpFile\n");
    exit(1);
}

$php = str_replace(
    'public const RECOMMEND_BY_AUTHOR_PLUGIN_COUNT = 10;',
    'public const RECOMMEND_BY_AUTHOR_PLUGIN_COUNT = 6;',
    $php
);
file_put_contents($phpFile, $php);
echo "recommend-by-author patch: PHP patched (count=6)\n";

// --- Patch template ---
$tplFile = "$pluginDir/templates/articleFooter.tpl";
$newTpl = <<<'TPL'
{**
 * plugins/generic/recommendByAuthor/templates/articleFooter.tpl
 *
 * Patched: "Articles by the same author(s)" — 6 items, no pagination,
 * no journal/issue links, title first.
 *}
{if !$articlesBySameAuthor->wasEmpty()}
	<section id="articlesBySameAuthorList">
		<h2>Articles by the same author(s)</h2>
		<ul>
			{iterate from=articlesBySameAuthor item=articleBySameAuthor}
				{assign var=submission value=$articleBySameAuthor.publishedSubmission}
				{assign var=article value=$articleBySameAuthor.article}
				{assign var=issue value=$articleBySameAuthor.issue}
				{assign var=journal value=$articleBySameAuthor.journal}
				{assign var=publication value=$article->getCurrentPublication()}

				<li>
					<a href="{url router=PKP\core\PKPApplication::ROUTE_PAGE journal=$journal->getPath() page="article" op="view" path=$submission->getBestId() urlLocaleForPage=""}">
						{$publication->getLocalizedFullTitle(null, 'html')|strip_unsafe_html}
					</a>
					{assign var=authors value=""}
					{foreach from=$publication->getData('authors') item=author name=authorLoop}
						{if $authors != ""}{assign var=authors value=$authors|cat:", "}{/if}
						{assign var=authors value=$authors|cat:$author->getFullName()}
					{/foreach}
					<span style="color:#666"> &mdash; {if $authors != ""}{$authors|escape}, {/if}{if $issue}{$issue->getVolume()}.{$issue->getNumber()} ({$issue->getYear()}){/if}</span>
				</li>
			{/iterate}
		</ul>
	</section>
{/if}
TPL;

file_put_contents($tplFile, $newTpl);
echo "recommend-by-author patch: template patched\n";
