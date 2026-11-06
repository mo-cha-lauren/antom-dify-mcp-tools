"""Contract tests against the actual SDKs, without merchant credentials or network."""
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.errors.tool import ToolProviderOAuthError
import httpx
import httpx2
import pytest
from cryptography.fernet import Fernet
from mcp_types import Tool, ToolAnnotations
from werkzeug import Request

import antom_client as client
from tool_catalog import exclusion_reason
import provider.antom as oauth

REDIRECT = 'https://cloud.dify.ai/console/api/oauth/plugin/antom/antom_mcp_tools/callback?proxy_context=test-context'
METADATA = {
    'issuer': client.ISSUER,
    'authorization_endpoint': client.ISSUER + '/mcp/oauth2/authorize',
    'token_endpoint': client.ISSUER + '/mcp/oauth2/token',
    'registration_endpoint': client.ISSUER + '/mcp/oauth2/register',
    'code_challenge_methods_supported': ['S256'],
    'token_endpoint_auth_methods_supported': ['none'],
}

# Names from the merchant's discovery response. The exercises below use only
# synthetic schemas and data; the merchant response did not include schemas.
QUERY_SPELLINGS = [
    ('antom_diagnose_error', 'antom_error_diagnose'),
    ('antom_inquire_diagnosis_result', 'get_antom_diagnosis_result'),
    ('antom_inquire_integration_logs', 'get_antom_integration_logs'),
    ('antom_inquire_merchant_profile', 'get_antom_merchant_profile'),
    ('antom_fetch_bill_download_url', 'get_antom_report_downloadUrl'),
    ('antom_query_transaction_detail', 'get_antom_transaction_details'),
]
OTHER_REPORTED_NAMES = [
    'create_antom_subscription_price', 'create_antom_subscription_product',
    'get_antom_payment', 'get_antom_refund',
    'list_antom_subscription_prices', 'list_antom_subscription_products',
]


@pytest.fixture
def oauth_http(monkeypatch):
    calls = []
    replies = {'token': {'access_token': 'synthetic-access', 'refresh_token': 'synthetic-refresh',
                         'token_type': 'Bearer', 'expires_in': 3600}}

    def handler(request):
        calls.append(request)
        url = str(request.url)
        if url.endswith('oauth-protected-resource'):
            data = {'resource': client.SERVER_URL, 'authorization_servers': [client.ISSUER]}
        elif url.endswith('oauth-authorization-server'):
            data = METADATA
        elif url.endswith('/register'):
            data = {'client_id': 'synthetic-client'}
        else:
            data = replies['token']
        return httpx.Response(200, json=data)

    real = httpx.Client
    monkeypatch.setattr(oauth.httpx, 'Client', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return calls, replies


def test_sdk_registration_loads_provider_and_tools():
    registration = PluginRegistration(DifyPluginEnv())
    assert set(registration.tools_mapping['antom_mcp_tools'][2]) == {'discover_antom_queries', 'query_antom'}


def test_dcr_pkce_cross_instance_callback_and_refresh(oauth_http):
    calls, replies = oauth_http
    key = Fernet.generate_key().decode()
    settings = {'oauth_state_key': key}
    url = oauth.AntomProvider()._oauth_get_authorization_url(REDIRECT, settings)
    params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert params['redirect_uri'] == REDIRECT
    assert params['resource'] == client.SERVER_URL
    assert params['code_challenge_method'] == 'S256'
    pending = json.loads(Fernet(key.encode()).decrypt(params['state'].encode()))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(pending['code_verifier'].encode()).digest()).rstrip(b'=').decode()
    assert params['code_challenge'] == challenge
    assert pending['code_verifier'] not in url
    registration = json.loads(next(r for r in calls if str(r.url).endswith('/register')).content)
    assert registration['token_endpoint_auth_method'] == 'none'
    request = Request.from_values(query_string=urlencode({'state': params['state'], 'code': 'synthetic-code'}))
    credentials = oauth.AntomProvider()._oauth_get_credentials(REDIRECT, settings, request)
    assert credentials.credentials['client_id'] == 'synthetic-client'
    assert 'oauth_state_key' not in credentials.credentials
    assert credentials.expires_at > time.time()
    posted = parse_qs(calls[-1].content.decode())
    assert posted['code_verifier'] == [pending['code_verifier']]
    replies['token'] = {'access_token': 'synthetic-renewed', 'expires_in': 3600}
    refreshed = oauth.AntomProvider()._oauth_refresh_credentials(REDIRECT, settings, credentials.credentials)
    assert refreshed.credentials['refresh_token'] == 'synthetic-refresh'


