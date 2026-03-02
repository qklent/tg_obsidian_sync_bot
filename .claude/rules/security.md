# Security Rules

- Never hardcode secrets, API keys, or tokens in source code
- Always use environment variables or config files for sensitive values
- Validate all user inputs at system boundaries
- Sanitize data before passing to external services (Telegram, LLM, Git)
- Never log secrets — redact tokens from log output
- Follow OWASP top 10 guidelines
- Review for command injection, especially in git operations and shell commands
