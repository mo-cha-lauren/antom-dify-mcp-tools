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
from werkzeug import Request

import antom_client as client
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

# The names came from discovery. All tool definitions and arguments below are synthetic.
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
            if 'pages' in behavior:
                cursor = body.get('params', {}).get('cursor')
                result = behavior['pages'][cursor]
            else:
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
    assert [t['name'] for t in listing['tools']] == ['antom_query_transaction_detail', 'create_payment']
    assert listing['server_tool_count'] == 2
    assert listing['supported_tool_count'] == 2
    assert listing['excluded_tools'] == []
    result = client.execute('synthetic-access', 'antom_query_transaction_detail', {'reference': 'synthetic-reference'})
    assert result['content'][0]['text'] == 'synthetic-result'
    assert 'initialize' in calls and 'tools/call' in calls


def test_bad_arguments_prevent_mcp_tool_call(mcp_http):
    calls, _ = mcp_http
    with pytest.raises(client.AntomError, match='live tool schema'):
        client.execute('synthetic-access', 'antom_query_transaction_detail', {'invented': 'value'})
    assert 'tools/call' not in calls


@pytest.mark.parametrize('name', [
    'create_antom_subscription_price', 'create_antom_subscription_product', 'future_antom_action',
])
def test_advertised_write_tool_is_discoverable_and_callable(mcp_http, name):
    calls, behavior = mcp_http
    schema = {'type': 'object', 'properties': {'note': {'type': 'string'}},
              'required': ['note'], 'additionalProperties': False}
    hints = {'readOnlyHint': False, 'destructiveHint': True}
    behavior['tools'] = [{'name': name, 'inputSchema': schema, 'annotations': hints}]
    behavior['expected_call_name'] = name
    behavior['expected_arguments'] = {'note': 'synthetic'}
    listing = client.execute('synthetic-access')
    item, = listing['tools']
    assert item['name'] == name
    assert item['input_schema'] == schema
    assert item['annotations']['readOnlyHint'] is False
    assert item['annotations']['destructiveHint'] is True
    assert 'tools/call' not in calls
    with pytest.raises(client.AntomError, match='live tool schema'):
        client.execute('synthetic-access', name, {'invented': True})
    assert 'tools/call' not in calls
    result = client.execute('synthetic-access', name, {'note': 'synthetic'})
    assert result['content'][0]['text'] == 'synthetic-result'
    assert calls.count('tools/call') == 1


def test_tool_removed_after_discovery_is_not_dispatched(mcp_http):
    calls, behavior = mcp_http
    name = 'temporary_account_tool'
    behavior['tools'] = [{'name': name, 'inputSchema': {'type': 'object'}}]
    assert [t['name'] for t in client.execute('synthetic-access')['tools']] == [name]
    behavior['tools'] = []
    with pytest.raises(client.AntomError):
        client.execute('synthetic-access', name, {})
    assert calls.count('tools/list') == 2
    assert 'tools/call' not in calls


def test_upstream_tool_errors_are_redacted(mcp_http):
    calls, behavior = mcp_http
    behavior['error'] = True
    with pytest.raises(client.AntomError) as error:
        client.execute('synthetic-access', 'antom_query_transaction_detail', {'reference': 'synthetic-reference'})
    assert 'upstream-private-error' not in str(error.value)
    assert calls.count('tools/call') == 1


def test_empty_server_remains_empty(mcp_http):
    calls, behavior = mcp_http
    behavior['tools'] = []
    empty = client.execute('synthetic-access')
    assert empty['status'] == 'no_server_tools'
    assert empty['server_tool_count'] == 0
    assert empty['tools'] == []
    assert empty['supported_tool_count'] == 0
    assert empty['excluded_tools'] == []
    assert 'tools/call' not in calls


def test_discover_tool_renders_summary_without_requiring_trace(monkeypatch):
    import tools.discover as discovery
    payload = {'tools': [], 'summary': 'Synthetic discovery explanation'}
    monkeypatch.setattr(discovery, 'execute', lambda token: payload)
    tool = discovery.DiscoverAntomTools.from_credentials({'access_token': 'synthetic-access'})
    messages = list(tool._invoke({}))
    assert [m.type.value for m in messages] == ['json', 'text']
    assert messages[1].message.text == payload['summary']


@pytest.mark.parametrize('parameters', [{}, {'tool_name': None}, {'tool_name': ''}, {'tool_name': ' '}])
def test_call_action_requires_a_tool_name_without_running_discovery(mcp_http, parameters):
    from tools.query import QueryAntom

    calls, _ = mcp_http
    tool = QueryAntom.from_credentials({'access_token': 'synthetic-access'})
    with pytest.raises(client.AntomError):
        list(tool._invoke(parameters))
    assert not calls


def test_discovery_with_reported_tool_names(mcp_http):
    calls, behavior = mcp_http
    reported = {wire for _, wire in QUERY_SPELLINGS} | set(OTHER_REPORTED_NAMES)
    behavior['tools'] = [{'name': name, 'inputSchema': {'type': 'object'}}
                         for name in sorted(reported)]
    listing = client.execute('synthetic-access')
    assert listing['status'] == 'ready'
    assert listing['server_tool_count'] == 12
    assert listing['supported_tool_count'] == 12
    assert {t['name'] for t in listing['tools']} == reported
    assert listing['excluded_tools'] == []
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


def test_exact_advertised_name_takes_precedence_over_a_legacy_alias(mcp_http):
    calls, behavior = mcp_http
    documented, wire = QUERY_SPELLINGS[1]
    behavior['tools'] = [{'name': wire, 'inputSchema': {'type': 'object'}},
                         {'name': documented, 'inputSchema': {'type': 'object'},
                          'annotations': {'readOnlyHint': False}}]
    behavior['expected_call_name'] = documented
    client.execute('synthetic-access', documented, {})
    assert calls.count('tools/call') == 1


def test_discovery_and_call_can_reach_beyond_twenty_pages(mcp_http):
    calls, behavior = mcp_http
    names = [f'remote_tool_{i}' for i in range(22)]
    pages = {}
    for i, name in enumerate(names):
        cursor = str(i) if i else None
        pages[cursor] = {'tools': [{'name': name, 'inputSchema': {'type': 'object'}}]}
        if i < len(names) - 1:
            pages[cursor]['nextCursor'] = str(i + 1)
    behavior['pages'] = pages
    behavior['expected_call_name'] = names[-1]
    assert [t['name'] for t in client.execute('synthetic-access')['tools']] == names
    client.execute('synthetic-access', names[-1], {})
    assert calls.count('tools/list') == 2 * len(names)
    assert calls.count('tools/call') == 1


def test_repeated_pagination_cursor_fails_without_dispatch(mcp_http):
    calls, behavior = mcp_http
    behavior['pages'] = {None: {'tools': [], 'nextCursor': 'cycle'},
                         'cycle': {'tools': [], 'nextCursor': 'cycle'}}
    with pytest.raises(client.AntomError):
        client.execute('synthetic-access', 'some_tool', {})
    assert calls.count('tools/list') == 2
    assert 'tools/call' not in calls
