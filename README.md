# Antom MCP Tools for Dify

Antom merchants can use a Dify workflow to query merchant profiles, transaction
details, integration logs, diagnostics and billing report URLs through the
[Antom developer MCP service](https://docs.antom.com/ac/ref/mcp). The plugin
exposes two Dify tools. **Discover Antom queries** lists the supported tools and
input schemas available to the authorized account. **Query Antom** runs one of
those tools with the arguments you provide.

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
  for queries and `dashboard-apiv2.antom.com` for OAuth.

## Install and authorize

1. Build the package with the official Dify Plugin CLI. From the source
   repository root, run
   `dify plugin package . -o ../antom_mcp_tools-0.0.3.difypkg`.
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
5. Add **Discover Antom queries** to a Workflow Tool node and run the workflow.
   The result lists only supported tools that your account actually exposes.
6. Add **Query Antom**, select a returned name, and supply `arguments` as a JSON
   object string matching its `input_schema`. The plugin validates the arguments
   against a fresh server listing before making the call.

Discovery reports `server_tool_count`, `supported_tool_count`, `status`, and
`excluded_tools`. If the service returns zero tools, discovery reports
`no_server_tools`. If it returns tools but all fall outside the plugin's
allowlist or carry an explicit non-read-only marker, discovery reports
`no_supported_tools`. Check `excluded_tools` for names and reasons.

### 0.0.3 tool names

Use a name returned by **Discover Antom queries** and the `input_schema`
returned alongside it. Version 0.0.3 recognizes the following pairs.

| Documented name | Name advertised by the tested service |
| --- | --- |
| `antom_inquire_merchant_profile` | `get_antom_merchant_profile` |
| `antom_query_transaction_detail` | `get_antom_transaction_details` |
| `antom_diagnose_error` | `antom_error_diagnose` |
| `antom_inquire_diagnosis_result` | `get_antom_diagnosis_result` |
| `antom_inquire_integration_logs` | `get_antom_integration_logs` |
| `antom_fetch_bill_download_url` | `get_antom_report_downloadUrl` |

### Query merchant onboarding status

In Dify Cloud, version 0.0.3 discovered 6 supported tools from the 12 returned
by one authorized account. The merchant profile query was exercised end to end.
For the other five tools, this test verified discovery and their input schemas.

1. On the **Query Antom** node, select the credential you used for discovery.
2. If discovery lists `get_antom_merchant_profile` with the corresponding input
   schema, enter it as **Tool name** and enter `{"scopeList":["ONBOARD"]}` as
   **Arguments JSON**.
3. Run the node and open **Last run > Output**. In the verified call,
   `json[0].isError` was `false` and
   `json[0].structuredContent.result.resultCode` was `SUCCESS`. Find the
   merchant's onboarding status at `json[0].structuredContent.onboardStatus`.

## Boundaries

- Only the six query and diagnostic capabilities in the table are eligible. The
  plugin excludes any tool explicitly marked non-read-only. The query uses the
  OAuth credential selected on the workflow node. Creating payments, refunds or
  subscriptions and transferring assets all fall outside its scope.
- Dify stores OAuth credentials. A temporary encrypted state includes PKCE data
  and the exact Dify callback, and expires after ten minutes.
- Requests go to the two fixed HTTPS destinations listed under **Requirements**
  with TLS verification and bounded timeouts. The plugin suppresses raw upstream
  errors and makes a single query attempt.
- Query results may contain merchant, transaction, log or billing data. Only use
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
