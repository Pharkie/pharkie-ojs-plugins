-- Registers and enables the Archive Mode plugin for the whole install (a site
-- plugin, so it also covers /index/login and /index/user/register). Safe to
-- run again. The plugin stays inert until OJS_ARCHIVE_MOVED_TO is set
-- (README.md). The setting goes in first, so no request can load the plugin
-- and cache it as having none. Flush OJS's cache afterwards (README.md).
INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  SELECT 'archivemodeplugin', NULL, 'enabled', '1', 'bool' FROM DUAL
  WHERE NOT EXISTS (SELECT 1 FROM plugin_settings
                    WHERE plugin_name = 'archivemodeplugin' AND context_id IS NULL AND setting_name = 'enabled');
UPDATE plugin_settings SET setting_value = '1'
  WHERE plugin_name = 'archivemodeplugin' AND context_id IS NULL AND setting_name = 'enabled';
INSERT INTO versions (major, minor, revision, build, date_installed, current, product_type, product, product_class_name, lazy_load, sitewide)
  SELECT 1, 0, 0, 0, NOW(), 1, 'plugins.generic', 'archiveMode', 'ArchiveModePlugin', 1, 1 FROM DUAL
  WHERE NOT EXISTS (SELECT 1 FROM versions WHERE product_type = 'plugins.generic' AND product = 'archiveMode');
UPDATE versions SET sitewide = 1
  WHERE product_type = 'plugins.generic' AND product = 'archiveMode' AND current = 1;
