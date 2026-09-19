# Private vault

Each login user can create a gocryptfs vault. Run these commands as that user:

```bash
boxman vault init
boxman vault unlock
boxman vault status
boxman claude
boxman vault lock
```

`init` creates `~/.private.cipher` and `~/private`, checks that they are empty, and asks gocryptfs to initialize the encrypted backing directory. Save the password and recovery key outside the host. `unlock` mounts decrypted files at `~/private`; `lock` unmounts them. A process with an open file in the mount can prevent locking.

`boxman claude` requires the vault to be unlocked and sets `CLAUDE_CONFIG_DIR=~/private/claude` before starting Claude Code. Use it before the first Claude login if you want Claude's config in the vault. It does not migrate an existing `~/.claude` directory. Other tools need their credential locations configured separately.

Back up `~/.private.cipher`, including `gocryptfs.conf`, and exclude the plaintext mount from backups. The vault protects backing files and snapshots while locked. It does not hide plaintext from running processes or a host administrator while unlocked.
