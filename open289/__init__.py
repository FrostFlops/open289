"""open289: Fluke 287/289 multimeter access over a Fluke IR3000 FC Bluetooth LE adapter.

Modules:
  cli        - the `open289` command line (JSON output)
  mcp_server - MCP server (`open289-mcp`) for AI agents
  transport  - BLE backend selection (real adapter or the virtual one), discovery, connection
  adapter    - IR3000 FC adapter service client (pass-through to the meter's serial protocol)
  meter      - Fluke 287/289 remote protocol: queries and binary record decoders
  reading    - decoder for the adapter's live reading notifications
  logger     - long-running logging with reconnect
  sim        - virtual IR3000 FC + Fluke 289 (tests, CI, trying the tool without hardware)
Run the CLI with `python -m open289 --help`.
"""
__version__ = "0.1.1"
