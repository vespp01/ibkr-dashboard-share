# Public Sharing Readiness

This repository was created as a clean, history-free public snapshot.

## Included Safety Controls

- Local Docker Compose startup is documented in `README.md`.
- `.env.example` contains placeholders only.
- IBKR Flex Web Service setup is documented in `docs/setup/flex-queries.md`.
- The frontend has a Settings page with Spanish, English, and German language
  selection.
- Settings includes a local Privacy mode for screenshots and feedback sessions
  with real data.
- Options includes a local Strategy Lab MVP for manual payoff calculations; it
  does not fetch option chains or place orders.
- Options includes HHI concentration for the open option book. The formula is
  documented in `docs/methodology.md` and uses absolute EOD market value, not
  assignment notional or IBKR margin.
- `scripts/check_publication_clean.sh` verifies a committed clean clone with
  fake credentials, isolated Docker container names, isolated ports, and an
  isolated database volume.
- Documentation and defaults use localhost examples.
- `Extractos/`, `.env`, SQLite databases, and frontend build output are ignored.
- Internal handoffs, audit reports, agent metadata, private product references,
  and the original repository history are not included.
- Tracked examples and test fixtures use synthetic or generic sample data; they
  do not contain a real portfolio, statement, account ID, or credential.

## Never Commit Or Share

- Real `.env` files.
- SQLite databases or Docker volumes.
- IBKR statement exports, Flex XML, CSVs, PDFs, or screenshots with account
  numbers.
- Local/private captures or reverse-engineered third-party bundles. Use them
  only as private design/reference material.

## Before Publishing A Change

- Run `scripts/check_publication_clean.sh` from a clean committed tree.
- Run the secret and private-identifier scans documented in the repository.
- Share credentials through a password manager, never through Git or chat.
