# baby-onit: the agent loop (S7)

The agent loop, the function agent_loop, is the heart of baby-onit, and it is a while True loop with four moves. The messages start as the system prompt, plus the replayed session, plus the user's task. Then, repeatedly:

1. Send the messages to the model (provider.chat).
2. If the reply has tool calls, run them (dispatch, parallel or serial).
3. Append the results as tool messages.
4. If there are no tool calls, the reply is the answer; stop. The loop also stops on the iteration cap.

The baby-onit loop also enforces two limits. The iteration cap is max_chat_iterations, where -1 means unlimited, matching onit. Context compaction happens when the running token count approaches max_context_tokens: older turns are summarised so the conversation fits.

The loop returns the final assistant text. It takes an on_event(kind, text) callback so the UI can render each step live, but the loop itself knows nothing about screens; it just emits events.

Teaching point: the agent loop is deliberately dumb. It has no policy, no intelligence, no "helping." It relays messages, runs tools, and stops. All the intelligence is in the model; all the safety is in the guards around it.
