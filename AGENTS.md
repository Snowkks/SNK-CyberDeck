# Snowk CyberDeck — Development Rules

CyberDeck is a toolbox containing small Linux, networking, and security tools.

The first tool is Network Diagnostics.

## Working style

The user is learning while building this project.

For every new feature:

1. Explain what the feature will do in plain language.
2. Explain the important Linux/networking concepts and commands involved.
3. State which files you intend to create or modify.
4. STOP and wait for user approval.
5. After approval, implement ONLY that feature.
6. Do not automatically continue to another feature.
7. When implementation finishes, STOP and explain:
   - what changed
   - how it works
   - what commands/concepts were used
   - how the user can run and test it manually
8. Wait until the user has personally tested the feature before continuing.

## Agents

Act as the lead developer.

- Use `coder` for meaningful implementation when useful.
- Do not use subagents for explanations, planning, or trivial changes.
- Do not automatically run tester or reviewer.
- Use `tester` only when the user asks to test or approves testing.
- Use `reviewer` only for security-sensitive or substantial changes.
- Never start autonomous coder → tester → reviewer → fix loops.
- Never start the next roadmap item automatically.
- Keep delegated tasks narrow.
- Do not give subagents unnecessary conversation history.

## Scope

Keep the implementation simple.

- Build user-visible functionality before abstractions.
- Do not introduce frameworks or abstraction layers for hypothetical future needs.
- Do not implement future tools until requested.
- Reuse code when duplication actually exists.
- Prefer understandable code over clever architecture.

## Safety

- Do not commit or push unless explicitly requested.
- Do not install packages without approval.
- Do not use sudo without approval.
- Keep temporary files inside the project.
- Ask before making product, architecture, security, or scope decisions.
