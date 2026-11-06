"""DCR + PKCE OAuth. Dify binds the callback to its user/workspace context."""
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dify_plugin import ToolProvider
from dify_plugin.entities.oauth import ToolOAuthCredentials
from dify_plugin.errors.tool import ToolProviderCredentialValidationError, ToolProviderOAuthError

from antom_client import AntomError, ISSUER, SERVER_URL, execute, require_origin


def _cipher(credentials):
    try:
        return Fernet(str(credentials["oauth_state_key"]).encode("ascii"))
    except (KeyError, ValueError, UnicodeError):
        raise ToolProviderOAuthError("Configure a valid Fernet OAuth state key in OAuth Client Settings.") from None


def _json_request(method: str, url: str, **kwargs):
    try:
        require_origin(url, ISSUER if urlsplit(url).hostname == urlsplit(ISSUER).hostname else SERVER_URL)
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            if len(response.content) > 65536:
                raise ValueError("oversized response")
            value = response.json()
            if not isinstance(value, dict) or "error" in value:
                raise ValueError("invalid OAuth response")
            return value
    except (httpx.HTTPError, ValueError, AntomError):
        raise ToolProviderOAuthError("Antom OAuth request failed. Check access permissions or retry authorization.") from None


def _metadata():
    resource = _json_request("GET", SERVER_URL + "/.well-known/oauth-protected-resource")
    if resource.get("resource") != SERVER_URL or ISSUER not in resource.get("authorization_servers", []):
        raise ToolProviderOAuthError("Antom resource metadata does not match the expected issuer.")
    metadata = _json_request("GET", ISSUER + "/.well-known/oauth-authorization-server")
    if metadata.get("issuer") != ISSUER or "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise ToolProviderOAuthError("Antom authorization metadata does not support the required PKCE flow.")
    if "none" not in metadata.get("token_endpoint_auth_methods_supported", []):
        raise ToolProviderOAuthError("Antom does not support public OAuth clients.")
    try:
        for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
            require_origin(metadata[field], ISSUER)
    except (KeyError, AntomError):
        raise ToolProviderOAuthError("Antom returned unsupported OAuth endpoints.") from None
    return metadata


def _credentials(data, client_id, previous=None):
    token = data.get("access_token")
    if not isinstance(token, str) or not token or any(c.isspace() for c in token):
        raise ToolProviderOAuthError("Antom returned an invalid access token.")
    if str(data.get("token_type", "Bearer")).lower() != "bearer":
        raise ToolProviderOAuthError("Antom returned an unsupported token type.")
    try:
        lifetime = int(data["expires_in"])
        if lifetime <= 0:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ToolProviderOAuthError("Antom did not return a valid token lifetime.") from None
    credentials = {"access_token": token, "client_id": client_id}
    refresh = data.get("refresh_token") or (previous or {}).get("refresh_token")
    if refresh:
        credentials["refresh_token"] = refresh
    return ToolOAuthCredentials(credentials=credentials, expires_at=int(time.time()) + lifetime)


class AntomProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict) -> None:
        try:
            execute(credentials.get("access_token", ""))
        except AntomError:
            raise ToolProviderCredentialValidationError("Antom authorization is invalid or MCP access is unavailable.") from None

    def _oauth_get_authorization_url(self, redirect_uri: str, system_credentials) -> str:
        cipher = _cipher(system_credentials)
        parsed = urlsplit(redirect_uri)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ToolProviderOAuthError("Use Dify's HTTPS OAuth callback URL.")
        metadata = _metadata()
        registration = _json_request("POST", metadata["registration_endpoint"], json={
            "client_name": "Antom MCP Tools for Dify", "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"], "token_endpoint_auth_method": "none",
        })
        client_id = registration.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise ToolProviderOAuthError("Antom did not register an OAuth client.")
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = cipher.encrypt(json.dumps({
            "redirect_uri": redirect_uri, "client_id": client_id,
            "code_verifier": verifier, "nonce": secrets.token_urlsafe(24),
        }).encode()).decode()
        return metadata["authorization_endpoint"] + "?" + urlencode({
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
            "scope": "mcp:tools", "resource": SERVER_URL, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
        })

    def _oauth_get_credentials(self, redirect_uri: str, system_credentials, request) -> ToolOAuthCredentials:
        if request.args.get("error"):
            raise ToolProviderOAuthError("Antom authorization was not granted.")
        state = request.args.get("state", "")
        code = request.args.get("code", "")
        if not state or len(state) > 8192 or not code or len(code) > 4096:
            raise ToolProviderOAuthError("Antom callback is missing authorization data.")
        try:
            pending = json.loads(_cipher(system_credentials).decrypt(state.encode(), ttl=600))
            if not secrets.compare_digest(pending["redirect_uri"], redirect_uri):
                raise ValueError()
            client_id = pending["client_id"]
            verifier = pending["code_verifier"]
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
            raise ToolProviderOAuthError("Authorization expired or callback context does not match. Start again.") from None
        metadata = _metadata()
        data = _json_request("POST", metadata["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": client_id, "code_verifier": verifier, "resource": SERVER_URL,
        })
        return _credentials(data, client_id)

    def _oauth_refresh_credentials(self, redirect_uri: str, system_credentials, credentials) -> ToolOAuthCredentials:
        if not credentials.get("refresh_token") or not credentials.get("client_id"):
            raise ToolProviderOAuthError("Antom authorization cannot be refreshed. Authorize again.")
        metadata = _metadata()
        data = _json_request("POST", metadata["token_endpoint"], data={
            "grant_type": "refresh_token", "refresh_token": credentials["refresh_token"],
            "client_id": credentials["client_id"], "resource": SERVER_URL,
        })
        return _credentials(data, credentials["client_id"], credentials)