@pytest.mark.parametrize('mode', ['changed-state', 'expired-state', 'other-workspace'])
def test_oauth_rejects_invalid_state_before_exchange(oauth_http, mode):
    calls, _ = oauth_http
    key = Fernet.generate_key()
    settings = {'oauth_state_key': key.decode()}
    pending = {'redirect_uri': REDIRECT, 'client_id': 'synthetic-client', 'code_verifier': 'synthetic-verifier'}
    state = Fernet(key).encrypt_at_time(json.dumps(pending).encode(), int(time.time()) - (601 if mode == 'expired-state' else 0)).decode()
    if mode == 'changed-state':
        state = 'invalid'
    request = Request.from_values(query_string=urlencode({'state': state, 'code': 'synthetic-code'}))
    with pytest.raises(ToolProviderOAuthError):
        oauth.AntomProvider()._oauth_get_credentials(REDIRECT + ('-other' if mode == 'other-workspace' else ''), settings, request)
    assert not calls


def test_oauth_denial_has_no_token_exchange(oauth_http):
    calls, _ = oauth_http
    with pytest.raises(ToolProviderOAuthError, match='not granted'):
        oauth.AntomProvider()._oauth_get_credentials(REDIRECT, {}, Request.from_values(query_string={'error': 'access_denied'}))
    assert not calls


@pytest.mark.parametrize('url', ['http://mcp.antom.com', 'https://mcp.antom.com.evil.test',
                                 'https://mcp.antom.com@127.0.0.1', 'https://mcp.antom.com:8443',
                                 'https://127.0.0.1', 'https://mcp.antom.com/#secret'])
def test_fixed_network_boundary(url):
    with pytest.raises(client.AntomError):
        client.require_origin(url, client.SERVER_URL)


@pytest.mark.parametrize('raw', ['[]', 'null', '{"amount":NaN}', 'not JSON', '{' + 'x' * 32768])
def test_invalid_json_is_rejected(raw):
    with pytest.raises(client.AntomError):
        client.parse_arguments(raw)


def test_external_schema_references_are_never_fetched():
    with pytest.raises(client.AntomError, match='External'):
        client.validate_arguments({'$ref': 'https://127.0.0.1/metadata'}, {})


@pytest.fixture
def mcp_http(monkeypatch):
    calls = []
    behavior = {'error': False}
    selected = 'antom_query_transaction_detail'
    schema = {'type': 'object', 'properties': {'reference': {'type': 'string'}},
              'required': ['reference'], 'additionalProperties': False}

    def handler(request):
        assert request.url.host == 'mcp.antom.com'
        if request.method != 'POST':
            return httpx2.Response(405)
        body = json.loads(request.content)
        method = body['method']
        calls.append(method)
        if 'id' not in body:
            return httpx2.Response(202)
        if method == 'initialize':
            result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},
                      'serverInfo': {'name': 'synthetic-antom', 'version': 'test'}}
        elif method == 'tools/list':
            result = {'tools': behavior.get('tools', [
                {'name': selected, 'inputSchema': schema, 'annotations': {'readOnlyHint': True}},
                {'name': 'create_payment', 'inputSchema': {'type': 'object'}},
            ])}
        elif method == 'tools/call':
            assert body['params']['name'] == behavior.get('expected_call_name', selected)
            if 'expected_arguments' in behavior:
                assert body['params']['arguments'] == behavior['expected_arguments']
            result = {'content': [{'type': 'text', 'text': 'upstream-private-error' if behavior['error'] else 'synthetic-result'}],
                      'isError': behavior['error']}
        else:
            raise AssertionError(method)
        return httpx2.Response(200, json={'jsonrpc': '2.0', 'id': body['id'], 'result': result})

    real = httpx2.AsyncClient
    monkeypatch.setattr(client.httpx2, 'AsyncClient', lambda **kw: real(transport=httpx2.MockTransport(handler), **kw))
    return calls, behavior


