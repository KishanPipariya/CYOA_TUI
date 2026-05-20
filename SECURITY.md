# Security Policy

## Supported Versions

Security fixes are handled on the latest released version.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately by opening a GitHub security advisory or by contacting the maintainer through the repository issue tracker and requesting a private disclosure path.

Do not include secrets, private model files, save files, or crash logs in a public issue. The app may store local paths and story content in support logs, so redact user-specific data before sharing diagnostics.

## Model and Download Safety

`cyoa-tui` can run with local GGUF models. Only download model files from sources you trust, and verify checksums for release artifacts when they are provided. The app stores models, saves, config, and logs in per-user application directories rather than the repository checkout.
