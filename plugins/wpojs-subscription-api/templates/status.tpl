<div id="wpojs-status-page" style="max-width:800px;padding:16px;">
    <h3>{translate key="plugins.generic.wpojsSubscriptionApi.displayName"} &mdash; Status</h3>

    {* ---- Config Health Check ---- *}
    <h4 style="margin-top:24px;">Configuration</h4>
    {if $allGreen}
        <p style="color:#46b450;font-weight:600;">Plugin configured correctly.</p>
    {/if}
    <table class="pkpTable" style="width:100%;">
        <thead>
            <tr>
                <th>Check</th>
                <th>Status</th>
                <th>Detail</th>
            </tr>
        </thead>
        <tbody>
            {foreach from=$configChecks item=check}
                <tr>
                    <td>{$check.name|escape}</td>
                    <td>
                        {if $check.ok}
                            <span style="color:#46b450;">&#10003; Yes</span>
                        {else}
                            <span style="color:#dc3232;">&#10007; No</span>
                        {/if}
                    </td>
                    <td>{if isset($check.detail)}{$check.detail|escape}{/if}</td>
                </tr>
            {/foreach}
        </tbody>
    </table>

    {* ---- Sync Stats ---- *}
    <h4 style="margin-top:24px;">Sync Stats</h4>
    <table class="pkpTable" style="width:100%;">
        <tbody>
            <tr>
                <td>Active individual subscriptions</td>
                <td><strong>{$activeSubCount}</strong></td>
            </tr>
            <tr>
                <td>Users created by sync</td>
                <td><strong>{$syncCreatedCount}</strong></td>
            </tr>
        </tbody>
    </table>

    {if $subTypeCounts|@count > 0}
        <h5 style="margin-top:12px;">Subscription types in use</h5>
        <table class="pkpTable" style="width:100%;">
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Count</th>
                </tr>
            </thead>
            <tbody>
                {foreach from=$subTypeCounts item=row}
                    <tr>
                        <td>{$row->type_name|escape}</td>
                        <td>{$row->count}</td>
                    </tr>
                {/foreach}
            </tbody>
        </table>
    {/if}

    {* ---- Recent API Activity ---- *}
    <h4 style="margin-top:24px;">Recent API Activity</h4>
    {if $recentLogs|@count > 0}
        <table class="pkpTable" style="width:100%;">
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Method</th>
                    <th>Endpoint</th>
                    <th>Source IP</th>
                    <th>HTTP Status</th>
                </tr>
            </thead>
            <tbody>
                {foreach from=$recentLogs item=log}
                    <tr>
                        <td style="white-space:nowrap;">{$log->created_at|escape}</td>
                        <td>{$log->method|escape}</td>
                        <td>{$log->endpoint|escape}</td>
                        <td>{$log->source_ip|escape}</td>
                        <td>
                            {if $log->http_status >= 200 && $log->http_status < 300}
                                <span style="color:#46b450;">{$log->http_status}</span>
                            {elseif $log->http_status >= 400}
                                <span style="color:#dc3232;">{$log->http_status}</span>
                            {else}
                                {$log->http_status}
                            {/if}
                        </td>
                    </tr>
                {/foreach}
            </tbody>
        </table>
        <p style="color:#666;font-size:12px;margin-top:8px;">Showing last 50 entries. Entries older than 30 days are automatically deleted.</p>
    {else}
        <p>No API activity logged yet.</p>
    {/if}
</div>
