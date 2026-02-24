<div id="wpojs-settings-page" style="max-width:640px;padding:16px;">
    <h3>{translate key="plugins.generic.wpojsSubscriptionApi.displayName"} &mdash; {translate key="plugins.generic.wpojsSubscriptionApi.settings"}</h3>
    <p style="color:#666;font-size:13px;margin-bottom:16px;">{translate key="plugins.generic.wpojsSubscriptionApi.settings.description"}</p>

    <form id="wpojs-settings-form">
        <div style="margin-bottom:16px;">
            <label for="wpojs-loginHint" style="display:block;font-weight:600;margin-bottom:4px;">
                {translate key="plugins.generic.wpojsSubscriptionApi.settings.loginHint"}
            </label>
            <textarea id="wpojs-loginHint" name="loginHint" rows="3" style="width:100%;font-family:monospace;font-size:13px;">{$loginHint|escape}</textarea>
            <small style="color:#666;">Placeholder: <code>{literal}{lostPasswordUrl}{/literal}</code> &mdash; link to the OJS password reset page.</small>
        </div>

        <div style="margin-bottom:16px;">
            <label for="wpojs-paywallHint" style="display:block;font-weight:600;margin-bottom:4px;">
                {translate key="plugins.generic.wpojsSubscriptionApi.settings.paywallHint"}
            </label>
            <textarea id="wpojs-paywallHint" name="paywallHint" rows="3" style="width:100%;font-family:monospace;font-size:13px;">{$paywallHint|escape}</textarea>
            <small style="color:#666;">Placeholder: <code>{literal}{supportEmail}{/literal}</code> &mdash; value from <code>config.inc.php [wpojs] support_email</code>.</small>
        </div>

        <div style="margin-bottom:16px;">
            <label for="wpojs-footerMessage" style="display:block;font-weight:600;margin-bottom:4px;">
                {translate key="plugins.generic.wpojsSubscriptionApi.settings.footerMessage"}
            </label>
            <textarea id="wpojs-footerMessage" name="footerMessage" rows="3" style="width:100%;font-family:monospace;font-size:13px;">{$footerMessage|escape}</textarea>
            <small style="color:#666;">Placeholder: <code>{literal}{wpUrl}{/literal}</code> &mdash; value from <code>config.inc.php [wpojs] wp_member_url</code>.</small>
        </div>

        <div style="display:flex;gap:8px;align-items:center;">
            <button type="submit" class="pkpButton" style="font-weight:600;">
                {translate key="plugins.generic.wpojsSubscriptionApi.settings.save"}
            </button>
            <button type="button" id="wpojs-restore-defaults" class="pkpButton" style="color:#666;">
                {translate key="plugins.generic.wpojsSubscriptionApi.settings.restoreDefaults"}
            </button>
            <span id="wpojs-settings-saved" style="color:#46b450;font-weight:600;display:none;">
                {translate key="plugins.generic.wpojsSubscriptionApi.settings.saved"}
            </span>
        </div>
    </form>
</div>

<script>
(function() {
    var defaults = {
        loginHint: {$defaultLoginHint|json_encode},
        paywallHint: {$defaultPaywallHint|json_encode},
        footerMessage: {$defaultFooterMessage|json_encode}
    };

    document.getElementById('wpojs-restore-defaults').addEventListener('click', function() {
        document.getElementById('wpojs-loginHint').value = defaults.loginHint;
        document.getElementById('wpojs-paywallHint').value = defaults.paywallHint;
        document.getElementById('wpojs-footerMessage').value = defaults.footerMessage;
    });

    document.getElementById('wpojs-settings-form').addEventListener('submit', function(e) {
        e.preventDefault();
        var form = this;
        var data = new FormData(form);
        data.append('verb', 'settings');

        fetch(form.closest('[data-url]')?.dataset.url || window.location.href, {
            method: 'POST',
            body: data,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function(resp) {
            if (resp.ok) {
                var saved = document.getElementById('wpojs-settings-saved');
                saved.style.display = 'inline';
                setTimeout(function() { saved.style.display = 'none'; }, 3000);
            }
        });
    });
})();
</script>
