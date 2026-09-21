# Secret and Path Scan

The final public source, tests, examples, configuration sample, documents, and
sanitized integration trace fixture were scanned while excluding generated
dependencies, build output, ordinary runtime output, caches, and Git metadata.

Results:

- Real API keys, access-key identifiers, tokens, passwords, and credentials: 0.
- User names and machine-specific home paths: 0.
- Local workspace or interpreter absolute paths: 0.
- Non-public repository/module references in production source: 0.
- Dynamic path injection or subprocess execution bridge: 0.

Configuration names and empty/example values remain intentionally documented;
they are not credentials. Runtime-claims tests intentionally use unmistakably
fake credential strings to prove trace redaction; those values never appear in
the persisted test trace. The required open-source status flag names are also
documentation, not runtime paths.
