# citegraph Level-1 eval

50 questions. Tool latency p50 0.5 ms, p95 4.3 ms.

## Accuracy

| tool | questions | citegraph F1 | grep F1 | citegraph P | citegraph R |
|---|---|---|---|---|---|
| find_config_key | 5 | 0.60 | 0.86 | 1.00 | 0.46 |
| find_path | 5 | 0.60 | n/a | 0.60 | 0.60 |
| what_calls | 20 | 0.73 | 0.69 | 0.80 | 0.70 |
| what_does_it_call | 20 | 0.84 | 0.58 | 0.88 | 0.84 |

## Calibration

| rule | nominal confidence | observed precision | edges |
|---|---|---|---|
| same_file | 0.95 | 1.00 | 26 |
| import_scope | 0.90 | 1.00 | 17 |
| repo_unique | 0.70 | 0.60 | 5 |
| ambiguous | 0.15 | 0.17 | 82 |

![f1_by_tool.svg](f1_by_tool.svg)

![calibration.svg](calibration.svg)

## Misses

- `py-what_calls-flask-3`: missing ['flask:tests.test_blueprints.test_route_decorator_custom_endpoint_with_dots'], extra []

- `py-what_calls-flask-4`: missing ['flask:tests.test_json_tag.test_custom_tag', 'flask:tests.test_json_tag.test_duplicate_tag', 'flask:tests.test_json_tag.test_tag_order'], extra []

- `py-what_calls-flask-6`: missing ['flask:flask.json.tag.TaggedJSONSerializer.tag'], extra []

- `py-what_calls-flask-8`: missing ['flask:flask.json.dumps', 'flask:flask.testing.EnvironBuilder.json_dumps', 'flask:tests.test_basic.test_json_dump_dataclass', 'flask:tests.test_json.test_json_as_unicode'], extra []

- `py-what_calls-flask-10`: missing ['flask:tests.test_basic.test_make_response_with_response_instance', 'flask:tests.test_basic.test_max_cookie_size', 'flask:tests.test_basic.test_max_cookie_size.index', 'flask:tests.test_basic.test_response_types.from_response_headers', 'flask:tests.test_basic.test_session_vary_cookie.vary_cookie_header_set', 'flask:tests.test_basic.test_session_vary_cookie.vary_header_set', 'flask:tests.test_helpers.TestStreaming.test_async_view.index', 'flask:tests.test_helpers.TestStreaming.test_stream_keeps_session.index', 'flask:tests.test_helpers.TestStreaming.test_streaming_with_context.index', 'flask:tests.test_helpers.TestStreaming.test_streaming_with_context_and_custom_close.index', 'flask:tests.test_helpers.TestStreaming.test_streaming_with_context_as_decorator.index', 'flask:tests.test_views.test_explicit_head.Index.head', 'flask:tests.test_views.test_implicit_head.Index.get', 'flask:tests.type_check.typing_app_decorators.after_async', 'flask:tests.type_check.typing_app_decorators.after_sync'], extra []

- `py-what_calls-httpx-1`: missing ['httpx:httpx._client.Client.close'], extra []

- `py-what_calls-httpx-8`: missing ['httpx:tests.client.test_client.test_patch'], extra []

- `py-what_does_it_call-flask-2`: missing ['flask:flask.sansio.scaffold.Scaffold._check_setup_finished'], extra []

- `py-what_does_it_call-flask-4`: missing ['flask:flask.app.Flask.send_static_file', 'flask:flask.sansio.app.App.__init__', 'flask:flask.sansio.app.App.add_url_rule'], extra []

- `py-what_does_it_call-flask-6`: missing [], extra ['flask:flask.templating.DispatchingJinjaLoader.list_templates']

- `py-what_does_it_call-flask-9`: missing ['flask:flask.sansio.scaffold.Scaffold.add_url_rule'], extra []

- `py-what_does_it_call-httpx-3`: missing ['httpx:httpx._urls.QueryParams.add'], extra []

- `py-find_config_key-flask-1`: missing ['flask:src/flask/app.py:627', 'flask:tests/conftest.py:21', 'flask:tests/test_helpers.py:345'], extra []

- `py-find_config_key-flask-2`: missing ['flask:tests/conftest.py:22'], extra []

- `py-find_config_key-flask-3`: missing ['flask:tests/test_cli.py:579'], extra []

- `py-find_config_key-httpx-1`: missing ['httpx:tests/conftest.py:24'], extra []

- `py-find_config_key-flask-4`: missing ['flask:examples/tutorial/flaskr/__init__.py:11', 'flask:src/flask/app.py:183', 'flask:src/flask/sansio/app.py:216', 'flask:tests/conftest.py:49', 'flask:tests/static/config.toml:2', 'flask:tests/test_config.py:10', 'flask:tests/test_config.py:112', 'flask:tests/test_config.py:116', 'flask:tests/test_config.py:120', 'flask:tests/test_config.py:124', 'flask:tests/test_config.py:137'], extra []

- `py-find_path-flask-1`: missing ['flask:flask.app.Flask.__init__', 'flask:flask.helpers.get_debug_flag', 'flask:flask.sansio.app.App.__init__', 'flask:flask.sansio.app.App.make_config'], extra []

- `py-find_path-httpx-1`: missing ['httpx:httpx._api.options', 'httpx:httpx._api.request', 'httpx:httpx._client.BaseClient._merge_url', 'httpx:httpx._client.BaseClient.build_request', 'httpx:httpx._client.Client.request'], extra []
