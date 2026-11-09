# Antom MCP Tools for Dify

Antom merchants can use two Dify actions to work with the
[Antom developer MCP service](https://docs.antom.com/ac/ref/mcp).
**Discover Antom tools** lists the tools the service advertises for the selected
merchant credential, together with their names and input schemas.
**Call an Antom tool** runs a selected tool with that credential. Depending on
the account, a tool can read data or change it.

The source is available from the
[plugin repository](https://github.com/mo-cha-lauren/antom-dify-mcp-tools).

For plugin support, email [the maintainer](mailto:luoxiaofan.lxf@ant-intl.com)
or open a [GitHub issue](https://github.com/mo-cha-lauren/antom-dify-mcp-tools/issues).
Include the plugin version and a redacted error message.

## Get access to Antom MCP

**Antom developer MCP access requires an allowlist application.** Sign up as a
merchant first, then apply separately for MCP access.

1. **New to Antom?** [Start merchant onboarding](https://s.antom.com/ruzhu).
   If you already have an Antom merchant account, go to step 2.
2. **Apply for the MCP allowlist.** Email
   [Antom integration support](mailto:pub_antom_integration_service@antom.com)
   to request MCP access for your merchant account. Wait for Antom's approval.
3. **After approval**, have your merchant administrator enable MCP Access in
   Antom Dashboard under **Settings > Team** for the Test or Live mode you plan
   to use. Then authorize the plugin in Dify.

See the [Antom MCP access guide](https://docs.antom.com/ac/ref/mcp) for details.

## Dify requirements

- Dify 1.14.2 or later with permission to install local plugin packages.
- The Dify plugin runtime must be able to connect over HTTPS to `mcp.antom.com`
  for tool calls and `dashboard-apiv2.antom.com` for OAuth.

## Install and authorize

1. Build the package with the official Dify Plugin CLI. From the source
   repository root, run
   `dify plugin package . -o ../antom_mcp_tools-0.0.4.difypkg`.
2. In Dify, open **Integrations > Install > Local package** and choose the package.
   Use a workspace that permits local packages. After installation, open
   **Integrations > Tools > Tool Plugin**, search for **Antom**, and open
   **Antom MCP Tools** (package name `antom_mcp_tools`).
3. Open the provider's **OAuth Client Settings**. Generate an OAuth state key
   locally using `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`
   after installing the dependencies. Paste it into **OAuth state encryption key**.
   Keep it stable and private. It encrypts temporary OAuth state for this
   workspace. Rotating it invalidates pending authorization flows.
4. Click **Authorize** and sign in to Antom yourself. Review and approve the
   permissions. Each authorization dynamically registers a public OAuth client.
   PKCE protects the code exchange.
5. Add **Discover Antom tools** to a Workflow Tool node and run it. In its
   `tools` output, find the tool name and `input_schema` for your operation.
6. Add **Call an Antom tool**, select the same credential, and enter that name
   and an `arguments` JSON object string matching the schema. Before each call,
   the plugin checks the current server listing and validates the arguments.

### Changes in 0.0.4

Discovery now returns the full catalog advertised by Antom for the selected
credential, including newly introduced tool names and tools marked
`readOnlyHint: false`. The plugin applies no local filter based on tool names
or read-only annotations. Antom decides which tools the merchant may execute.
If the server returns zero tools, discovery reports `no_server_tools`.

The two Dify action identifiers remain `discover_antom_queries` and
`query_antom` so existing workflows can still refer to them. When upgrading
from 0.0.3, review any agents or workflows that select tools automatically.
Their available operations can now include writes. For example, one account's
catalog included `create_antom_subscription_price` and
`create_antom_subscription_product`. Check the selected tool and its inputs
before running a write, and leave automatic retries off. If the call times out,
check the result in Antom before retrying to avoid repeating a completed change.

### Query merchant onboarding status

In an earlier Dify Cloud test, Antom returned 12 tools for one merchant to
version 0.0.3. That version exposed six of them, and the merchant profile query
completed successfully. This is the historical end-to-end example.

1. On the **Call an Antom tool** node, select the credential you used for discovery.
2. If discovery lists `get_antom_merchant_profile` with the corresponding input
   schema, enter it as **Tool name** and enter `{"scopeList":["ONBOARD"]}` as
   **Arguments JSON**.
3. Run the node and open **Last run > Output**. In the verified call,
   `json[0].isError` was `false` and
   `json[0].structuredContent.result.resultCode` was `SUCCESS`. Find the
   merchant's onboarding status at `json[0].structuredContent.onboardStatus`.

## Boundaries

- Dify stores OAuth credentials. A temporary encrypted state includes PKCE data
  and the exact Dify callback, and expires after ten minutes.
- Requests go to the two fixed HTTPS destinations listed under **Dify requirements**
  with TLS verification and bounded timeouts. The plugin suppresses raw upstream
  errors and makes one tool call per invocation.
- Tool results may contain merchant, transaction, log or billing data. Only use
  workflows and models that your organization permits to process this data.
- One workspace test encountered a credential refresh failure. Reauthorizing in
  Dify and selecting the new credential on the workflow nodes restored access.
  Automatic renewal after token expiry still needs separate verification.
  To revoke access, use **Antom Dashboard > Settings > Session management >
  OAuth Sessions**.

For details about credentials, query results and retention, see the
[privacy notice](PRIVACY.md).

## Development and validation

Use Python 3.12. Install `requirements.txt` in an isolated environment, then
install `pytest` and run `python -m pytest -q tests` from the source repository
root. Tests use simulated HTTP responses. See Dify's
[Marketplace package validation instructions](https://docs.dify.ai/en/develop-plugin/publishing/marketplace-listing/submit-plugin-to-marketplace)
to check a package before submitting it.

Update by installing a rebuilt versioned package after reviewing its source and
release notes. The plugin refreshes tool schemas before each call. Changes at
the remote service may require updating workflow inputs.