def test_actual_mcp_handshake_discovery_and_query(mcp_http):
    calls, _ = mcp_http
    listing = client.execute('synthetic-access')
    assert [t['name'] for t in listing['tools']] == ['antom_query_transaction_detail']
    assert listing['server_tool_count'] == 2
    assert listing['supported_tool_count'] == 1
    assert listing['excluded_tools'] == [{'name': 'create_payment', 'reason': 'unsupported_tool_name'}]
    result = client.execute('synthetic-access', 'antom_query_transaction_detail', {'reference': 'synthetic-reference'})
    assert result['content'][0]['text'] == 'synthetic-result'
    assert 'initialize' in calls and 'tools/call' in calls


def test_bad_arguments_prevent_mcp_tool_call(mcp_http):
    calls, _ = mcp_http
    with pytest.raises(client.AntomError, match='live tool schema'):
        client.execute('synthetic-access', 'antom_query_transaction_detail', {'invented': 'value'})
    assert 'tools/call' not in calls


@pytest.mark.parametrize('write_tool', ['create_payment', 'create_antom_subscription_price',
                                         'create_antom_subscription_product'])
def test_write_tool_blocked_before_network(mcp_http, write_tool):
    calls, _ = mcp_http
    with pytest.raises(client.AntomError, match='allowlist'):
        client.execute('synthetic-access', write_tool, {})
    assert not calls


def test_upstream_tool_errors_are_redacted(mcp_http):
    _, behavior = mcp_http
    behavior['error'] = True
    with pytest.raises(client.AntomError) as error:
        client.execute('synthetic-access', 'antom_query_transaction_detail', {'reference': 'synthetic-reference'})
    assert 'upstream-private-error' not in str(error.value)


def test_server_non_read_only_annotation_is_rejected():
    tool = Tool(name='antom_query_transaction_detail', input_schema={'type': 'object'},
                annotations=ToolAnnotations(read_only_hint=False))
    assert exclusion_reason(tool) == "server_marks_non_read_only"


@pytest.mark.parametrize('annotations', [None, {}, {'destructiveHint': False}])
def test_missing_read_only_hint_does_not_hide_allowlisted_query(annotations):
    tool = Tool.model_validate({'name': 'antom_query_transaction_detail',
                                'inputSchema': {'type': 'object'}, 'annotations': annotations})
    assert exclusion_reason(tool) is None


def test_discovery_distinguishes_server_empty_from_filtered_empty(mcp_http):
    calls, behavior = mcp_http
    behavior['tools'] = []
    empty = client.execute('synthetic-access')
    assert empty['status'] == 'no_server_tools'
    assert empty['server_tool_count'] == 0
    behavior['tools'] = [
        {'name': 'synthetic_subscription_lookup', 'inputSchema': {'type': 'object'}},
        {'name': 'antom_query_transaction_detail', 'inputSchema': {'type': 'object'},
         'annotations': {'readOnlyHint': False}},
    ]
    filtered = client.execute('synthetic-access')
    assert filtered['status'] == 'no_supported_tools'
    assert filtered['server_tool_count'] == 2
    assert filtered['supported_tool_count'] == 0
    assert filtered['excluded_tools'] == [
        {'name': 'synthetic_subscription_lookup', 'reason': 'unsupported_tool_name'},
        {'name': 'antom_query_transaction_detail', 'reason': 'server_marks_non_read_only'},
    ]
    assert 'input_schema' not in json.dumps(filtered['excluded_tools'])
    assert 'tools/call' not in calls


