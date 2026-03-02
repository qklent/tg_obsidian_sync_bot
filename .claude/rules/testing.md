# Testing Rules

- Follow TDD when practical: write test expectations before implementation
- Use AAA pattern (Arrange, Act, Assert)
- Target 80% coverage for new code
- Test isolation: no shared mutable state between tests
- Mock external services (Telegram API, LLM, Git remote)
- Test both success and error paths
- Integration tests for the full message-to-note pipeline
