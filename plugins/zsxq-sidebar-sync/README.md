# Knowledge Planet Sidebar Sync

This plugin packages the MP3/PDF sidebar skills and the optional MCP server used by the Knowledge Planet synchronization workflow.

The current production skills hand off through the loopback-only HTML bridge already served by the local frontend:

- `/zsxq-sidebar-history.html?kind=mp3|pdf`: read complete three-day download history.
- `/zsxq-sidebar-submit.html?kind=mp3|pdf`: submit one frozen inline manifest to the existing import, clean, and publish workflow.
- `/zsxq-sidebar-bridge.html`: verify bridge health.

This route requires no Downloads folder authorization. Before the first submit, every newly downloaded file must include its browser-reported absolute path; a definite validation error ends the run, while only an unknown result may replay the identical manifest and `run_id`.

The plugin also retains three narrowly scoped MCP tools for environments where an OpenAI Secure MCP Tunnel is available:

- `zsxq_health`: verify the local import service.
- `zsxq_get_history`: read the server-owned three-day MP3 or PDF history.
- `zsxq_submit_manifest`: persist one scan manifest and queue the existing import, clean, and publish workflow idempotently.

The sidebar never receives general shell, filesystem, or arbitrary HTTP access. Both bridge routes accept only the fixed Knowledge Planet group and MP3/PDF tabs; the MCP server can call only the local loopback import API.

The MCP launcher uses the project virtual environment by default. In a Git worktree it can reuse the main checkout's environment; an explicit interpreter can be supplied with `ZSXQ_MCP_PYTHON=/absolute/path/to/python` when required.

## Optional one-time tunnel setup

1. In [OpenAI Platform Tunnels](https://platform.openai.com/settings/organization/tunnels), create a tunnel for the intended organization/workspace and copy its non-secret `tunnel_...` ID.
2. In [Runtime API keys](https://platform.openai.com/settings/organization/api-keys), create a **Restricted** key with only **Tunnels Read + Use**. Do not use an admin key or an unrestricted key for the daemon.
3. From the project root, run:

   ```bash
   plugins/zsxq-sidebar-sync/scripts/connect-secure-tunnel.sh \
     --tunnel-id tunnel_0123456789abcdef0123456789abcdef
   ```

   Paste the Runtime API key only into the hidden terminal prompt. The script stores it outside the repository at:

   ```text
   ~/Library/Application Support/tunnel-client/secrets/zsxq-sidebar-runtime-api-key
   ```

   The file mode is `0600`; generated tunnel configuration contains only a `file:` reference. Re-running the script reuses the same key and managed runtime alias.

4. Confirm the final status JSON reports `process_running`, `healthy`, and `ready` as true.
5. While the runtime is ready, open [ChatGPT connector settings](https://chatgpt.com/#settings/Connectors), enable Developer mode if needed, and create a **Tunnel** connection using the same tunnel ID.
6. Start a new ChatGPT conversation, enable that connection from the tools menu, and first call `zsxq_health`.

The current HTML bridge skills do not require this tunnel. Creating the Platform tunnel or Runtime API key grants persistent access and must be completed or explicitly confirmed by the account owner. Never paste either API key into chat.

## Test order

1. User manually tests the MP3 skill on the exact `调研纪要` URL.
2. User manually tests the PDF skill on the exact `外资研报` URL.
3. Codex tests opening the correct Chrome profile/page and invoking each skill.
4. Only after all three stages pass may the paused four-hour automation be resumed.

The skills use a 48-hour window, at most 40 topic cards and 40 new files, three-day server history, MP3 download count strictly greater than 10, and PDF download count strictly greater than 30. A valid zero-file scan still submits an empty manifest so the existing synchronization orchestration can send its normal completion notification.

## References

- [OpenAI Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
- [Build an MCP server](https://developers.openai.com/plugins/build/mcp-server)
- [Connect and test a plugin in ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)