def test_discover_tool_renders_summary_without_requiring_trace(monkeypatch):
    import tools.discover as discovery
    payload = {'tools': [], 'summary': 'Synthetic discovery explanation'}
    monkeypatch.setattr(discovery, 'execute', lambda token: payload)
    tool = discovery.DiscoverAntomTools.from_credentials({'access_token': 'synthetic-access'})
    messages = list(tool._invoke({}))
    assert [m.type.value for m in messages] == ['json', 'text']
    assert messages[1].message.text == payload['summary']


def test_discovery_with_reported_tool_names(mcp_http):
    calls, behavior = mcp_http
    supported = {wire for _, wire in QUERY_SPELLINGS}
    behavior['tools'] = [{'name': name, 'inputSchema': {'type': 'object'}}
                         for name in sorted(supported | set(OTHER_REPORTED_NAMES))]
    listing = client.execute('synthetic-access')
    assert listing['status'] == 'ready'
    assert listing['server_tool_count'] == 12
    assert listing['supported_tool_count'] == 6
    assert {t['name'] for t in listing['tools']} == supported
    assert {t['name']: t['reason'] for t in listing['excluded_tools']} == {
        name: 'unsupported_tool_name' for name in OTHER_REPORTED_NAMES}
    assert 'tools/call' not in calls


@pytest.mark.parametrize('documented,wire', QUERY_SPELLINGS)
def test_documented_query_calls_only_advertised_wire_alias(mcp_http, documented, wire):
    _, behavior = mcp_http
    behavior['tools'] = [{'name': wire, 'inputSchema': {'type': 'object'}}]
    behavior['expected_call_name'] = wire
    result = client.execute('synthetic-access', documented, {})
    assert result['content'][0]['text'] == 'synthetic-result'


def test_wire_query_uses_live_schema_for_validation_and_dispatch(mcp_http):
    calls, behavior = mcp_http
    wire = 'get_antom_transaction_details'
    schema = {'type': 'object', 'required': ['test_reference'],
              'properties': {'test_reference': {'type': 'string'}},
              'additionalProperties': False}
    arguments = {'test_reference': 'synthetic-value'}
    behavior['tools'] = [{'name': wire, 'inputSchema': schema}]
    behavior['expected_call_name'] = wire
    behavior['expected_arguments'] = arguments
    with pytest.raises(client.AntomError, match='live tool schema'):
        client.execute('synthetic-access', wire, {'invented': 'invalid'})
    assert 'tools/call' not in calls
    result = client.execute('synthetic-access', wire, arguments)
    assert result['content'][0]['text'] == 'synthetic-result'


def test_non_read_only_wire_alias_cannot_be_invoked_by_either_spelling(mcp_http):
    calls, behavior = mcp_http
    documented, wire = QUERY_SPELLINGS[0]
    behavior['tools'] = [{'name': wire, 'inputSchema': {'type': 'object'},
                          'annotations': {'readOnlyHint': False}}]
    listing = client.execute('synthetic-access')
    assert listing['tools'] == []
    assert listing['excluded_tools'] == [{'name': wire, 'reason': 'server_marks_non_read_only'}]
    for name in [documented, wire]:
        with pytest.raises(client.AntomError):
            client.execute('synthetic-access', name, {})
    assert 'tools/call' not in calls


def test_exact_advertised_query_is_preferred_and_its_read_only_flag_is_respected(mcp_http):
    calls, behavior = mcp_http
    documented, wire = QUERY_SPELLINGS[1]
    behavior['tools'] = [{'name': name, 'inputSchema': {'type': 'object'}} for name in [wire, documented]]
    behavior['expected_call_name'] = documented
    client.execute('synthetic-access', documented, {})
    assert calls.count('tools/call') == 1
    behavior['tools'][1]['annotations'] = {'readOnlyHint': False}
    with pytest.raises(client.AntomError):
        client.execute('synthetic-access', documented, {})
    assert calls.count('tools/call') == 1
