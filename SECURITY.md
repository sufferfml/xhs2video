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

Email privacy is a contributor choice: GitHub noreply addresses are recommended,
not required. An intentionally public work or open-source email is not itself a
security incident. The history checker warns about non-noreply metadata without
printing the address and does not fail solely for it. Review the warning for
unintended disclosure. This exception applies only to email metadata; sensitive
content checks and Gitleaks remain blocking. Do not disable either check or
ignore its failure to accommodate an email warning.

If a credential was ever exposed, revoke/rotate it first. Removing a file or rewriting
Git history alone does not revoke a credential or erase copies. The initial public
snapshot started a new history; the previous development repository must remain
private. Routine contributions and releases preserve the established public history.
