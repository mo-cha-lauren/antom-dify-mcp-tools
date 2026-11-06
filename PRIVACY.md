# Antom MCP Tools privacy notice

The plugin sends the OAuth registration, authorization-code exchange and refresh
requests to `dashboard-apiv2.antom.com`. A registration includes the Dify callback
URL and the plugin's client name. Antom handles sign-in and user consent.

Dify stores OAuth credentials under its own credential-management policy. The
plugin uses the authorized access token only for requests to `mcp.antom.com`.
The OAuth state key remains in Dify's OAuth client configuration. The temporary state
is authenticated and encrypted and contains the callback URL, registered client
identifier, PKCE verifier and a random nonce.

Queries transmit their arguments to Antom. Results may include merchant details,
transaction data, integration logs, diagnosis results or billing download URLs.
Results are returned to the Dify workflow/agent that invoked the tool. Dify may
retain workflow history and send results to configured model providers according
to the user's application configuration. Operators must choose permitted models,
control workspace access and set appropriate retention policies.

The plugin has no independent data store, telemetry, token files, result cache
or application logs. It suppresses raw upstream error bodies. A billing query
returns a download URL to the calling workflow. Dify and Antom may maintain
their own operational and audit records. Read the
[Antom Privacy Policy for Merchant Services](https://docs.antom.com/ac/Platform/privacy)
and the [Dify Cloud privacy policy](https://www.dify.ai/legal/privacy-policy)
for details about how each service handles personal data.

Revoke an authorization through Antom Dashboard OAuth Sessions and remove the
corresponding Dify credential using the normal account controls when appropriate.
For merchant access questions, see the [Antom MCP guide](https://docs.antom.com/ac/ref/mcp).
When reporting plugin issues, keep credentials, merchant identifiers and query
results out of public issue reports.
