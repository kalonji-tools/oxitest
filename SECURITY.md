# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| latest  | ✓         |
| < latest| ✗         |

Only the latest release is supported. Please upgrade before reporting.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

**Preferred:** Use [GitHub private security advisories](https://github.com/kalonji-tools/oxitest/security/advisories/new) to report a vulnerability confidentially.

We aim to acknowledge reports within **72 hours** and provide an initial assessment within **7 days**. If a fix is warranted, we will coordinate a disclosure timeline with you before releasing a patch.

## Plugin Trust Model

Plugins declared in `plugins = [...]` are **arbitrary Python code** that runs inside
worker processes with the same privileges as your test code. There is no sandboxing
or isolation between plugin code and test code — a plugin can read files, make network
calls, or modify the environment just like any test.

Before adding a plugin to your configuration:

- **Vet the source.** Review the plugin code or only install plugins from sources you trust.
- **Pin versions.** Use locked dependencies to prevent silent upgrades.
- **Treat plugins like dependencies.** The same supply-chain caution that applies to
  PyPI packages applies to oxitest plugins.
