## Summary

<!-- What does this PR do and why? Link the issue it addresses, if any. -->

## Changes

<!-- Bullet list of the notable changes. -->

## Testing

<!-- Commands you ran and their results. `python scripts/verify.py` is the
     full local gate; paste a summary (pass/fail + coverage %) rather than
     the entire log. -->

- [ ] `python scripts/verify.py` passes locally
- [ ] `python scripts/sync-plugin-tree.py --check` passes (if `src/cursor_goal/**` changed)
- [ ] New/updated tests cover the change
- [ ] Docs updated (`README.md`, `docs/*.md`, `.cursor/skills/goal/SKILL.md`) if behavior changed
- [ ] `CHANGELOG.md` updated under `[Unreleased]`

## Security considerations

<!-- Does this touch the trust model, ACL/permission checks, secret
     redaction, fail-open/fail-closed behavior, or subprocess execution?
     If yes, explain the implications and confirm SECURITY.md still
     accurately describes the behavior. If no, write "None". -->

## Checklist

- [ ] I have not hand-edited `plugins/cursor-goal/**` or `.cursor-plugin/marketplace.json` (regenerated via `sync-plugin-tree.py` instead)
- [ ] I have not committed build artifacts (`dist/`, `dist-test/`, `.coverage`, `coverage.xml`, `__pycache__/`)
- [ ] I agree my contribution is licensed under AGPL-3.0-only (see [COPYING](../COPYING))
