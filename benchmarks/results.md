# CoDoc Evaluation Benchmark Summary

| Metric | Value |
| :--- | :--- |
| Total Cases | 18 |
| Positive (Stale) Cases | 12 |
| Negative (Control) Cases | 6 |
| True Positives (TP) | 12 |
| False Positives (FP) | 0 |
| False Negatives (FN) | 0 |
| **Precision** | **100.0%** |
| **Recall** | **100.0%** |
| **F1 Score** | **100.0%** |
| **Exact Match Accuracy** | **100.0%** |
| False Positive Rate | 0.0% |
| Repair Success Rate | 100.0% |
| Avg Latency | 0.00s |
| Total Tokens | 0 (prompt: 0, completion: 0) |

### Per-Case Results

| Case ID | Category | Expected | Predicted | TP | FP | FN | P | R | F1 | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `fastapi_param_rename_oauth2` | param_rename | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_default_change_status_code` | default_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_endpoint_route_prefix_change` | endpoint_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_cli_option_removal_proxy_headers` | cli_option_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_schema_response_model_type_change` | schema_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_dep_injection_signature_rename` | param_rename | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `pydantic_field_type_migration_strict_int` | schema_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `pydantic_config_dict_key_removal_allow_mutation` | schema_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `pydantic_validator_mode_change_after_to_before` | behavioral_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `pydantic_root_validator_deprecated_wrap_mode` | behavioral_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_websocket_endpoint_rename_route` | endpoint_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_header_param_default_alias_required` | default_change | 1 | 1 | 1 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_internal_variable_rename_helper` | benign_refactor | 0 | 0 | 0 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_whitespace_and_comments_reformat` | benign_refactor | 0 | 0 | 0 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `pydantic_test_suite_addition_edge_cases` | benign_refactor | 0 | 0 | 0 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `pydantic_already_updated_docs_in_pr` | already_updated_docs | 0 | 0 | 0 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_private_helper_extraction` | benign_refactor | 0 | 0 | 0 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |
| `fastapi_already_updated_docs_in_pr` | already_updated_docs | 0 | 0 | 0 | 0 | 0 | 100% | 100% | 100% | ✅ PASS |