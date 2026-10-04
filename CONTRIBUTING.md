# Adding a plugin

1. Publish a ZIP release containing the plugin directory and `manifest.json`.
2. Keep the manifest `id` simple (`a-z`, digits, `_`, `-`) and equal to the
   registry `id`; keep its `version` equal to the registry `version`.
3. Add one entry to `plugins.json` with an HTTPS `source`, repository, author,
   description, and useful tags.
4. Open a pull request and include the release URL and a short test note.

The Community Store validates the archive before replacing any installed
plugin. Registry inclusion is still a trust decision, so review source code,
permissions, and release provenance before merging.
