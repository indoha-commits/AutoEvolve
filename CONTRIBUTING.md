# Contributing

Company Core is an early open-source project. Small changes with tests and a clear lifecycle impact
are preferred over broad feature additions.

## Development

```bash
make setup
make check
make dev
```

Use mock mode for tests and examples. Tests must not require network access or paid provider credentials.

## Pull requests

- Open an issue before significant API, schema, provider, or lifecycle changes.
- Keep one concern per pull request.
- Add tests for behavior and failure cases.
- Update documentation and `CHANGELOG.md` when users are affected.
- Do not include customer data, provider payloads, secrets, proprietary prompts, or unlicensed assets.
- Preserve human approval and delivery-idempotency invariants.

By contributing, you agree that your contribution is licensed under the repository's MIT License
and that you have the right to submit it.

## Provider adapters

A provider adapter must:

- Implement an existing protocol without leaking provider-specific data into domain models.
- Declare required environment variables and link to provider terms.
- Use bounded timeouts and return actionable errors.
- Supply anonymized or synthetic contract fixtures.
- Avoid live API calls in the default test suite.
- Document which actions consume credits or send external messages.

## Commit style

Use imperative, scoped messages such as `sales: enforce approval hash before send`. Maintainers may squash pull requests when merging.
