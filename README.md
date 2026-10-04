# AngelOS Community Registry

`plugins.json` is the public registry consumed by the independent Community
Store plugin. Each entry must point to a HTTPS ZIP archive containing one
AngelOS plugin directory and its `manifest.json`.

Required fields are `id`, `name`, `author`, `version`, `description`,
`source`, and `repository`. The archive manifest `id` and `version` must match
the registry entry exactly. Keep entries small and reviewable; the store
validates the manifest before replacing a local plugin.
