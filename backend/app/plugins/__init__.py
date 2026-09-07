"""chiatienan's own pipeline plugins (plan Task 1.6).

Each is a move of a block of ``chat.run_bot_turn``, with its comments. They are
host-specific by nature — the persona prompt, the lunch renderers, the money
validators, the draft cards — and Phase 3 relocates the lunch ones into the
``lunch_ledger`` pack. ``app.run.legacy`` is the seam into the frozen
``agent.run_turn``.
"""
