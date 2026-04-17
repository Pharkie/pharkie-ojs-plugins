{**
 * plugins/generic/similarArticles/templates/articleFooter.tpl
 *
 * Renders a "Related articles" sidebar on the article page footer.
 * Layout mirrors the project's existing recommendBySimilarity patched
 * template so the user-facing appearance is unchanged.
 *
 * Backing data: $similarArticles is an ordered array of Submission
 * objects (already in rank order 1..5), assigned by
 * SimilarArticlesPlugin::renderFooter().
 *}
{if !empty($similarArticles)}
	<section id="similarArticlesList">
		<h2 class="label" id="similarArticles">
			Related articles
		</h2>
		<ul>
			{foreach from=$similarArticles item=submission}
				{assign var=publication value=$submission->getCurrentPublication()}

				<li>
					<a href="{url router=PKP\core\PKPApplication::ROUTE_PAGE journal=$currentContext->getPath() page="article" op="view" path=$submission->getBestId() urlLocaleForPage=""}">
						{$publication->getLocalizedFullTitle(null, 'html')|strip_unsafe_html}
					</a>
					{* Escape each author name before concatenation rather than at
					   the end — getFullName() can contain characters that break
					   the separator (e.g. a comma inside the name), and
					   defence-in-depth against any upstream XSS in name data. *}
					{assign var=authors value=""}
					{foreach from=$publication->getData('authors') item=author}
						{if $authors != ""}{assign var=authors value=$authors|cat:", "}{/if}
						{assign var=authors value=$authors|cat:($author->getFullName()|escape)}
					{/foreach}
					{if $authors != ""}<span style="color:#666"> &mdash; {$authors}</span>{/if}
				</li>
			{/foreach}
		</ul>
	</section>
{/if}
