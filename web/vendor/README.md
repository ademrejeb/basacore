# Vendored `@adem.rejeb/ui` 0.1.2

`adem.rejeb-ui-0.1.2.tgz` is the `npm pack` output of the UI workspace at
`personal-ui` `c9aefcc` (Stack/Grid no longer clear the gap shorthand on
client-side renders). The local npm token cannot publish to the `@adem.rejeb`
scope (`E404`), so `package.json` installs this tarball with `file:`.

Regenerate after a UI change:

```bash
cd ui
npm run build:ui
npm pack --workspace @adem.rejeb/ui --pack-destination ../basa/web/vendor
```

Then update the `file:` path in `package.json` and run `npm install`.
