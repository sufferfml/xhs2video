# Security policy

0.2.0a1 is an experimental local, single-user tool. Only local stdio is supported.
Do not expose it as an unauthenticated HTTP service or a shared bot. Local clients
can intentionally select files; the application is not a sandbox for hostile users.

For a vulnerability, use the repository's Security > Report a vulnerability flow
when available. Otherwise open an issue requesting a private contact, without
including exploit details, personal data, credentials or signed URLs. Do not post
sensitive reproduction files publicly. There is no guaranteed response SLA.

Never commit .env files, browser profiles, cookies, conversation identifiers,
private absolute paths, or real downloaded media. Public source snapshots must pass
`scripts/check_release.py` and Gitleaks before publication. Check archive contents,
commit metadata, all published refs, Actions logs and release attachments as well.

If a credential was ever exposed, revoke/rotate it first. Removing a file or rewriting
Git history alone does not revoke a credential or erase copies. This initial release
uses a new history; the previous development repository must remain private.
